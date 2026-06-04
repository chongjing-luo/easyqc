import pandas as pd

from utils.data_manager import DataManager


def test_read_list_supports_text_and_csv(tmp_path) -> None:
    text_file = tmp_path / "subjects.txt"
    text_file.write_text("SUB001\nSUB002\n", encoding="utf-8")
    csv_file = tmp_path / "subjects.csv"
    csv_file.write_text("ezqcid\nSUB001\nSUB002\n", encoding="utf-8")

    dm = DataManager()

    assert dm.read_list(str(text_file))["path"].tolist() == ["SUB001", "SUB002"]
    assert dm.read_list(str(csv_file))["ezqcid"].tolist() == ["SUB001", "SUB002"]


def test_get_list_returns_one_column_for_directory_children(tmp_path) -> None:
    (tmp_path / "sub-002").mkdir()
    (tmp_path / "sub-001").mkdir()
    (tmp_path / "not_a_subject.txt").write_text("ignored", encoding="utf-8")

    result = DataManager().get_list(str(tmp_path))

    assert list(result.columns) == [0]
    assert set(result[0].tolist()) == {"sub-001", "sub-002"}


def test_get_list_returns_empty_one_column_table_for_empty_directory(tmp_path) -> None:
    result = DataManager().get_list(str(tmp_path))

    assert list(result.columns) == [0]
    assert result.empty


def test_extract_words_as_df_splits_space_comma_and_newline() -> None:
    result = DataManager().extract_words_as_df("SUB001, SUB002\nSUB003")

    assert result["0"].tolist() == ["SUB001", "SUB002", "SUB003"]


def test_set_varname_batch_handles_missing_varname_without_raising() -> None:
    df = pd.DataFrame({"subject": ["SUB001", "SUB002"]})

    result = DataManager().set_varname_batch(df, varname="missing", batch="batch1")

    assert "ezqcid" not in result.columns
    assert result["ezqcbatch"].tolist() == ["batch1", "batch1"]


def test_set_varname_batch_adds_ezqcid_and_batch() -> None:
    df = pd.DataFrame({"subject": ["SUB001", "SUB002"]})

    result = DataManager().set_varname_batch(df, varname="subject", batch="batch1")

    assert result["ezqcid"].tolist() == ["SUB001", "SUB002"]
    assert result["ezqcbatch"].tolist() == ["batch1", "batch1"]


def test_transform_table_uses_structured_operations() -> None:
    df = pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002"],
            "age": [29, 31],
            "score": [3, 2],
        }
    )

    result = DataManager().transform_table(
        df,
        [
            {"operation": "derive_column", "name": "qc_pass", "expression": "score >= 3"},
            {"operation": "filter_rows", "conditions": [{"column": "qc_pass", "operator": "==", "value": True}]},
            {"operation": "select_columns", "columns": ["ezqcid", "qc_pass"]},
        ],
    )

    assert result.to_dict("records") == [{"ezqcid": "SUB001", "qc_pass": True}]
