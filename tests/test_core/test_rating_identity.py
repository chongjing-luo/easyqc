from pathlib import Path

import pytest

from core.rating_identity import (
    PORTABLE_FILENAME_MAX_UTF8_BYTES,
    PORTABLE_PATH_MAX_UTF16_UNITS,
    RatingFilenameError,
    RatingIdentity,
    RatingIdentityError,
    RatingPathBudgetError,
    build_rating_filename,
    canonical_rating_path,
    parse_rating_filename,
    validate_easyqcid,
    validate_module_name,
    validate_rating_identity,
    validate_rater,
)


def test_rating_identity_accepts_approved_characters_and_max_lengths() -> None:
    identity = validate_rating_identity(
        "M" * 32,
        "r_1",
        "SUB.001-session_1-" + ("x" * 110),
    )

    assert identity == RatingIdentity(
        module_name="M" * 32,
        rater="r_1",
        easyqcid="SUB.001-session_1-" + ("x" * 110),
    )
    assert len(identity.easyqcid) == 128


def test_public_field_validators_return_the_exact_string() -> None:
    assert validate_module_name("Anat_QC") == "Anat_QC"
    assert validate_rater("Rater_01") == "Rater_01"
    assert validate_easyqcid("SUB.001-session-1") == "SUB.001-session-1"


@pytest.mark.parametrize(
    "validator",
    [validate_module_name, validate_rater, validate_easyqcid],
)
def test_public_field_validators_require_str_without_coercion(validator) -> None:
    with pytest.raises(RatingIdentityError):
        validator(123)


@pytest.mark.parametrize(
    ("field", "module_name", "rater", "easyqcid"),
    [
        ("module_name", "", "rater", "SUB001"),
        ("module_name", "Anat-All", "rater", "SUB001"),
        ("module_name", "模块", "rater", "SUB001"),
        ("module_name", "M" * 33, "rater", "SUB001"),
        ("rater", "AnatAll", "", "SUB001"),
        ("rater", "AnatAll", "rater-one", "SUB001"),
        ("rater", "AnatAll", "rater.one", "SUB001"),
        ("rater", "AnatAll", "r" * 33, "SUB001"),
        ("easyqcid", "AnatAll", "rater", ""),
        ("easyqcid", "AnatAll", "rater", "SUB/001"),
        ("easyqcid", "AnatAll", "rater", "SUB 001"),
        ("easyqcid", "AnatAll", "rater", "x" * 129),
        ("easyqcid", "AnatAll", "rater", "."),
        ("easyqcid", "AnatAll", "rater", ".."),
    ],
)
def test_rating_identity_rejects_invalid_identifiers(
    field: str,
    module_name: str,
    rater: str,
    easyqcid: str,
) -> None:
    with pytest.raises(RatingIdentityError) as exc_info:
        RatingIdentity(module_name, rater, easyqcid)

    assert exc_info.value.field == field


@pytest.mark.parametrize(
    "reserved_name",
    ["CON", "prn", "Aux", "nul", "COM1", "com9", "LPT1", "lpt9"],
)
@pytest.mark.parametrize("field", ["module_name", "rater"])
def test_rating_identity_rejects_windows_device_names_case_insensitively(
    reserved_name: str,
    field: str,
) -> None:
    values = {
        "module_name": "AnatAll",
        "rater": "reviewer",
        "easyqcid": "SUB001",
    }
    values[field] = reserved_name

    with pytest.raises(RatingIdentityError) as exc_info:
        RatingIdentity(**values)

    assert exc_info.value.field == field
    assert "Windows" in exc_info.value.reason


def test_rating_identity_rejects_reserved_observation_rater_case_insensitively() -> None:
    with pytest.raises(RatingIdentityError) as exc_info:
        RatingIdentity("AnatAll", "__OBSERVATION_NO_RATER__", "SUB001")

    assert exc_info.value.field == "rater"
    assert "reserved" in exc_info.value.reason


def test_filename_round_trip_preserves_hyphens_in_easyqcid() -> None:
    identity = RatingIdentity("AnatAll", "rater_1", "CCNPPEK0001_01-anat-v2")

    filename = build_rating_filename(identity)

    assert filename == "AnatAll-rater_1-CCNPPEK0001_01-anat-v2.json"
    assert parse_rating_filename(filename) == identity


def test_parser_removes_exactly_one_final_json_suffix() -> None:
    identity = parse_rating_filename("AnatAll-rater_1-SUB001.json.json")

    assert identity.easyqcid == "SUB001.json"


@pytest.mark.parametrize(
    "filename",
    [
        "AnatAll-rater_1-SUB001",
        "AnatAll-rater_1-SUB001.JSON",
        "AnatAll-rater_1.json",
        "folder/AnatAll-rater_1-SUB001.json",
        r"folder\AnatAll-rater_1-SUB001.json",
    ],
)
def test_parser_rejects_malformed_or_non_basename_input(filename: str) -> None:
    with pytest.raises(RatingFilenameError):
        parse_rating_filename(filename)


def test_canonical_rating_path_is_pure_and_uses_redundant_identity(tmp_path: Path) -> None:
    rating_root = tmp_path / "RatingFiles"
    identity = RatingIdentity("AnatAll", "rater_1", "SUB001-session-1")

    result = canonical_rating_path(rating_root, identity)

    assert result == (
        rating_root
        / "AnatAll"
        / "rater_1"
        / "AnatAll-rater_1-SUB001-session-1.json"
    )
    assert not rating_root.exists()


def test_filename_and_path_budgets_are_explicit_and_fail_before_io(
    tmp_path: Path,
) -> None:
    assert PORTABLE_FILENAME_MAX_UTF8_BYTES == 240
    assert PORTABLE_PATH_MAX_UTF16_UNITS == 240

    rating_root = tmp_path / ("x" * 220)
    with pytest.raises(RatingPathBudgetError) as exc_info:
        canonical_rating_path(
            rating_root,
            RatingIdentity("AnatAll", "rater_1", "SUB001"),
        )

    assert exc_info.value.budget == "absolute path UTF-16 code units"
    assert exc_info.value.actual > exc_info.value.limit
    assert not rating_root.exists()
