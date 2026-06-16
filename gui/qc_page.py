from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from core.code_executor import CodeExecutor
from core.rating_service import RatingService
from models.rating import Rating


@dataclass
class QCPageRuntimeContext:
    """Runtime state boundary for the legacy QC page."""

    settings: dict[str, Any]
    tables: dict[str, Any]
    output_dir: str | Path | None = None
    module_rater_dir: str | None = None
    legacy_dt: Any = field(default=None, repr=False)

    @classmethod
    def from_legacy_dt(cls, dt: Any) -> "QCPageRuntimeContext":
        return cls(
            settings=getattr(dt, "settings", {}) or {},
            tables=getattr(dt, "tab", {}) or {},
            output_dir=getattr(dt, "output_dir", None),
            module_rater_dir=getattr(dt, "dir_module_rater", None),
            legacy_dt=dt,
        )

    @classmethod
    def from_gui_state(cls, gui_state: Any) -> "QCPageRuntimeContext":
        return cls.from_legacy_dt(getattr(gui_state, "dt", None))

    @classmethod
    def from_project_service(
        cls, project_service: Any, tables: dict[str, Any] | None = None
    ) -> "QCPageRuntimeContext":
        """P2-CLI: build a runtime context directly from ProjectService (no
        ProjectManager / LegacyGUIStateAdapter / DataContainer). Used by the
        CLI 4-arg path. ``tables`` is the session-state result dict (may be
        empty for CLI, which only needs settings + output_dir)."""
        current = project_service.current_project
        return cls(
            settings=project_service.settings,
            tables=tables or {},
            output_dir=str(current.path) if current is not None else None,
            module_rater_dir=None,
            legacy_dt=None,
        )

    def set_module_rater_dir(self, path: str | Path) -> str:
        self.module_rater_dir = str(path)
        if self.legacy_dt is not None:
            self.legacy_dt.dir_module_rater = self.module_rater_dir
        return self.module_rater_dir


class QCPageController:
    def __init__(self, rating_service: RatingService | None = None, code_executor: CodeExecutor | None = None):
        self.rating_service = rating_service
        self.code_executor = code_executor or CodeExecutor()

    def current_module(self, settings: dict, module_index: str) -> dict:
        return settings["qcmodule"][module_index]

    def set_current_module(self, settings: dict, module_index: str, module: dict) -> None:
        settings["qcmodule"][module_index] = module

    def module_index_by_name(self, settings: dict, module_name: str) -> str | None:
        return next((i for i, module in settings["qcmodule"].items() if module["name"] == module_name), None)

    def module_rater(self, module: dict) -> str | None:
        return module.get("rater")

    def module_rater_dir(self, output_dir: str | Path, module_name: str, rater: str) -> str:
        return str(Path(output_dir) / "RatingFiles" / module_name / rater)

    def set_subject(self, module: dict, ezqcid: str | None) -> None:
        module["ezqcid"] = ezqcid

    def ensure_module_table(self, tables: dict, module_name: str):
        if module_name not in tables or tables[module_name] is None:
            tables[module_name] = tables.get("ezqc_qctable")
            if tables[module_name] is None:
                tables[module_name] = tables.get("ezqc_all")
        return tables.get(module_name)

    def table_has_rows(self, table) -> bool:
        if table is None:
            return False
        return not (hasattr(table, "empty") and table.empty)

    def module_subject_rows(self, tables: dict, module_name: str) -> pd.DataFrame:
        table = tables.get(module_name)
        if table is None or "ezqcid" not in table.columns:
            return pd.DataFrame(columns=["ezqcid"])
        return table[["ezqcid"]].drop_duplicates().sort_values("ezqcid")

    def first_subject_id(self, tables: dict, module_name: str) -> str | None:
        rows = self.module_subject_rows(tables, module_name)
        if rows.empty:
            return None
        return rows["ezqcid"].tolist()[0]

    def subject_exists(self, tables: dict, module_name: str, ezqcid: str) -> bool:
        table = tables.get(module_name)
        if table is None or "ezqcid" not in table.columns:
            return False
        return ezqcid in table["ezqcid"].values

    def module_table(self, tables: dict, module_name: str):
        return tables.get(module_name)

    def set_score_value(self, module: dict, score_key: str, value) -> None:
        module["scores"][score_key]["value"] = value

    def set_tag_value(self, module: dict, tag_key: str, value) -> None:
        module["tags"][tag_key]["value"] = value

    def set_notes(self, module: dict, notes: str) -> None:
        module["notes"] = notes

    def set_code_execution(self, module: dict, code_exe: dict[int, str] | None) -> None:
        module["code_exe"] = code_exe

    def reset_rating_state(self, module: dict, ezqcid: str) -> dict:
        for score in module.get("scores", {}).values():
            score["value"] = None
        for tag in module.get("tags", {}).values():
            tag["value"] = False
        module["code_exe"] = None
        module["ezqcid"] = ezqcid
        module["time"] = None
        module["notes"] = None
        return module

    def apply_rating_state(self, current_module: dict, rating_module: dict) -> dict:
        current_module["ezqcid"] = rating_module.get("ezqcid", current_module.get("ezqcid"))
        current_module["time"] = rating_module.get("time")
        current_module["notes"] = rating_module.get("notes")
        current_module["code_exe"] = rating_module.get("code_exe")

        for key, score in current_module.get("scores", {}).items():
            rating_score = rating_module.get("scores", {}).get(key)
            if isinstance(rating_score, dict) and "value" in rating_score:
                score["value"] = rating_score.get("value")

        for key, tag in current_module.get("tags", {}).items():
            rating_tag = rating_module.get("tags", {}).get(key)
            if isinstance(rating_tag, dict) and "value" in rating_tag:
                tag["value"] = rating_tag.get("value")

        return current_module

    def find_rating_compatibility_issues(self, current_module: dict, rating_module: dict) -> list[tuple[str, str]]:
        """Surface schema drift between the current module and a saved rating.

        Never raises KeyError: a saved rating that lacks (or has extra) score/tag
        keys vs the current module is reported as an explicit issue, so load-time
        drift does not crash the GUI (BUG-2, F-RAT-7, F-SET-7).

        Issue kinds: ("score", key) / ("score_missing", key) /
                     ("tag", key) / ("tag_missing", key).
        """
        issues: list[tuple[str, str]] = []
        rating_scores = rating_module.get("scores", {}) or {}
        for key, score in (current_module.get("scores", {}) or {}).items():
            rating_score = rating_scores.get(key)
            if not isinstance(rating_score, dict):
                issues.append(("score_missing", key))
            elif score.get("num_") != rating_score.get("num_"):
                issues.append(("score", key))

        rating_tags = rating_module.get("tags", {}) or {}
        for key, tag in (current_module.get("tags", {}) or {}).items():
            rating_tag = rating_tags.get(key)
            if not isinstance(rating_tag, dict):
                issues.append(("tag_missing", key))
            elif tag.get("label") != rating_tag.get("label"):
                issues.append(("tag", key))

        return issues

    def save_legacy_module_rating(self, module: dict, module_rater_dir: str | Path) -> Path:
        module["time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rating = Rating.from_legacy_dict(module)
        return RatingService.save_rating_to_rater_dir(Path(module_rater_dir), rating, module)

    def load_legacy_module_rating(
        self,
        module: dict,
        module_rater_dir: str | Path,
        ezqcid: str,
        rater: str,
    ) -> tuple[list[Path], dict | None]:
        rating_files = RatingService.find_rating_files_in_rater_dir(
            Path(module_rater_dir),
            module["name"],
            ezqcid,
            rater,
        )
        if len(rating_files) != 1:
            return rating_files, None
        return rating_files, RatingService.load_legacy_rating_file(rating_files[0])

    def load_first_legacy_module_rating(
        self,
        module: dict,
        module_rater_dir: str | Path,
        ezqcid: str,
        rater: str,
    ) -> tuple[list[Path], dict | None]:
        rating_files = RatingService.find_rating_files_in_rater_dir(
            Path(module_rater_dir),
            module["name"],
            ezqcid,
            rater,
        )
        if not rating_files:
            return rating_files, None
        return rating_files, RatingService.load_legacy_rating_file(rating_files[0])

    def generate_code(
        self,
        ezqcid: str,
        settings: dict,
        module: dict,
        table: pd.DataFrame,
    ) -> tuple[str, dict[int, str]]:
        code_vars = table.loc[table["ezqcid"] == ezqcid].to_dict("records")[0]
        code_vars = {**code_vars, **settings["constants"]}
        code = self.code_executor.parse_template(module["code"], code_vars)

        if code.startswith("MULTICMD"):
            code = code.replace("MULTICMD", "", 1).strip()
            commands = [cmd.strip() for cmd in code.split(";|") if cmd.strip()]
            code_exe = {i: cmd for i, cmd in enumerate(commands)}
        elif "MULTICMD" in code:
            code_parts = code.split("MULTICMD", 1)
            code_pre = code_parts[0].strip()
            if not code_pre.endswith(";"):
                code_pre += ";"
            code_pos = code_parts[1].strip() if len(code_parts) > 1 else ""
            commands = [cmd.strip() for cmd in code_pos.split(";|") if cmd.strip()]
            code_exe = {i: code_pre + cmd for i, cmd in enumerate(commands)}
        else:
            code_exe = {0: code}

        return code, code_exe


class QCPage:
    def __init__(self, parent, controller: QCPageController):
        self.parent = parent
        self.controller = controller


__all__ = ["QCPage", "QCPageController", "QCPageRuntimeContext"]
