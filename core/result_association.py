"""Pure, row-preserving association of original rating facts with a master list."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from types import MappingProxyType
from typing import Any

import pandas as pd
from pandas.api.types import is_scalar

from core.module_filter import resolve_module_filter_identities
from core.rating_identity import validate_easyqcid, validate_module_name, validate_rater
from models.rating import Rating
from models.result_association import AssociationSource, ResultAssociationRule
from models.table_view_state import filter_expression_from_json_object


@dataclass(frozen=True)
class AssociationProjection:
    """Detached derived cells plus immutable exact references, in master order."""

    columns: pd.DataFrame
    sources_by_easyqcid: Mapping[str, tuple[AssociationSource, ...]]


def _validate_rules(rules: Sequence[ResultAssociationRule]) -> None:
    if not isinstance(rules, (list, tuple)):
        raise ValueError("association rules must be a sequence of ResultAssociationRule")
    identifiers: set[str] = set()
    outputs: set[str] = set()
    for rule in rules:
        if not isinstance(rule, ResultAssociationRule):
            raise ValueError("association rules must contain ResultAssociationRule objects")
        if rule.rule_id in identifiers:
            raise ValueError(f"duplicate association rule_id: {rule.rule_id!r}")
        identifiers.add(rule.rule_id)
        validate_module_name(rule.source_module)
        validate_rater(rule.source_rater)
        for _, output in rule.fields:
            if output in outputs:
                raise ValueError(f"duplicate association output name: {output!r}")
            outputs.add(output)
        filter_expression_from_json_object(rule.source_filter)
        filter_expression_from_json_object(rule.target_filter)


def parse_association_rules(payload: object) -> tuple[ResultAssociationRule, ...]:
    """Read a settings rule array without I/O or mutation; invalid input raises.

    This boundary validates schema and filter syntax. Master-dependent column
    checks belong to project_associations, not settings deserialization.
    """

    if not isinstance(payload, (list, tuple)):
        raise ValueError("result_associations must be an array")
    rules = tuple(ResultAssociationRule.from_dict(item) for item in payload)
    _validate_rules(rules)
    return rules


def _join_key(value: Any, column: str) -> tuple[object, Any] | None:
    """Keep text exact; missing/blank scalars never form a matching group."""

    if not is_scalar(value):
        raise ValueError(f"association key column {column!r} must contain scalar values")
    if pd.isna(value):
        return None
    if isinstance(value, str):
        return (str, value) if value.strip() else None
    # Distinguish strings, Booleans and numeric values; never parse text keys.
    if isinstance(value, bool):
        return (bool, value)
    if isinstance(value, Real):
        return (Real, value)
    try:
        hash(value)
    except TypeError as exc:
        raise ValueError(f"association key column {column!r} contains an invalid value") from exc
    return (type(value), value)


def _rating_text(rating: Rating | None, field: str) -> str:
    if rating is None:
        return "—"
    if field == "notes":
        value = rating.notes
    elif field.startswith("score"):
        value = rating.scores.get(field[len("score"):])
    else:
        value = rating.tags.get(field[len("tag"):])
    if not is_scalar(value):
        raise ValueError(f"rating field {field!r} must contain a scalar value")
    return "—" if pd.isna(value) else str(value)


def project_associations(
    master: pd.DataFrame,
    ratings: Sequence[Rating],
    rules: Sequence[ResultAssociationRule],
) -> AssociationProjection:
    """Project labelled raw rating collections without expanding master rows.

    Input: original master columns, raw Ratings and validated rule objects.
    Output: easyqcid + new display columns with the original index/order, and
    exact source references for every master identity. No I/O or input changes.
    Bad rules, keys, columns or duplicate identities fail before any result is
    returned. Group indexes avoid scanning the master once per target row.
    Persistence and rendering are deliberately outside this function.
    """

    if not isinstance(master, pd.DataFrame):
        raise TypeError("association master must be a pandas DataFrame")
    if master.columns.has_duplicates:
        raise ValueError("association master contains duplicate columns")
    if "easyqcid" not in master.columns:
        raise ValueError("association master requires easyqcid")
    _validate_rules(rules)
    identities = master["easyqcid"].tolist()
    seen: set[str] = set()
    for identity in identities:
        validate_easyqcid(identity)
        if identity in seen:
            raise ValueError(f"duplicate master easyqcid: {identity!r}")
        seen.add(identity)

    keys: dict[str, list[tuple[object, Any] | None]] = {}
    for rule in rules:
        for column in (rule.source_key, rule.target_key):
            if column not in master.columns:
                raise ValueError(f"unknown association key column: {column!r}")
            if column not in keys:
                keys[column] = [_join_key(value, column) for value in master[column].tolist()]
        for _, output in rule.fields:
            if output in master.columns:
                raise ValueError(f"association output column collision: {output!r}")

    rating_index: dict[tuple[str, str, str], Rating] = {}
    for rating in ratings:
        if not isinstance(rating, Rating):
            raise TypeError("association ratings must contain Rating objects")
        identity = (
            validate_easyqcid(rating.easyqcid),
            validate_module_name(rating.module_name),
            validate_rater(rating.rater),
        )
        if identity in rating_index:
            raise ValueError(f"duplicate rating identity: {identity!r}")
        rating_index[identity] = rating

    derived: dict[str, list[str]] = {}
    source_groups: list[tuple[AssociationSource, ...]] = []
    matched_groups: dict[str, list[int]] = {identity: [] for identity in identities}
    for rule in rules:
        eligible_sources = set(resolve_module_filter_identities(
            master, filter_expression_from_json_object(rule.source_filter),
        ))
        eligible_targets = set(resolve_module_filter_identities(
            master, filter_expression_from_json_object(rule.target_filter),
        ))
        grouped: dict[tuple[object, Any], list[str]] = defaultdict(list)
        for identity, key in zip(identities, keys[rule.source_key]):
            if identity in eligible_sources and key is not None:
                grouped[key].append(identity)

        # Render each source collection once; repeated target keys reuse it.
        collections: dict[tuple[object, Any], tuple[tuple[str, ...], int]] = {}
        for key, members in grouped.items():
            source_ratings = [rating_index.get((identity, rule.source_module, rule.source_rater)) for identity in members]
            references = tuple(
                AssociationSource(rule.rule_id, rule.name, identity, rule.source_module, rule.source_rater, rating is not None)
                for identity, rating in zip(members, source_ratings)
            )
            values = tuple(
                "; ".join(f"{identity}: {_rating_text(rating, field)}" for identity, rating in zip(members, source_ratings))
                for field, _ in rule.fields
            )
            collections[key] = (values, len(source_groups))
            source_groups.append(references)

        rule_columns = [[] for _ in rule.fields]
        for identity, key in zip(identities, keys[rule.target_key]):
            collection = collections.get(key) if key is not None and identity in eligible_targets else None
            if collection is None:
                for column in rule_columns:
                    column.append("")
            else:
                values, group_index = collection
                for column, value in zip(rule_columns, values):
                    column.append(value)
                matched_groups[identity].append(group_index)
        for (_, output), values in zip(rule.fields, rule_columns):
            derived[output] = values

    columns = master.loc[:, ["easyqcid"]].copy(deep=True)
    for name, values in derived.items():
        columns[name] = pd.Series(values, index=master.index, dtype=object)

    # Match-group IDs are small stable cache keys. Identical target matches
    # share one immutable tuple, including combinations from multiple rules.
    # Expanding source references separately per target is quadratic in a
    # many-to-many group, even though the displayed cells already share text.
    combinations: dict[tuple[int, ...], tuple[AssociationSource, ...]] = {(): ()}
    sources: dict[str, tuple[AssociationSource, ...]] = {}
    for identity, group_indices in matched_groups.items():
        signature = tuple(group_indices)
        if signature not in combinations:
            combinations[signature] = (
                source_groups[signature[0]]
                if len(signature) == 1
                else tuple(source for index in signature for source in source_groups[index])
            )
        sources[identity] = combinations[signature]
    return AssociationProjection(
        columns=columns,
        sources_by_easyqcid=MappingProxyType(sources),
    )


__all__ = ["AssociationProjection", "parse_association_rules", "project_associations"]
