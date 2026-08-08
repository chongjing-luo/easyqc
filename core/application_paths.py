"""Resolve writable EasyQC application state without changing project paths."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import re

from platformdirs import PlatformDirs


_VERSION = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9][A-Za-z0-9.-]*)?$"
)
_DATA_ROOT_ENV = "EASYQC_DATA_ROOT"


class ApplicationPathError(ValueError):
    """Raised when application-state location cannot be resolved safely."""


def resolve_application_state_root(
    *,
    source_root: str | os.PathLike[str],
    version: str,
    frozen: bool,
    environment: Mapping[str, str] | None = None,
    user_data_root: str | os.PathLike[str] | None = None,
) -> Path:
    """Return the non-project state root for one EasyQC installation.

    Source checkouts preserve the established installation-scoped files beside
    ``easyqc.py``.  A frozen application may live in read-only ``/opt``,
    ``Program Files`` or an application bundle, so it uses a per-user,
    version-isolated directory.  The function only resolves a path; creation is
    deferred to the services that actually write state.
    """

    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        raise ApplicationPathError("application version is invalid")
    active_environment = os.environ if environment is None else environment
    override = active_environment.get(_DATA_ROOT_ENV)
    if override is not None:
        return _canonical_absolute_override(override)

    source = Path(source_root).resolve(strict=False)
    if not frozen:
        return source

    if user_data_root is None:
        base = Path(
            PlatformDirs("EasyQC", appauthor=False).user_data_path
        ).resolve(strict=False)
    else:
        base = Path(user_data_root).resolve(strict=False)
    if not base.is_absolute():
        raise ApplicationPathError("per-user data root must be absolute")
    return base / f"native-{version}"


def _canonical_absolute_override(value: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ApplicationPathError(f"{_DATA_ROOT_ENV} must be a non-empty path")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ApplicationPathError(f"{_DATA_ROOT_ENV} must be absolute")
    resolved = candidate.resolve(strict=False)
    if os.path.normcase(str(candidate)) != os.path.normcase(str(resolved)):
        raise ApplicationPathError(f"{_DATA_ROOT_ENV} must be canonical")
    return resolved


__all__ = [
    "ApplicationPathError",
    "resolve_application_state_root",
]
