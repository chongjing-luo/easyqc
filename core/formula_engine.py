"""Vectorized evaluator for closed EasyQC Formula AST nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pandas as pd

from core.formula_parser import (
    FormulaError,
    FormulaNode,
    FormulaParser,
    FormulaValidationError,
    ParsedFormula,
)
from models.formula_function import (
    FORMULA_FUNCTION_ARITY,
    FORMULA_FUNCTION_CATALOG,
    FormulaFunctionSpec,
)


class FormulaEvaluationError(FormulaError):
    """Raised when strict materialization sees unresolved row errors."""


@dataclass(frozen=True, slots=True)
class FormulaEvaluation:
    """One row-aligned formula value plus explicit per-row errors."""

    values: pd.Series
    errors: pd.Series

    def __post_init__(self) -> None:
        if not isinstance(self.values, pd.Series):
            raise TypeError("公式结果必须是 Series")
        if not isinstance(self.errors, pd.Series):
            raise TypeError("公式错误必须是 Series")
        if not self.values.index.equals(self.errors.index):
            raise ValueError("公式结果与错误索引不一致")

    @property
    def has_errors(self) -> bool:
        return bool(self.errors.notna().any())

    def raise_for_errors(self, *, max_examples: int = 3) -> None:
        """Raise one bounded summary when unresolved row errors remain."""

        if type(max_examples) is not int or not 1 <= max_examples <= 10:
            raise ValueError("错误示例数量必须是 1 到 10 的整数")
        unresolved = self.errors.dropna()
        if unresolved.empty:
            return
        examples = "; ".join(
            f"索引 {index}: {message}"
            for index, message in unresolved.iloc[:max_examples].items()
        )
        raise FormulaEvaluationError(
            f"公式有 {len(unresolved)} 行无法处理（{examples}）"
        )


def _blank_errors(index: pd.Index) -> pd.Series:
    return pd.Series(pd.NA, index=index, dtype="string")


def _broadcast(value: object, index: pd.Index) -> pd.Series:
    return pd.Series([value] * len(index), index=index)


def _combine_errors(*error_series: pd.Series) -> pd.Series:
    """Keep the first row error while preserving the common index."""

    if not error_series:
        raise ValueError("至少需要一个错误序列")
    combined = _blank_errors(error_series[0].index)
    for errors in error_series:
        combined = combined.combine_first(errors.astype("string"))
    return combined


def _text_value_mask(values: pd.Series) -> pd.Series:
    """Identify actual text cells without a Python callback per row."""

    if isinstance(values.dtype, pd.StringDtype):
        return values.notna()
    if not pd.api.types.is_object_dtype(values.dtype):
        return pd.Series(False, index=values.index, dtype=bool)
    try:
        return values.str.startswith("", na=False).astype(bool)
    except (AttributeError, TypeError):
        return pd.Series(False, index=values.index, dtype=bool)


def _numeric_operand(
    operand: FormulaEvaluation,
    *,
    error_message: str,
    allow_text: bool = False,
) -> tuple[pd.Series, pd.Series]:
    """Return nullable numbers plus row errors for invalid values."""

    values = operand.values
    text_values = _text_value_mask(values)
    rejected_type = pd.Series(False, index=values.index, dtype=bool)
    if pd.api.types.is_bool_dtype(values.dtype) or pd.api.types.is_datetime64_any_dtype(
        values.dtype
    ):
        rejected_type = values.notna()

    numeric = pd.to_numeric(values, errors="coerce")
    numeric = pd.Series(numeric, index=values.index, dtype="Float64")
    invalid = values.notna() & numeric.isna()
    if not allow_text:
        invalid |= text_values
    invalid |= rejected_type

    errors = operand.errors.copy()
    errors = errors.mask(invalid & errors.isna(), error_message)
    numeric = numeric.mask(errors.notna(), pd.NA)
    return numeric, errors


def _boolean_operand(
    operand: FormulaEvaluation,
    *,
    error_message: str,
) -> tuple[pd.Series, pd.Series]:
    """Return nullable booleans with deterministic whole-column typing."""

    values = operand.values
    inferred = pd.api.types.infer_dtype(values, skipna=True)
    is_boolean = pd.api.types.is_bool_dtype(values.dtype) or inferred == "boolean"
    if is_boolean:
        boolean_values = values.astype("boolean")
        invalid = values.isna()
    else:
        boolean_values = pd.Series(
            pd.NA,
            index=values.index,
            dtype="boolean",
        )
        invalid = pd.Series(True, index=values.index, dtype=bool)

    errors = operand.errors.copy()
    errors = errors.mask(invalid & errors.isna(), error_message)
    boolean_values = boolean_values.mask(errors.notna(), pd.NA)
    return boolean_values, errors


def _string_operand(operand: FormulaEvaluation) -> tuple[pd.Series, pd.Series]:
    """Return nullable deterministic text while preserving prior row errors."""

    errors = operand.errors.copy()
    values = operand.values.astype("string").mask(errors.notna(), pd.NA)
    return values, errors


class FormulaEngine:
    """Evaluate whitelisted functions through row-aligned pandas operations."""

    _ARITY: Final = FORMULA_FUNCTION_ARITY

    def __init__(self, parser: FormulaParser | None = None) -> None:
        self.parser = parser or FormulaParser()

    @staticmethod
    def function_catalog() -> tuple[FormulaFunctionSpec, ...]:
        """Return the immutable metadata shared by parser, Core and GUI."""

        return FORMULA_FUNCTION_CATALOG

    def evaluate(
        self,
        frame: pd.DataFrame,
        formula: str | ParsedFormula,
    ) -> FormulaEvaluation:
        """Evaluate one formula without mutating its source DataFrame."""

        if not isinstance(frame, pd.DataFrame):
            raise TypeError("公式来源必须是 DataFrame")
        parsed = (
            self.parser.parse(formula)
            if isinstance(formula, str)
            else formula
        )
        if not isinstance(parsed, ParsedFormula):
            raise TypeError("公式必须是文本或 ParsedFormula")
        missing = [
            column
            for column in parsed.referenced_columns
            if column not in frame.columns
        ]
        if missing:
            raise FormulaValidationError(f"未知列: {missing[0]}")
        return self._evaluate_node(parsed.root, frame)

    def _evaluate_node(
        self,
        node: FormulaNode,
        frame: pd.DataFrame,
    ) -> FormulaEvaluation:
        if node.kind == "literal":
            return FormulaEvaluation(
                _broadcast(node.value, frame.index),
                _blank_errors(frame.index),
            )
        if node.kind == "column":
            return FormulaEvaluation(
                frame[str(node.value)].copy(),
                _blank_errors(frame.index),
            )
        if node.kind == "unary":
            return self._evaluate_unary(node, frame)
        if node.kind == "binary":
            return self._evaluate_binary(node, frame)
        if node.kind == "call":
            return self._evaluate_call(node, frame)
        raise FormulaValidationError(f"不支持的公式节点: {node.kind}")

    def _evaluate_unary(
        self,
        node: FormulaNode,
        frame: pd.DataFrame,
    ) -> FormulaEvaluation:
        operand = self._evaluate_node(node.children[0], frame)
        operator = str(node.value)
        if operator == "NOT":
            values, errors = _boolean_operand(
                operand,
                error_message="NOT 需要布尔值",
            )
            return FormulaEvaluation((~values).mask(errors.notna(), pd.NA), errors)
        if operator in {"+", "-"}:
            values, errors = _numeric_operand(
                operand,
                error_message=f"{operator} 需要数值",
            )
            result = values if operator == "+" else -values
            return FormulaEvaluation(result.mask(errors.notna(), pd.NA), errors)
        raise FormulaValidationError(f"不支持的一元运算符: {operator}")

    def _evaluate_binary(
        self,
        node: FormulaNode,
        frame: pd.DataFrame,
    ) -> FormulaEvaluation:
        left = self._evaluate_node(node.children[0], frame)
        right = self._evaluate_node(node.children[1], frame)
        operator = str(node.value)
        if operator in {"+", "-", "*", "/"}:
            return self._evaluate_arithmetic(operator, left, right)
        if operator in {"=", "<>", "<", "<=", ">", ">="}:
            return self._evaluate_comparison(operator, left, right)
        if operator in {"AND", "OR"}:
            return self._evaluate_logical(operator, left, right)
        errors = _combine_errors(left.errors, right.errors)
        if operator == "&":
            values = (
                left.values.astype("string").fillna("")
                + right.values.astype("string").fillna("")
            )
        else:
            raise FormulaValidationError(f"不支持的运算符: {operator}")
        return FormulaEvaluation(values.mask(errors.notna(), pd.NA), errors)

    @staticmethod
    def _evaluate_arithmetic(
        operator: str,
        left: FormulaEvaluation,
        right: FormulaEvaluation,
    ) -> FormulaEvaluation:
        left_values, left_errors = _numeric_operand(
            left,
            error_message=f"{operator} 需要数值",
        )
        right_values, right_errors = _numeric_operand(
            right,
            error_message=f"{operator} 需要数值",
        )
        errors = _combine_errors(left_errors, right_errors)

        if operator == "+":
            values = left_values + right_values
        elif operator == "-":
            values = left_values - right_values
        elif operator == "*":
            values = left_values * right_values
        else:
            zero_divisor = right_values.eq(0).fillna(False)
            errors = errors.mask(
                zero_divisor & errors.isna(),
                "除数不能为零",
            )
            safe_divisor = right_values.mask(zero_divisor, pd.NA)
            values = left_values / safe_divisor
        return FormulaEvaluation(values.mask(errors.notna(), pd.NA), errors)

    @staticmethod
    def _evaluate_comparison(
        operator: str,
        left: FormulaEvaluation,
        right: FormulaEvaluation,
    ) -> FormulaEvaluation:
        errors = _combine_errors(left.errors, right.errors)
        usable = errors.isna()
        left_blank = left.values.isna()
        right_blank = right.values.isna()
        both_present = usable & ~left_blank & ~right_blank
        values = pd.Series(False, index=left.values.index, dtype=bool)

        if operator in {"=", "<>"}:
            equal = pd.Series(False, index=left.values.index, dtype=bool)
            equal.loc[usable & left_blank & right_blank] = True
            try:
                compared = left.values.loc[both_present].eq(
                    right.values.loc[both_present]
                )
                equal.loc[both_present] = compared.fillna(False).astype(bool)
            except (TypeError, ValueError):
                errors = errors.mask(
                    both_present & errors.isna(),
                    f"{operator} 无法比较",
                )
            values = equal if operator == "=" else (~equal & usable)
        else:
            method = {
                "<": "lt",
                "<=": "le",
                ">": "gt",
                ">=": "ge",
            }[operator]
            try:
                compared = getattr(
                    left.values.loc[both_present],
                    method,
                )(right.values.loc[both_present])
                values.loc[both_present] = compared.fillna(False).astype(bool)
            except (TypeError, ValueError):
                errors = errors.mask(
                    both_present & errors.isna(),
                    f"{operator} 无法比较",
                )
        return FormulaEvaluation(values.mask(errors.notna(), False), errors)

    @staticmethod
    def _evaluate_logical(
        operator: str,
        left: FormulaEvaluation,
        right: FormulaEvaluation,
    ) -> FormulaEvaluation:
        left_values, left_errors = _boolean_operand(
            left,
            error_message=f"{operator} 需要布尔值",
        )
        right_values, right_errors = _boolean_operand(
            right,
            error_message=f"{operator} 需要布尔值",
        )
        errors = _combine_errors(left_errors, right_errors)
        if operator == "AND":
            values = left_values & right_values
        else:
            values = left_values | right_values
        return FormulaEvaluation(values.mask(errors.notna(), pd.NA), errors)

    def _evaluate_call(
        self,
        node: FormulaNode,
        frame: pd.DataFrame,
    ) -> FormulaEvaluation:
        name = str(node.value)
        arity = self._ARITY.get(name)
        if arity is None:
            raise FormulaValidationError(f"不支持的函数: {name}")
        if not arity[0] <= len(node.children) <= arity[1]:
            expected = (
                str(arity[0])
                if arity[0] == arity[1]
                else f"{arity[0]} 到 {arity[1]}"
            )
            raise FormulaValidationError(
                f"函数 {name} 参数数量必须是 {expected}"
            )
        args = [self._evaluate_node(child, frame) for child in node.children]
        if name == "IF":
            return self._if(args)
        if name == "IFERROR":
            return self._iferror(args)
        if name == "ISBLANK":
            values = args[0].values.isna()
            return FormulaEvaluation(
                values.mask(args[0].errors.notna(), False),
                args[0].errors.copy(),
            )
        if name == "COALESCE":
            return self._coalesce(args)
        if name == "BLANK":
            return FormulaEvaluation(
                _broadcast(pd.NA, frame.index),
                _blank_errors(frame.index),
            )
        if name in {"TRIM", "UPPER", "LOWER", "LEN"}:
            return self._text_unary(name, args[0])
        if name in {"LEFT", "RIGHT", "MID"}:
            return self._text_slice(name, node, args)
        if name == "FIND":
            return self._find(node, args)
        if name in {"TEXTBEFORE", "TEXTAFTER"}:
            return self._partition(name, node, args)
        if name == "SUBSTITUTE":
            return self._substitute(node, args)
        if name == "VALUE":
            values, errors = _numeric_operand(
                args[0],
                error_message="VALUE 无法转换为数值",
                allow_text=True,
            )
            return FormulaEvaluation(values, errors)
        if name in {"ABS", "ROUND"}:
            return self._numeric_function(name, node, args)
        if name in {"PATHNAME", "PARENTPATH", "EXTENSION", "STEM"}:
            return self._path_function(name, args[0])
        raise FormulaValidationError(f"函数尚未实现: {name}")

    @staticmethod
    def _text_unary(
        name: str,
        argument: FormulaEvaluation,
    ) -> FormulaEvaluation:
        values, errors = _string_operand(argument)
        if name == "TRIM":
            result = values.str.strip()
        elif name == "UPPER":
            result = values.str.upper()
        elif name == "LOWER":
            result = values.str.lower()
        else:
            result = values.str.len()
        return FormulaEvaluation(result.mask(errors.notna(), pd.NA), errors)

    @classmethod
    def _text_slice(
        cls,
        name: str,
        node: FormulaNode,
        args: list[FormulaEvaluation],
    ) -> FormulaEvaluation:
        values, errors = _string_operand(args[0])
        if name == "MID":
            start = cls._scalar_integer(
                node.children[1],
                name,
                "start",
                minimum=1,
            )
            count = cls._scalar_integer(
                node.children[2],
                name,
                "count",
                minimum=0,
            )
            result = values.str.slice(start=start - 1, stop=start - 1 + count)
        else:
            count = cls._scalar_integer(
                node.children[1],
                name,
                "count",
                minimum=0,
            )
            if name == "LEFT":
                result = values.str.slice(stop=count)
            elif count == 0:
                result = pd.Series("", index=values.index, dtype="string")
                result = result.mask(values.isna(), pd.NA)
            else:
                result = values.str.slice(start=-count)
        return FormulaEvaluation(result.mask(errors.notna(), pd.NA), errors)

    @classmethod
    def _find(
        cls,
        node: FormulaNode,
        args: list[FormulaEvaluation],
    ) -> FormulaEvaluation:
        find_text = cls._scalar_text(
            node.children[0],
            "FIND",
            "find_text",
            allow_empty=False,
        )
        within_text, text_errors = _string_operand(args[1])
        errors = _combine_errors(args[0].errors, text_errors)
        zero_based = within_text.str.find(find_text)
        found = zero_based.ge(0) | within_text.isna()
        errors = errors.mask(
            ~found & errors.isna(),
            "FIND 未找到文本",
        )
        positions = (zero_based + 1).astype("Int64")
        return FormulaEvaluation(
            positions.mask(errors.notna(), pd.NA),
            errors,
        )

    @classmethod
    def _partition(
        cls,
        name: str,
        node: FormulaNode,
        args: list[FormulaEvaluation],
    ) -> FormulaEvaluation:
        delimiter = cls._scalar_text(
            node.children[1],
            name,
            "delimiter",
            allow_empty=False,
        )
        values, text_errors = _string_operand(args[0])
        errors = _combine_errors(text_errors, args[1].errors)
        if values.empty:
            return FormulaEvaluation(values.copy(), errors)
        partitions = values.str.partition(delimiter)
        found = partitions[1].eq(delimiter) | values.isna()
        errors = errors.mask(
            ~found & errors.isna(),
            f"{name} 未找到分隔符",
        )
        selected = partitions[0] if name == "TEXTBEFORE" else partitions[2]
        return FormulaEvaluation(
            selected.mask(errors.notna(), pd.NA),
            errors,
        )

    @classmethod
    def _substitute(
        cls,
        node: FormulaNode,
        args: list[FormulaEvaluation],
    ) -> FormulaEvaluation:
        old_text = cls._scalar_text(
            node.children[1],
            "SUBSTITUTE",
            "old_text",
            allow_empty=False,
        )
        new_text = cls._scalar_text(
            node.children[2],
            "SUBSTITUTE",
            "new_text",
            allow_empty=True,
        )
        values, text_errors = _string_operand(args[0])
        errors = _combine_errors(
            text_errors,
            args[1].errors,
            args[2].errors,
        )
        result = values.str.replace(old_text, new_text, regex=False)
        return FormulaEvaluation(result.mask(errors.notna(), pd.NA), errors)

    @classmethod
    def _numeric_function(
        cls,
        name: str,
        node: FormulaNode,
        args: list[FormulaEvaluation],
    ) -> FormulaEvaluation:
        values, errors = _numeric_operand(
            args[0],
            error_message=f"{name} 需要数值",
        )
        if name == "ABS":
            result = values.abs()
        else:
            digits = cls._scalar_integer(
                node.children[1],
                "ROUND",
                "digits",
            )
            errors = _combine_errors(errors, args[1].errors)
            result = values.round(digits)
        return FormulaEvaluation(result.mask(errors.notna(), pd.NA), errors)

    @staticmethod
    def _path_function(
        name: str,
        argument: FormulaEvaluation,
    ) -> FormulaEvaluation:
        values, errors = _string_operand(argument)
        normalized = values.str.replace("\\", "/", regex=False)
        path_name = normalized.str.rsplit("/", n=1).str[-1]
        if name == "PATHNAME":
            result = path_name
        elif name == "PARENTPATH":
            has_parent = normalized.str.contains("/", regex=False, na=False)
            result = normalized.str.rsplit("/", n=1).str[0].where(
                has_parent,
                "",
            )
            result = result.mask(normalized.isna(), pd.NA)
        else:
            if path_name.empty:
                return FormulaEvaluation(path_name.copy(), errors)
            parts = path_name.str.rpartition(".")
            has_suffix = (
                parts[0].ne("")
                & parts[1].eq(".")
                & parts[2].ne("")
            ).fillna(False)
            if name == "EXTENSION":
                result = ("." + parts[2]).where(has_suffix, "")
                result = result.mask(path_name.isna(), pd.NA)
            else:
                result = path_name.where(~has_suffix, parts[0])
        return FormulaEvaluation(result.mask(errors.notna(), pd.NA), errors)

    @classmethod
    def _if(cls, args: list[FormulaEvaluation]) -> FormulaEvaluation:
        condition, when_true, when_false = args
        condition_values, condition_errors = _boolean_operand(
            condition,
            error_message="IF 条件需要布尔值",
        )
        mask = condition_values.fillna(False).astype(bool)
        values = when_false.values.astype("object").copy()
        values.loc[mask] = when_true.values.astype("object").loc[mask]
        selected_errors = when_false.errors.copy()
        selected_errors.loc[mask] = when_true.errors.loc[mask]
        errors = _combine_errors(condition_errors, selected_errors)
        return FormulaEvaluation(
            values.mask(errors.notna(), pd.NA),
            errors,
        )

    @staticmethod
    def _iferror(args: list[FormulaEvaluation]) -> FormulaEvaluation:
        expression, fallback = args
        use_fallback = expression.errors.notna()
        values = expression.values.astype("object").copy()
        values.loc[use_fallback] = fallback.values.astype("object").loc[
            use_fallback
        ]
        errors = _blank_errors(expression.values.index)
        errors.loc[use_fallback] = fallback.errors.loc[use_fallback]
        return FormulaEvaluation(values.mask(errors.notna(), pd.NA), errors)

    @staticmethod
    def _coalesce(args: list[FormulaEvaluation]) -> FormulaEvaluation:
        index = args[0].values.index
        values = _broadcast(pd.NA, index).astype("object")
        errors = _blank_errors(index)
        unresolved = pd.Series(True, index=index, dtype=bool)
        for argument in args:
            has_result = argument.errors.notna() | argument.values.notna()
            selected = unresolved & has_result
            values.loc[selected] = argument.values.astype("object").loc[selected]
            errors.loc[selected] = argument.errors.loc[selected]
            unresolved &= ~selected
        return FormulaEvaluation(values.mask(errors.notna(), pd.NA), errors)

    @classmethod
    def _scalar_integer(
        cls,
        node: FormulaNode,
        function_name: str,
        parameter_name: str,
        *,
        minimum: int | None = None,
    ) -> int:
        value = cls._fixed_literal(node)
        if type(value) is not int:
            raise FormulaValidationError(
                f"函数 {function_name} 的参数 {parameter_name} 必须是固定整数"
            )
        if minimum is not None and value < minimum:
            raise FormulaValidationError(
                f"函数 {function_name} 的参数 {parameter_name} "
                f"不能小于 {minimum}"
            )
        return value

    @staticmethod
    def _fixed_literal(node: FormulaNode) -> object:
        if node.kind == "literal":
            return node.value
        if (
            node.kind == "unary"
            and node.value in {"+", "-"}
            and len(node.children) == 1
            and node.children[0].kind == "literal"
            and type(node.children[0].value) in {int, float}
        ):
            value = node.children[0].value
            return value if node.value == "+" else -value
        return None

    @staticmethod
    def _scalar_text(
        node: FormulaNode,
        function_name: str,
        parameter_name: str,
        *,
        allow_empty: bool,
    ) -> str:
        if node.kind != "literal" or not isinstance(node.value, str):
            raise FormulaValidationError(
                f"函数 {function_name} 的参数 {parameter_name} 必须是固定文本"
            )
        if not allow_empty and not node.value:
            raise FormulaValidationError(
                f"函数 {function_name} 的参数 {parameter_name} 不能为空"
            )
        return node.value


__all__ = [
    "FormulaEngine",
    "FormulaEvaluation",
    "FormulaEvaluationError",
    "FormulaFunctionSpec",
]
