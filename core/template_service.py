"""Installation-scoped constant, module, and command-setting templates."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from core.module_repository import (
    ModuleCatalogSnapshot,
    ModuleRecord,
    ModuleRepository,
    ModuleRepositoryError,
)
from models.qcmodule import QCModule
from utils.file_utils import FileUtils


TEMPLATE_SCHEMA_VERSION = 1


class TemplateServiceError(ValueError):
    """Raised when installation template state is invalid or conflicts."""


class TemplateService:
    """Own reusable templates for exactly one EasyQC installation root."""

    def __init__(self, application_root: str | Path) -> None:
        self.application_root = Path(application_root)
        self.constant_path = self.application_root / "constant_templates.json"
        self.settings_path = self.application_root / "app_settings.json"
        self.module_repository = ModuleRepository(
            self.application_root / "modules",
            scope="template",
        )

    def constants(self) -> dict[str, Any]:
        """Return a detached constant-template mapping."""

        if not self.constant_path.exists():
            return {}
        payload = self._load_object(self.constant_path)
        constants = payload.get("constants")
        if not isinstance(constants, dict):
            raise TemplateServiceError(
                f"constants must be an object: {self.constant_path}"
            )
        return deepcopy(constants)

    def constant(self, name: str) -> Any:
        """Return one detached constant-template value by exact name."""

        normalized = self._constant_name(name)
        constants = self.constants()
        if normalized not in constants:
            raise TemplateServiceError(
                f"unknown constant template: {normalized}"
            )
        return deepcopy(constants[normalized])

    def set_constant(
        self,
        name: str,
        value: Any,
        *,
        old_name: str | None = None,
    ) -> None:
        """Atomically add or edit one installation constant template."""

        normalized = self._constant_name(name)
        constants = self.constants()
        if old_name is None and normalized in constants:
            raise TemplateServiceError(
                f"constant template already exists: {normalized}"
            )
        if old_name is not None:
            normalized_old = self._constant_name(old_name)
            if normalized_old != normalized and normalized in constants:
                raise TemplateServiceError(
                    f"constant template already exists: {normalized}"
                )
            constants.pop(normalized_old, None)
        constants[normalized] = deepcopy(value)
        FileUtils.safe_json_save(
            self.constant_path,
            {
                "schema_version": TEMPLATE_SCHEMA_VERSION,
                "constants": constants,
            },
        )

    def delete_constant(self, name: str) -> bool:
        """Delete one constant template without touching any project copy."""

        normalized = self._constant_name(name)
        constants = self.constants()
        if normalized not in constants:
            return False
        del constants[normalized]
        FileUtils.safe_json_save(
            self.constant_path,
            {
                "schema_version": TEMPLATE_SCHEMA_VERSION,
                "constants": constants,
            },
        )
        return True

    def modules(self) -> ModuleCatalogSnapshot:
        """Return valid module templates plus path-scoped catalog errors."""

        return self.module_repository.snapshot()

    def module(self, module_id: str) -> ModuleRecord:
        """Return one detached module template by stable ID."""

        try:
            return self.module_repository.load(module_id)
        except ModuleRepositoryError as exc:
            raise TemplateServiceError(str(exc)) from exc

    def add_module(
        self,
        module: QCModule,
        *,
        display_order: int,
    ) -> ModuleRecord:
        """Create and atomically persist one new module template."""

        snapshot = self.modules()
        self._require_clean_modules(snapshot)
        if any(record.module.name == module.name for record in snapshot.records):
            raise TemplateServiceError(
                f"module template already exists: {module.name}"
            )
        record = ModuleRecord.create(
            module,
            scope="template",
            display_order=display_order,
        )
        try:
            return self.module_repository.save(record)
        except ModuleRepositoryError as exc:
            raise TemplateServiceError(str(exc)) from exc

    def save_module(self, record: ModuleRecord) -> ModuleRecord:
        """Atomically save one existing or detached template record."""

        if record.scope != "template":
            raise TemplateServiceError("template module scope must be 'template'")
        try:
            return self.module_repository.save(record)
        except ModuleRepositoryError as exc:
            raise TemplateServiceError(str(exc)) from exc

    def delete_module(self, module_id: str) -> bool:
        """Delete one template module without touching copied project modules."""

        try:
            return self.module_repository.delete(module_id)
        except ModuleRepositoryError as exc:
            raise TemplateServiceError(str(exc)) from exc

    def import_module(self, path: str | Path) -> ModuleRecord:
        """Import one legacy or schema-versioned module as a detached template."""

        source_path = Path(path)
        try:
            payload = FileUtils.safe_json_load(source_path)
        except (OSError, ValueError, TypeError) as exc:
            raise TemplateServiceError(f"{source_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise TemplateServiceError(
                f"module import must contain one object: {source_path}"
            )
        try:
            if "schema_version" in payload or "module" in payload:
                source = ModuleRecord.from_json_object(
                    payload,
                    expected_scope="template",
                )
                module = source.module
                display_order = source.display_order
            else:
                module = QCModule.from_legacy_dict(deepcopy(payload))
                snapshot = self.modules()
                self._require_clean_modules(snapshot)
                display_order = (
                    max(
                        (
                            record.display_order
                            for record in snapshot.records
                        ),
                        default=0,
                    )
                    + 10
                )
            return self.add_module(module, display_order=display_order)
        except (KeyError, TypeError, ValueError, ModuleRepositoryError) as exc:
            if isinstance(exc, TemplateServiceError):
                raise
            raise TemplateServiceError(f"{source_path}: {exc}") from exc

    def export_module(self, module_id: str, path: str | Path) -> Path:
        """Atomically export one complete schema-versioned template record."""

        target = Path(path)
        record = self.module(module_id)
        try:
            FileUtils.safe_json_save(target, record.to_json_object())
        except (OSError, ValueError, TypeError) as exc:
            raise TemplateServiceError(f"{target}: {exc}") from exc
        return target

    def shell_enabled(self) -> bool:
        """Return this installation's viewer Shell choice; default is false."""

        if not self.settings_path.exists():
            return False
        payload = self._load_object(self.settings_path)
        viewer = payload.get("viewer_execution")
        if not isinstance(viewer, dict) or not isinstance(viewer.get("shell"), bool):
            raise TemplateServiceError(
                f"viewer_execution.shell must be boolean: {self.settings_path}"
            )
        return viewer["shell"]

    def set_shell_enabled(self, enabled: bool) -> None:
        """Atomically persist this installation's viewer Shell choice."""

        if not isinstance(enabled, bool):
            raise TypeError("enabled must be bool")
        FileUtils.safe_json_save(
            self.settings_path,
            {
                "schema_version": TEMPLATE_SCHEMA_VERSION,
                "viewer_execution": {"shell": enabled},
            },
        )

    def _load_object(self, path: Path) -> dict[str, Any]:
        try:
            payload = FileUtils.safe_json_load(path)
        except (OSError, ValueError, TypeError) as exc:
            raise TemplateServiceError(f"{path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise TemplateServiceError(f"template file must be an object: {path}")
        if payload.get("schema_version") != TEMPLATE_SCHEMA_VERSION:
            raise TemplateServiceError(
                f"unsupported schema_version in {path}: "
                f"{payload.get('schema_version')!r}"
            )
        return payload

    @staticmethod
    def _constant_name(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("constant template name must be str")
        name = value.strip()
        if not name or not name.isidentifier():
            raise TemplateServiceError(
                f"invalid constant template name: {name!r}"
            )
        return name

    @staticmethod
    def _require_clean_modules(snapshot: ModuleCatalogSnapshot) -> None:
        if snapshot.errors:
            raise TemplateServiceError(
                "module template catalog contains errors: "
                + "; ".join(error.message for error in snapshot.errors)
            )


__all__ = [
    "TEMPLATE_SCHEMA_VERSION",
    "TemplateService",
    "TemplateServiceError",
]
