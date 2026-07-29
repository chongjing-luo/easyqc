from __future__ import annotations

from utils.data_manager import DataManager


def test_data_manager_csv_import_preserves_leading_zero_and_literal_na(
    tmp_path,
) -> None:
    source = tmp_path / "subjects.csv"
    source.write_text(
        "source_id,label\n001,NA\nNA,001\n",
        encoding="utf-8",
    )

    frame = DataManager().read_list(str(source))

    assert frame["source_id"].tolist() == ["001", "NA"]
    assert frame["label"].tolist() == ["NA", "001"]
