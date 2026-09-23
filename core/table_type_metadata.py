"""Pure CSV type metadata codec; never stores formulas or cell values.

CSV still owns the values. Text and nullable object-integer columns need
protection from pandas inference; compact masks distinguish cell types.
"""

from __future__ import annotations

import base64
import binascii
from io import BytesIO

import numpy as np
import pandas as pd


def _encode_mask(mask: pd.Series) -> str:
    values = mask.to_numpy(dtype=bool)
    return base64.b64encode(np.packbits(values)).decode("ascii") if values.any() else ""


def _decode_mask(encoded: object, rows: int) -> np.ndarray:
    if not isinstance(encoded, str):
        raise ValueError("invalid type bitmap")
    if encoded == "":
        return np.zeros(rows, dtype=bool)
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("invalid type bitmap") from error
    if len(payload) != (rows + 7) // 8:
        raise ValueError("type bitmap length does not match rows")
    if rows % 8 and payload[-1] & ((1 << (8 - rows % 8)) - 1):
        raise ValueError("type bitmap has nonzero padding")
    return np.unpackbits(np.frombuffer(payload, dtype=np.uint8), count=rows).astype(bool)


def describe_table_types(frame: pd.DataFrame) -> dict:
    """Describe inference-sensitive cells; reject unsupported mixed cells.

    No I/O or mutation. Other columns retain the existing CSV inference.
    """

    columns = list(frame.columns)
    if any(not isinstance(name, str) or not name for name in columns) or len(set(columns)) != len(columns):
        raise ValueError("table type metadata requires unique nonempty text column names")
    preserved = {}
    for name in columns:
        values = frame[name]
        if isinstance(values.dtype, pd.CategoricalDtype) or (
            pd.api.types.is_integer_dtype(values.dtype) and values.isna().any()
        ):
            values = values.astype(object)
        string_dtype = isinstance(values.dtype, pd.StringDtype)
        inferred = pd.api.types.infer_dtype(values, skipna=True)
        if not string_dtype and not pd.api.types.is_object_dtype(values.dtype):
            continue
        if string_dtype or inferred in {"string", "unicode", "empty"}:
            preserved[name] = {
                "kind": "text", "dtype": "string" if string_dtype else "object",
                "nulls": _encode_mask(values.isna()),
            }
        elif inferred in {"mixed", "mixed-integer"} or (
            inferred == "integer" and values.isna().any()
        ):
            texts = values.map(lambda value: isinstance(value, str))
            if not texts.any() and inferred != "integer":
                continue
            nulls = values.isna()
            booleans = values.map(lambda value: isinstance(value, (bool, np.bool_)))
            numbers = ~(texts | nulls | booleans)
            if not values.loc[numbers].map(
                lambda value: isinstance(value, (int, float, np.integer, np.floating)),
            ).all():
                raise ValueError(f"unsupported cell type in mixed text column: {name}")
            preserved[name] = {
                "kind": "mixed", "dtype": "object", "nulls": _encode_mask(nulls),
                "texts": _encode_mask(texts), "booleans": _encode_mask(booleans),
            }
    return {"row_count": len(frame), "columns": columns, "preserved": preserved}


def read_typed_csv(payload: bytes, schema: dict | None) -> pd.DataFrame:
    """Decode CSV bytes using a matching schema, or legacy inference if absent.

    Raises ValueError for malformed metadata/shape; never silently drops types.
    """

    converters = {"easyqcid": lambda value: value}
    if schema is None:
        return pd.read_csv(BytesIO(payload), encoding="utf-8", converters=converters)
    if not isinstance(schema, dict) or set(schema) != {"row_count", "columns", "preserved"}:
        raise ValueError("invalid table type schema")
    rows, columns, preserved = schema["row_count"], schema["columns"], schema["preserved"]
    if type(rows) is not int or rows < 0:
        raise ValueError("invalid table type row count")
    if (not isinstance(columns, list)
            or any(not isinstance(name, str) or not name for name in columns)
            or len(set(columns)) != len(columns)
            or not isinstance(preserved, dict)
            or not set(preserved).issubset(columns)):
        raise ValueError("invalid table type columns")
    converters.update({name: lambda value: value for name in preserved})
    frame = pd.read_csv(BytesIO(payload), encoding="utf-8", converters=converters)
    if len(frame) != rows or list(frame.columns) != columns:
        raise ValueError("table type schema does not match CSV shape")
    for name, spec in preserved.items():
        if not isinstance(spec, dict) or spec.get("kind") not in {"text", "mixed"}:
            raise ValueError(f"invalid column type: {name}")
        fields = {"kind", "dtype", "nulls"}
        if spec["kind"] == "mixed":
            fields |= {"texts", "booleans"}
        if set(spec) != fields or spec["dtype"] not in {"object", "string"}:
            raise ValueError(f"invalid column type fields: {name}")
        nulls = _decode_mask(spec["nulls"], rows)
        values = frame[name].astype(object)
        if not values.loc[nulls].eq("").all():
            raise ValueError("type bitmap marks nonempty CSV cells as missing")
        if spec["kind"] == "mixed":
            if spec["dtype"] != "object":
                raise ValueError("mixed column requires object dtype")
            texts = _decode_mask(spec["texts"], rows)
            booleans = _decode_mask(spec["booleans"], rows)
            if (texts & nulls | texts & booleans | nulls & booleans).any():
                raise ValueError("overlapping column type bitmaps")
            boolean_values = values.loc[booleans]
            if not boolean_values.isin(["True", "False"]).all():
                raise ValueError("invalid boolean cells in mixed column")
            values.loc[booleans] = boolean_values.eq("True").to_numpy(dtype=object)
            numbers = ~(texts | booleans | nulls)
            numeric_cells = values.loc[numbers]
            # Do not round large integer cells through float when the same
            # object column also contains fractional values.
            integers = numeric_cells.str.fullmatch(r"[+-]?[0-9]+", na=False)
            integer_cells = numeric_cells.loc[integers]
            fractional_cells = numeric_cells.loc[~integers]
            values.loc[integer_cells.index] = integer_cells.map(int).to_numpy(dtype=object)
            values.loc[fractional_cells.index] = pd.to_numeric(
                fractional_cells, errors="raise",
            ).to_numpy(dtype=object)
        values.loc[nulls] = pd.NA
        if (spec["kind"] == "mixed" and not texts.any()
                and not booleans.any() and integers.all()):
            # Numeric-only masks reuse the existing mixed schema. Restore a
            # nullable integer dtype, retaining numeric filters without float
            # rounding; larger Python integers remain exact object values.
            numeric_values = values.loc[~nulls]
            if not numeric_values.empty:
                smallest, largest = min(numeric_values), max(numeric_values)
                if -(2**63) <= smallest and largest < 2**63:
                    frame[name] = values.astype("Int64")
                    continue
                if 0 <= smallest and largest < 2**64:
                    frame[name] = values.astype("UInt64")
                    continue
        frame[name] = values.astype(spec["dtype"])
    return frame
