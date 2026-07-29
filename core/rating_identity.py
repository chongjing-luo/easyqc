"""Portable identity and path codec for current rating snapshots.

The canonical target filename is limited to 240 UTF-8 bytes, below the
255-byte component limit common to the supported filesystems. The absolute
target path is limited to 240 UTF-16 code units, leaving margin below the
classic Windows ``MAX_PATH`` boundary. Both checks are lexical and happen
before any filesystem operation.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn


MODULE_NAME_MAX_LENGTH = 32
RATER_MAX_LENGTH = 32
EASYQCID_MAX_LENGTH = 128

PORTABLE_FILENAME_MAX_UTF8_BYTES = 240
PORTABLE_PATH_MAX_UTF16_UNITS = 240

_MODULE_OR_RATER_PATTERN = re.compile(
    rf"^[A-Za-z0-9_]{{1,{MODULE_NAME_MAX_LENGTH}}}$"
)
_EASYQCID_PATTERN = re.compile(rf"^[A-Za-z0-9_.-]{{1,{EASYQCID_MAX_LENGTH}}}$")
_WINDOWS_DEVICE_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        "CONIN$",
        "CONOUT$",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)
_OBSERVATION_RATER_SENTINEL = "__observation_no_rater__"


class RatingIdentityCodecError(ValueError):
    """Base class for user-correctable rating identity codec errors."""


class RatingIdentityError(RatingIdentityCodecError):
    """An identity field does not satisfy the portable identifier policy."""

    def __init__(self, field: str, value: Any, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"Invalid rating {field} {value!r}: {reason}")


class RatingFilenameError(RatingIdentityCodecError):
    """A filename cannot be decoded as one canonical rating identity."""

    def __init__(self, filename: Any, reason: str) -> None:
        self.filename = filename
        self.reason = reason
        super().__init__(f"Invalid canonical rating filename {filename!r}: {reason}")


class RatingPathError(RatingIdentityCodecError):
    """A canonical rating path is not portable."""

    def __init__(self, path: Any, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Invalid canonical rating path {str(path)!r}: {reason}")


class RatingPathBudgetError(RatingPathError):
    """A canonical rating path exceeds an explicit portability budget."""

    def __init__(
        self,
        path: Path,
        *,
        budget: str,
        actual: int,
        limit: int,
    ) -> None:
        self.budget = budget
        self.actual = actual
        self.limit = limit
        super().__init__(
            path,
            f"{budget} is {actual}, exceeding the portable limit of {limit}",
        )


def _raise_identifier_error(field: str, value: Any, reason: str) -> NoReturn:
    raise RatingIdentityError(field, value, reason)


def _validate_module_or_rater(field: str, value: Any) -> None:
    if not isinstance(value, str):
        _raise_identifier_error(field, value, "must be a string")
    if _MODULE_OR_RATER_PATTERN.fullmatch(value) is None:
        _raise_identifier_error(
            field,
            value,
            "must contain 1-32 ASCII letters, digits, or underscores",
        )
    if value.upper() in _WINDOWS_DEVICE_NAMES:
        _raise_identifier_error(field, value, "is a reserved Windows device name")


def _validate_easyqcid(value: Any) -> None:
    if not isinstance(value, str):
        _raise_identifier_error("easyqcid", value, "must be a string")
    if _EASYQCID_PATTERN.fullmatch(value) is None:
        _raise_identifier_error(
            "easyqcid",
            value,
            "must contain 1-128 ASCII letters, digits, underscores, periods, or hyphens",
        )
    if value in {".", ".."}:
        _raise_identifier_error(
            "easyqcid",
            value,
            "cannot be a current-directory or parent-directory marker",
        )


@dataclass(frozen=True, slots=True)
class RatingIdentity:
    """Exact stable identity of one current rating snapshot."""

    module_name: str
    rater: str
    easyqcid: str

    def __post_init__(self) -> None:
        validate_module_name(self.module_name)
        validate_rater(self.rater)
        validate_easyqcid(self.easyqcid)


def validate_module_name(value: str) -> str:
    """Validate and return one project module's exact internal name."""

    _validate_module_or_rater("module_name", value)
    return value


def validate_rater(value: str) -> str:
    """Validate and return one exact rater ID, excluding internal sentinels."""

    _validate_module_or_rater("rater", value)
    if value.casefold() == _OBSERVATION_RATER_SENTINEL.casefold():
        _raise_identifier_error(
            "rater",
            value,
            "is reserved for internal observation records",
        )
    return value


def validate_easyqcid(value: str) -> str:
    """Validate and return one exact QC-list row identity."""

    _validate_easyqcid(value)
    return value


def validate_rating_identity(
    module_name: str,
    rater: str,
    easyqcid: str,
) -> RatingIdentity:
    """Validate three strings and return their immutable exact identity.

    Side effects: none.
    Errors: :class:`RatingIdentityError` names the invalid field and reason.
    """

    return RatingIdentity(module_name=module_name, rater=rater, easyqcid=easyqcid)


def _validate_filename_budget(filename: str) -> None:
    actual = len(filename.encode("utf-8"))
    if actual > PORTABLE_FILENAME_MAX_UTF8_BYTES:
        raise RatingFilenameError(
            filename,
            (
                f"UTF-8 size is {actual} bytes, exceeding the portable "
                f"component limit of {PORTABLE_FILENAME_MAX_UTF8_BYTES}"
            ),
        )


def build_rating_filename(identity: RatingIdentity) -> str:
    """Encode one validated identity as its canonical basename.

    Side effects: none.
    Errors: ``TypeError`` for programmer misuse and ``RatingFilenameError`` if
    the explicit portable filename budget is exceeded.
    """

    if not isinstance(identity, RatingIdentity):
        raise TypeError("identity must be a RatingIdentity")
    filename = f"{identity.module_name}-{identity.rater}-{identity.easyqcid}.json"
    _validate_filename_budget(filename)
    return filename


def parse_rating_filename(filename: str) -> RatingIdentity:
    """Decode a canonical basename by removing one suffix and splitting twice.

    Additional hyphens are retained in ``easyqcid``. Directory-bearing input is
    rejected so callers cannot accidentally treat a path as a basename.

    Side effects: none.
    Errors: :class:`RatingFilenameError`.
    """

    if not isinstance(filename, str):
        raise RatingFilenameError(filename, "must be a string basename")
    if "/" in filename or "\\" in filename:
        raise RatingFilenameError(filename, "must be a basename without directories")
    _validate_filename_budget(filename)
    if not filename.endswith(".json"):
        raise RatingFilenameError(filename, "must end with the exact suffix '.json'")

    identity_parts = filename[:-5].split("-", 2)
    if len(identity_parts) != 3 or any(part == "" for part in identity_parts):
        raise RatingFilenameError(
            filename,
            "must encode module_name, rater, and easyqcid separated by two hyphens",
        )

    try:
        return RatingIdentity(*identity_parts)
    except RatingIdentityError as exc:
        raise RatingFilenameError(
            filename,
            f"decoded {exc.field} is invalid: {exc.reason}",
        ) from exc


def _absolute_path_without_io(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _utf16_code_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def canonical_rating_path(
    rating_root: str | os.PathLike[str],
    identity: RatingIdentity,
) -> Path:
    """Return the canonical path below a project's ``RatingFiles`` root.

    The function performs lexical construction and validation only; it never
    creates directories or touches the filesystem.

    Side effects: none.
    Errors: ``TypeError`` for programmer misuse, ``RatingPathError`` for a
    non-portable root, and ``RatingPathBudgetError`` for an overlong absolute
    target path.
    """

    if not isinstance(identity, RatingIdentity):
        raise TypeError("identity must be a RatingIdentity")
    try:
        root = Path(rating_root)
    except (TypeError, ValueError) as exc:
        raise RatingPathError(rating_root, "rating_root must be a valid path") from exc
    if "\x00" in os.fspath(root):
        raise RatingPathError(root, "NUL characters are not portable")

    filename = build_rating_filename(identity)
    target = root / identity.module_name / identity.rater / filename
    absolute_target = _absolute_path_without_io(target)
    try:
        actual = _utf16_code_units(str(absolute_target))
    except UnicodeEncodeError as exc:
        raise RatingPathError(
            target,
            "contains a Unicode surrogate that cannot form a portable path",
        ) from exc
    if actual > PORTABLE_PATH_MAX_UTF16_UNITS:
        raise RatingPathBudgetError(
            target,
            budget="absolute path UTF-16 code units",
            actual=actual,
            limit=PORTABLE_PATH_MAX_UTF16_UNITS,
        )
    return target


__all__ = [
    "EASYQCID_MAX_LENGTH",
    "MODULE_NAME_MAX_LENGTH",
    "PORTABLE_FILENAME_MAX_UTF8_BYTES",
    "PORTABLE_PATH_MAX_UTF16_UNITS",
    "RATER_MAX_LENGTH",
    "RatingFilenameError",
    "RatingIdentity",
    "RatingIdentityCodecError",
    "RatingIdentityError",
    "RatingPathBudgetError",
    "RatingPathError",
    "build_rating_filename",
    "canonical_rating_path",
    "parse_rating_filename",
    "validate_easyqcid",
    "validate_module_name",
    "validate_rating_identity",
    "validate_rater",
]
