"""RI-13b: synthetic-only, read-only association projection contracts."""

from copy import deepcopy
from dataclasses import replace
from time import perf_counter
import tracemalloc

import pandas as pd
import pytest

from core.result_association import parse_association_rules, project_associations
from models.rating import Rating
from models.result_association import AssociationSource, ResultAssociationRule
from models.table_view_state import (
    FilterCondition,
    FilterExpression,
    FilterGroup,
    filter_expression_to_json_object,
)


def _filter(column, value):
    return filter_expression_to_json_object(FilterExpression(groups=(
        FilterGroup("group", "all", (FilterCondition(column, "==", value, "condition"),)),
    )))


def _rule(**changes):
    values = dict(
        rule_id="anat_to_func", name="Anatomical results", source_module="anat",
        source_rater="r1", source_key="participant", target_key="participant",
        fields=(("score1", "anat_score"), ("tag1", "anat_tag"), ("notes", "anat_notes")),
        source_filter=_filter("kind", "anat"), target_filter=_filter("kind", "func"),
    )
    values.update(changes)
    return ResultAssociationRule(**values)


def _master():
    return pd.DataFrame({
        "easyqcid": ["F2", "A2", "F1", "A1", "Z"],
        "participant": ["01", "01", "01", "01", "99"],
        "kind": ["func", "anat", "func", "anat", "func"],
    }, index=[15, 7, 20, 4, 1])


def _rating(identity="A1", *, module="anat", rater="r1", score=2, tag=False, notes="raw note"):
    return Rating(module, rater, identity, {"1": score}, {"1": tag}, notes)


def test_rule_json_round_trip_and_defensive_copies():
    source_filter = _filter("kind", "anat")
    fields = [["score1", "derived"]]
    rule = _rule(source_filter=source_filter, fields=fields)
    source_filter["groups"][0]["conditions"][0]["value"] = "changed"
    fields[0][1] = "changed"
    assert rule.fields == (("score1", "derived"),)
    assert rule.source_filter["groups"][0]["conditions"][0]["value"] == "anat"
    payload = rule.to_dict()
    assert payload["fields"] == [["score1", "derived"]]
    restored = ResultAssociationRule.from_dict(payload)
    assert restored == rule
    assert parse_association_rules([payload]) == (rule,)
    payload["source_filter"]["groups"][0]["conditions"][0]["value"] = "changed"
    assert restored.source_filter["groups"][0]["conditions"][0]["value"] == "anat"
    assert rule.source_filter["groups"][0]["conditions"][0]["value"] == "anat"
    assert parse_association_rules([]) == ()


@pytest.mark.parametrize("changes", [
    {"rule_id": ""}, {"name": " "}, {"source_module": ""}, {"source_rater": ""},
    {"source_key": ""}, {"target_key": " "}, {"fields": ()},
    {"fields": (("derived_input", "output"),)}, {"fields": (("time", "output"),)},
    {"fields": (("score1", " output "),)}, {"fields": (("notes", ""),)},
    {"fields": (("score1", "duplicate"), ("score2", "duplicate"))},
    {"fields": (("score1", "easyqcid"),)}, {"fields": (("score1", "module_name"),)},
    {"fields": (("score1", "rater"),)}, {"fields": (("score1", "notes"),)},
    {"fields": (("score1", "time"),)}, {"fields": (("score1", "score9"),)},
    {"fields": (("score1", "tag2"),)}, {"source_filter": "not JSON"},
])
def test_rule_rejects_invalid_contract(changes):
    with pytest.raises((ValueError, TypeError)):
        _rule(**changes)


@pytest.mark.parametrize("output", [
    "FutureQC.future_rater.score1", "FutureQC.future_rater.score999",
    "FutureQC.future_rater.tag2", "FutureQC.future_rater.notes",
    "FutureQC.future_rater.time",
])
def test_rule_reserves_natural_result_namespace_for_future_modules_and_raters(output):
    with pytest.raises(ValueError, match="reserved"):
        _rule(fields=(("score1", output),))


@pytest.mark.parametrize("output", ["linked.anat", "关联.评分", "custom.result.text"])
def test_rule_allows_non_reserved_dotted_labels(output):
    assert _rule(fields=(("score1", output),)).fields == (("score1", output),)


@pytest.mark.parametrize("payload", [None, {}, "rules", [42], [{"rule_id": "incomplete"}]])
def test_parse_rejects_malformed_payload(payload):
    with pytest.raises((ValueError, TypeError)):
        parse_association_rules(payload)


def test_parse_rejects_unknown_fields_invalid_filter_and_cross_rule_duplicates():
    payload = _rule().to_dict()
    payload["unknown"] = True
    with pytest.raises(ValueError, match="unknown|unexpected"):
        parse_association_rules([payload])
    payload = _rule().to_dict()
    payload["source_filter"]["schema_version"] = 99
    with pytest.raises(ValueError, match="schema_version"):
        parse_association_rules([payload])
    with pytest.raises(ValueError, match="rule_id|duplicate"):
        parse_association_rules([_rule().to_dict(), _rule().to_dict()])
    with pytest.raises(ValueError, match="anat_score|output|duplicate"):
        parse_association_rules([_rule().to_dict(), _rule(rule_id="another").to_dict()])


def test_many_sources_are_labelled_without_row_expansion_or_input_mutation():
    master = _master()
    ratings = [_rating("A1", score="01", tag=True), _rating("A2", score=0, notes="")]
    original_master, original_ratings = master.copy(deep=True), deepcopy(ratings)
    rule = _rule()
    original_rule = rule.to_dict()
    result = project_associations(master, ratings, (rule,))
    assert result.columns.columns.tolist() == ["easyqcid", "anat_score", "anat_tag", "anat_notes"]
    assert result.columns.index.tolist() == master.index.tolist()
    assert result.columns["easyqcid"].tolist() == master["easyqcid"].tolist()
    assert result.columns["anat_score"].tolist() == ["A2: 0; A1: 01", "", "A2: 0; A1: 01", "", ""]
    assert result.columns.at[15, "anat_tag"] == "A2: False; A1: True"
    assert result.columns.at[15, "anat_notes"] == "A2: ; A1: raw note"
    assert result.sources_by_easyqcid["F2"] == (
        AssociationSource(rule.rule_id, rule.name, "A2", "anat", "r1", True),
        AssociationSource(rule.rule_id, rule.name, "A1", "anat", "r1", True),
    )
    assert result.sources_by_easyqcid["Z"] == ()
    pd.testing.assert_frame_equal(master, original_master)
    assert ratings == original_ratings
    assert rule.to_dict() == original_rule
    result.columns.at[15, "easyqcid"] = "detached"
    pd.testing.assert_frame_equal(master, original_master)


def test_single_source_propagates_to_many_targets_and_keeps_raters_separate():
    master = _master().drop(index=7)
    first = _rule()
    second = _rule(rule_id="r2", source_rater="r2", fields=(("score1", "r2_score"),))
    result = project_associations(master, [_rating(score=2), _rating(rater="r2", score=4)], (first, second))
    assert result.columns["anat_score"].tolist() == ["A1: 2", "A1: 2", "", ""]
    assert result.columns["r2_score"].tolist() == ["A1: 4", "A1: 4", "", ""]
    assert [(item.easyqcid, item.rater) for item in result.sources_by_easyqcid["F2"]] == [("A1", "r1"), ("A1", "r2")]


def test_bidirectional_rules_use_only_original_master_and_raw_ratings():
    forward = _rule()
    reverse = _rule(
        rule_id="reverse", source_module="func", fields=(("score1", "func_score"),),
        source_filter=_filter("kind", "func"), target_filter=_filter("kind", "anat"),
    )
    result = project_associations(_master(), [_rating("A1"), _rating("F2", module="func", score=7)], (forward, reverse))
    assert result.columns.at[7, "func_score"] == "F2: 7; F1: —"
    assert result.columns.at[15, "func_score"] == ""
    assert result.columns.at[15, "anat_score"] == "A2: —; A1: 2"
    with pytest.raises(ValueError, match="anat_score"):
        project_associations(_master(), [], (forward, replace(reverse, source_key="anat_score")))
    with pytest.raises(ValueError, match="anat_score"):
        project_associations(_master(), [], (forward, replace(reverse, source_filter=_filter("anat_score", "2"))))


def test_unrated_missing_fields_and_no_source_are_distinguishable():
    partial = Rating("anat", "r1", "A1", {}, {}, None)
    result = project_associations(_master(), [partial], (_rule(),))
    assert result.columns.at[15, "anat_score"] == "A2: —; A1: —"
    assert result.columns.at[15, "anat_notes"] == "A2: —; A1: —"
    assert result.columns.at[1, "anat_score"] == ""
    assert [source.has_rating for source in result.sources_by_easyqcid["F2"]] == [False, True]


def test_exact_keys_preserve_text_leading_zeros_and_exclude_blanks_and_nulls():
    keys = ["01", "1", "", " ", None, float("nan"), pd.NA, " 01"]
    master = pd.DataFrame({
        "easyqcid": [f"S{i}" for i in range(8)] + [f"T{i}" for i in range(8)],
        "key": keys * 2, "kind": ["anat"] * 8 + ["func"] * 8,
    })
    result = project_associations(master, [_rating("S0", score=1), _rating("S1", score=2)], (
        _rule(source_key="key", target_key="key", fields=(("score1", "linked"),)),
    ))
    assert result.columns["linked"].tolist()[8:] == ["S0: 1", "S1: 2", "", "", "", "", "", "S7: —"]


def test_different_source_and_target_keys():
    master = pd.DataFrame({"easyqcid": ["A", "F"], "source": ["p", "other"], "target": ["other", "p"], "kind": ["anat", "func"]})
    result = project_associations(master, [_rating("A")], (_rule(source_key="source", target_key="target"),))
    assert result.columns["anat_score"].tolist() == ["", "A: 2"]


@pytest.mark.parametrize("key", ["source_key", "target_key"])
def test_unknown_keys_fail_even_for_empty_master(key):
    with pytest.raises(ValueError, match="absent"):
        project_associations(_master().iloc[:0], [], (_rule(**{key: "absent"}),))


def test_missing_and_duplicate_master_columns_or_identities_fail():
    with pytest.raises(ValueError, match="easyqcid"):
        project_associations(_master().drop(columns="easyqcid"), [], (_rule(),))
    duplicate_columns = _master().copy()
    duplicate_columns.columns = ["easyqcid", "participant", "participant"]
    with pytest.raises(ValueError, match="duplicate|重复"):
        project_associations(duplicate_columns, [], (_rule(),))
    duplicate_ids = _master().copy()
    duplicate_ids.at[7, "easyqcid"] = "F2"
    with pytest.raises(ValueError, match="duplicate|重复"):
        project_associations(duplicate_ids, [], (_rule(),))
    invalid_ids = _master().copy()
    invalid_ids.at[7, "easyqcid"] = " "
    with pytest.raises(ValueError, match="easyqcid"):
        project_associations(invalid_ids, [], (_rule(),))


def test_duplicate_rating_identity_fails_but_different_modules_are_independent():
    with pytest.raises(ValueError, match="duplicate|重复"):
        project_associations(_master(), [_rating(), _rating(score=3)], (_rule(),))
    result = project_associations(_master(), [_rating(), _rating(module="other", score=9)], (_rule(),))
    assert result.columns.at[15, "anat_score"] == "A2: —; A1: 2"


def test_master_column_collision_and_invalid_non_scalar_keys_fail():
    with pytest.raises(ValueError, match="participant|collision"):
        project_associations(_master(), [], (_rule(fields=(("score1", "participant"),)),))
    bad_keys = _master().copy()
    bad_keys.at[7, "participant"] = ["01"]
    with pytest.raises((ValueError, TypeError), match="key|participant"):
        project_associations(bad_keys, [], (_rule(),))


def test_no_rules_and_empty_master_are_well_formed():
    master = _master()
    no_rules = project_associations(master, [], ())
    pd.testing.assert_frame_equal(no_rules.columns, master[["easyqcid"]])
    assert no_rules.sources_by_easyqcid == {identity: () for identity in master.easyqcid}
    empty = project_associations(master.iloc[:0], [], (_rule(),))
    assert empty.columns.columns.tolist() == ["easyqcid", "anat_score", "anat_tag", "anat_notes"]
    assert empty.columns.empty
    assert empty.sources_by_easyqcid == {}


def test_no_filter_means_all_original_rows_and_duplicate_dataframe_index_is_preserved():
    master = pd.DataFrame({"easyqcid": ["A", "B"], "key": ["01", "01"]}, index=[4, 4])
    rule = _rule(source_key="key", target_key="key", source_filter=None, target_filter=None)
    result = project_associations(master, [_rating("A")], (rule,))
    assert result.columns.index.tolist() == [4, 4]
    assert result.columns["anat_score"].tolist() == ["A: 2; B: —", "A: 2; B: —"]
    with pytest.raises(TypeError):
        result.sources_by_easyqcid["A"] = ()


def test_numeric_boolean_and_text_join_keys_are_not_conflated():
    master = pd.DataFrame({
        "easyqcid": ["A", "B", "C", "D", "E", "F"],
        "key": [1, True, "1", 1, True, "1"],
        "kind": ["anat"] * 3 + ["func"] * 3,
    })
    result = project_associations(master, [], (_rule(source_key="key", target_key="key"),))
    assert result.columns["anat_score"].tolist() == ["", "", "", "A: —", "B: —", "C: —"]


def test_raw_rating_facts_are_authoritative_not_module_payload_or_other_raters():
    rating = _rating("A1", score="raw-value")
    rating.module_payload = {"scores": {"1": {"value": "not-the-rating-value"}}}
    result = project_associations(_master(), [rating, _rating("A2", rater="r2", score=9)], (_rule(),))
    assert result.columns.at[15, "anat_score"] == "A2: —; A1: raw-value"


def test_same_source_field_can_be_projected_to_distinct_new_columns():
    rule = _rule(fields=(("score1", "first"), ("score1", "second")))
    result = project_associations(_master(), [_rating()], (rule,))
    assert result.columns["first"].tolist() == result.columns["second"].tolist()


@pytest.mark.parametrize("side", ["source_filter", "target_filter"])
def test_unknown_filter_columns_fail_before_projection_is_returned(side):
    with pytest.raises(ValueError, match="missing_column"):
        project_associations(_master(), [], (_rule(**{side: _filter("missing_column", "x")}),))


def test_invalid_source_rating_value_fails_without_mutation():
    rating = _rating(score=[1, 2])
    original = deepcopy(rating)
    with pytest.raises(ValueError, match="score1.*scalar"):
        project_associations(_master(), [rating], (_rule(),))
    assert rating == original


def test_ten_thousand_row_indexed_projection_sanity():
    count = 5000
    master = pd.DataFrame({
        "easyqcid": [f"A{i}" for i in range(count)] + [f"F{i}" for i in range(count)],
        "participant": [f"{i:05d}" for i in range(count)] * 2,
        "kind": ["anat"] * count + ["func"] * count,
    })
    started = perf_counter()
    result = project_associations(master, [_rating(f"A{i}", score=i) for i in range(count)], (_rule(),))
    elapsed = perf_counter() - started
    assert len(result.columns) == 10000
    assert result.columns.iloc[-1].anat_score == "A4999: 4999"
    assert result.sources_by_easyqcid["F4999"][0].easyqcid == "A4999"
    assert elapsed < 10, f"indexed 10k projection unexpectedly took {elapsed:.2f}s"


def test_shared_many_to_many_source_collections_have_bounded_memory():
    count = 1000
    master = pd.DataFrame({
        "easyqcid": [f"A{i}" for i in range(count)] + [f"F{i}" for i in range(count)],
        "participant": ["same-key"] * (count * 2),
        "kind": ["anat"] * count + ["func"] * count,
    })
    rules = (
        _rule(fields=(("score1", "first"),)),
        _rule(rule_id="second", source_rater="r2", fields=(("score1", "second"),)),
    )
    tracemalloc.start()
    try:
        projection = project_associations(master, [], rules)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    print(f"shared-source projection: targets=1000 sources_per_target=2000 peak_mib={peak / 1024**2:.2f}")
    # One thousand targets share two identical 1k-source collections; the
    # projection must not allocate two million tuple/list reference slots.
    assert len(projection.columns) == 2000
    assert len(projection.sources_by_easyqcid["F0"]) == 2000
    assert projection.sources_by_easyqcid["F999"] == projection.sources_by_easyqcid["F0"]
    assert projection.sources_by_easyqcid["F0"][999].easyqcid == "A999"
    assert projection.sources_by_easyqcid["F0"][1000].rater == "r2"
    assert peak < 12 * 1024 * 1024, f"shared-source projection used {peak / 1024**2:.2f} MiB"
