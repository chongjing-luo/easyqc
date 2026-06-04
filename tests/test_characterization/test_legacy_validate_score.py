from gui.dialog_main import DialogMain


def test_validate_score_returns_label_list_for_comma_labels() -> None:
    result = DialogMain.validate_score(None, "Poor,Fair,Good", show_error=False)

    assert result == ["Poor", "Fair", "Good"]


def test_validate_score_expands_numeric_range() -> None:
    result = DialogMain.validate_score(None, "0-3", show_error=False)

    assert result == "0,1,2,3"


def test_validate_score_expands_single_number_from_one() -> None:
    result = DialogMain.validate_score(None, "3", show_error=False)

    assert result == "1,2,3"


def test_validate_score_rejects_duplicate_labels() -> None:
    result = DialogMain.validate_score(None, "Good,Good", show_error=False)

    assert result is None
