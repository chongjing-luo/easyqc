from __future__ import annotations

from pathlib import Path

import pytest

from core.application_paths import (
    ApplicationPathError,
    resolve_application_state_root,
)


def test_source_checkout_keeps_installation_scoped_state_beside_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()

    result = resolve_application_state_root(
        source_root=source,
        version="1.0.0",
        frozen=False,
        environment={},
        user_data_root=tmp_path / "unused",
    )

    assert result == source.resolve()
    assert list(tmp_path.iterdir()) == [source]


def test_frozen_application_uses_external_per_user_versioned_state(
    tmp_path: Path,
) -> None:
    source = tmp_path / "read-only app"
    source.mkdir()
    user_data = tmp_path / "user data" / "EasyQC"

    result = resolve_application_state_root(
        source_root=source,
        version="1.2.3",
        frozen=True,
        environment={},
        user_data_root=user_data,
    )

    assert result == user_data.resolve() / "native-1.2.3"
    assert not result.exists()
    assert source not in result.parents


def test_explicit_absolute_data_root_is_available_for_managed_deployments(
    tmp_path: Path,
) -> None:
    override = tmp_path / "controlled state"

    result = resolve_application_state_root(
        source_root=tmp_path / "source",
        version="1.0.0",
        frozen=True,
        environment={"EASYQC_DATA_ROOT": str(override)},
        user_data_root=tmp_path / "ignored",
    )

    assert result == override.resolve()
    assert not result.exists()


@pytest.mark.parametrize(
    ("version", "environment", "message"),
    [
        ("v1.0.0", {}, "version"),
        ("1.0.0", {"EASYQC_DATA_ROOT": "relative/path"}, "absolute"),
        ("1.0.0", {"EASYQC_DATA_ROOT": "/tmp/../tmp/easyqc"}, "canonical"),
    ],
)
def test_application_state_root_rejects_ambiguous_identity_or_override(
    tmp_path: Path,
    version: str,
    environment: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ApplicationPathError, match=message):
        resolve_application_state_root(
            source_root=tmp_path,
            version=version,
            frozen=True,
            environment=environment,
            user_data_root=tmp_path / "user data",
        )
