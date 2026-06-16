from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from models.project import Project
from models.qcmodule import QCModule
from models.rating import Rating
from utils.file_utils import FileUtils


@dataclass
class LoadedRatingsState:
    ratings: list[Rating]
    rating_dict: dict[str, dict[str, dict[str, Any]]]
    qctable: pd.DataFrame
    original_table: pd.DataFrame
    original_wide_table: pd.DataFrame


class RatingService:
    def __init__(self, project_or_service: Project | Any) -> None:
        self.project_or_service = project_or_service

    @property
    def project(self) -> Project:
        if isinstance(self.project_or_service, Project):
            return self.project_or_service
        current = getattr(self.project_or_service, "current_project", None)
        if current is None:
            raise ValueError("当前项目未加载")
        return current

    def scan_rating_files(self) -> list[Path]:
        rating_dir = self.project.rating_dir
        if not rating_dir.exists():
            return []
        return sorted(rating_dir.rglob("*.json"))

    def validate_rating_file(self, path: Path) -> bool:
        parts = path.name.replace(".json", "").split("._.")
        if len(parts) < 3:
            return False

        file_module = parts[0]
        file_ezqcid = parts[1]
        file_rater = parts[2]
        dir_rater = path.parent.name
        dir_module = path.parent.parent.name

        if file_module != dir_module or file_rater != dir_rater:
            return False

        try:
            rating = Rating.from_json_file(path)
        except Exception:
            return False

        return (
            rating.module_name == dir_module
            and rating.rater == dir_rater
            and rating.ezqcid == file_ezqcid
        )

    def load_rating(self, path: Path) -> Rating:
        return Rating.from_json_file(path)

    @staticmethod
    def find_rating_files_in_rater_dir(
        target_dir: Path,
        module_name: str,
        ezqcid: str,
        rater: str,
    ) -> list[Path]:
        prefix = f"{module_name}._.{ezqcid}._.{rater}"
        return sorted(target_dir.glob(f"{prefix}*"))

    @staticmethod
    def load_legacy_rating_file(path: Path) -> dict[str, Any]:
        return Rating.from_json_file(path).to_legacy_dict()

    def save_rating(self, rating: Rating, legacy_module: QCModule | dict[str, Any] | None = None) -> Path:
        target_dir = self.project.rating_dir / rating.module_name / rating.rater
        return self.save_rating_to_rater_dir(target_dir, rating, legacy_module)

    @staticmethod
    def save_rating_to_rater_dir(
        target_dir: Path,
        rating: Rating,
        legacy_module: QCModule | dict[str, Any] | None = None,
    ) -> Path:
        if legacy_module is None and rating.legacy_payload is None:
            raise ValueError("保存评分 JSON 需要完整 legacy qcmodule payload")

        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / rating.filename
        prefix = f"{rating.module_name}._.{rating.ezqcid}._.{rating.rater}"
        old_files = list(target_dir.glob(f"{prefix}*"))

        # P0-E: stamp the current schema version onto the outbound rating
        # payload (metadata layered on the 16-key module snapshot; NOT a module
        # field). Inject at the service layer to keep models/rating.py pure.
        # Never downgrade an existing higher version (forward-compat).
        payload = rating.to_legacy_dict(legacy_module)
        current = payload.get("schema_version")
        if not isinstance(current, int) or current < 1:
            payload["schema_version"] = 1
        FileUtils.safe_json_save(target_path, payload)

        for old_file in old_files:
            if old_file.resolve() != target_path.resolve():
                old_file.unlink()

        return target_path

    def load_all_ratings(self) -> list[Rating]:
        return [rating for rating, _ in self.load_all_rating_records()]

    def load_all_rating_records(self) -> list[tuple[Rating, Path]]:
        ratings = []
        for path in self.scan_rating_files():
            if self.validate_rating_file(path):
                ratings.append((self.load_rating(path), path))
        return ratings

    def load_legacy_state(self, subjects: pd.DataFrame) -> LoadedRatingsState:
        """Load ratings in the shape expected by the legacy GUI state."""
        records = self.load_all_rating_records()
        ratings = [rating for rating, _ in records]
        original_table = self.rating_records_to_long_dataframe(records)
        original_wide_table = self.long_table_to_wide(original_table)
        return LoadedRatingsState(
            ratings=ratings,
            rating_dict=self.build_rating_dict(ratings),
            qctable=self.merge_subjects_with_rating_wide(original_wide_table, subjects),
            original_table=original_table,
            original_wide_table=original_wide_table,
        )

    def build_rating_dict(self, ratings: list[Rating]) -> dict[str, dict[str, dict[str, Any]]]:
        rating_dict: dict[str, dict[str, dict[str, Any]]] = {}
        for rating in ratings:
            rating_dict.setdefault(rating.ezqcid, {})
            rating_dict[rating.ezqcid][f"{rating.module_name}-{rating.rater}"] = rating.to_legacy_dict()
        return rating_dict

    def aggregate_to_wide(self, ratings: list[Rating], subjects: pd.DataFrame) -> pd.DataFrame:
        original_table = self.rating_records_to_long_dataframe([(rating, None) for rating in ratings])
        original_wide_table = self.long_table_to_wide(original_table)
        return self.merge_subjects_with_rating_wide(original_wide_table, subjects)

    def rating_records_to_long_dataframe(self, records: list[tuple[Rating, Path | None]]) -> pd.DataFrame:
        if not records:
            return pd.DataFrame()
        return pd.concat(
            [self.rating_to_flat_dataframe(rating, path) for rating, path in records],
            ignore_index=False,
        ).sort_index()

    def long_table_to_wide(self, long_df: pd.DataFrame) -> pd.DataFrame:
        if long_df.empty:
            return pd.DataFrame()

        # P3-A / F-AGG-4: assert identity uniqueness BEFORE pivot. pivot_table
        # with aggfunc='first' would silently collapse duplicate
        # (ezqcid, module_name, rater) tuples, hiding a broken F-RAT-3 invariant
        # (one file per identity). Fail loud with the offending identities.
        dup_keys = ["ezqcid", "module_name", "rater"]
        duplicates = long_df[long_df.duplicated(subset=dup_keys, keep=False)]
        if not duplicates.empty:
            offenders = (
                duplicates[dup_keys].drop_duplicates()
                .astype(str)
                .agg("/".join, axis=1)
                .tolist()
            )
            raise ValueError(
                f"重复的评分身份(ezqcid/module/rater),F-RAT-3 不变量被破坏: {offenders[:5]}"
            )

        wide = long_df.pivot_table(index="ezqcid", columns=["module_name", "rater"], aggfunc="first").reset_index()
        new_columns = []
        for col in wide.columns:
            if col == "ezqcid" or (isinstance(col, tuple) and col[0] == "ezqcid"):
                new_columns.append("ezqcid")
            else:
                new_columns.append(f"{col[1]}.{col[2]}.{col[0]}")
        wide.columns = new_columns
        wide["ezqcid"] = wide["ezqcid"].astype(str)
        return wide

    def merge_subjects_with_rating_wide(self, rating_wide: pd.DataFrame, subjects: pd.DataFrame) -> pd.DataFrame:
        if rating_wide.empty:
            return subjects.copy()

        # BUG-4 / F-IMP-5: ezqcid is THE join key and must be string on BOTH
        # sides. The rating side is already coerced in long_table_to_wide, but
        # subjects read from CSV can carry numeric-looking IDs as int64, which
        # makes pandas raise (or silently NaN) on the merge. Coerce here so the
        # join is identity-stable regardless of how each side was loaded.
        subjects = subjects.copy()
        if "ezqcid" in subjects.columns:
            subjects["ezqcid"] = subjects["ezqcid"].astype(str)
        if "ezqcid" in rating_wide.columns:
            rating_wide = rating_wide.copy()
            rating_wide["ezqcid"] = rating_wide["ezqcid"].astype(str)

        result = pd.merge(subjects, rating_wide, on="ezqcid", how="left")
        result = result.drop(columns=[col for col in result.columns if ".code" in col or ".code_exe" in col])

        score_cols = [col for col in result.columns if re.search(r"\.score\d+$", col) and col != "ezqcid"]
        tag_cols = [col for col in result.columns if re.search(r"\.tag\d+$", col) and col != "ezqcid"]
        notes_cols = [col for col in result.columns if re.search(r"\.notes\d+$", col) and col != "ezqcid"]
        other_cols = [
            col
            for col in result.columns
            if col != "ezqcid"
            and not re.search(r"\.score\d+$", col)
            and not re.search(r"\.tag\d+$", col)
            and not re.search(r"\.notes\d+$", col)
        ]

        return result[["ezqcid"] + score_cols + tag_cols + notes_cols + other_cols]

    def rating_to_flat_dataframe(self, rating: Rating, path: Path | None = None) -> pd.DataFrame:
        data = rating.to_legacy_dict()
        flattened: dict[str, Any] = {}

        for key, value in data.items():
            if key not in ["scores", "tags", "code_exe"]:
                flattened[key] = value

        if isinstance(data.get("code_exe"), dict):
            flattened["code_exe"] = ""
            for code_value in data["code_exe"].values():
                if isinstance(code_value, dict):
                    for sub_value in code_value.values():
                        flattened["code_exe"] = f"{flattened['code_exe']}; {sub_value};"
                else:
                    flattened["code_exe"] = code_value

        for score_key, score_value in data.get("scores", {}).items():
            if isinstance(score_value, dict):
                for sub_key, sub_value in score_value.items():
                    suffix = "" if sub_key == "value" else sub_key
                    flattened[f"score{score_key}{suffix}"] = sub_value
            else:
                flattened[f"score{score_key}"] = score_value

        for tag_key, tag_value in data.get("tags", {}).items():
            if isinstance(tag_value, dict):
                for sub_key, sub_value in tag_value.items():
                    suffix = "" if sub_key == "value" else sub_key
                    flattened[f"tag{tag_key}{suffix}"] = sub_value
            else:
                flattened[f"tag{tag_key}"] = tag_value

        if path is not None:
            flattened["filename"] = path.name
            flattened["filepath"] = str(path)

        flattened["module_name"] = flattened.pop("name")
        return pd.DataFrame([flattened])


__all__ = ["LoadedRatingsState", "RatingService"]
