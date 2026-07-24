from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from core.managed_runtime import ManagedRuntimeError
from core.managed_runtime_platform import (
    HostPreflightSnapshot,
    detect_native_runtime_target,
    resolve_runtime_paths,
    run_preflight,
)
from models.managed_runtime import ReleaseManifestV1, RuntimeTargetV1


MIB = 1024 * 1024


def _target(target_id: str) -> RuntimeTargetV1:
    values = {
        "ubuntu-22.04-x86_64": ("linux", "22.04", "x86_64"),
        "ubuntu-24.04-x86_64": ("linux", "24.04", "x86_64"),
        "windows-11-x86_64": ("windows", "11", "x86_64"),
        "macos-13-arm64": ("macos", "13", "arm64"),
    }
    return RuntimeTargetV1(*values[target_id])


@pytest.mark.parametrize(
    ("os_name", "version", "distribution", "machine", "expected"),
    (
        ("linux", "22.04", "ubuntu", "AMD64", "ubuntu-22.04-x86_64"),
        ("linux", "24.04", "ubuntu", "x86_64", "ubuntu-24.04-x86_64"),
        ("windows", "11", None, "AMD64", "windows-11-x86_64"),
        ("macos", "15.4", None, "aarch64", "macos-13-arm64"),
    ),
)
def test_native_target_detection_selects_only_approved_platforms(
    monkeypatch: pytest.MonkeyPatch,
    os_name: str,
    version: str,
    distribution: str | None,
    machine: str,
    expected: str,
) -> None:
    monkeypatch.setattr("core.managed_runtime_platform._current_os_name", lambda: os_name)
    monkeypatch.setattr(
        "core.managed_runtime_platform._current_os_version",
        lambda _os_name: (version, distribution),
    )
    monkeypatch.setattr("core.managed_runtime_platform.platform.machine", lambda: machine)

    assert detect_native_runtime_target().target_id == expected


@pytest.mark.parametrize(
    ("os_name", "version", "distribution", "machine", "message"),
    (
        ("linux", "23.10", "ubuntu", "x86_64", "22.04 or 24.04"),
        ("windows", "10", None, "AMD64", "Windows 11 x86_64 only"),
    ),
)
def test_native_target_detection_rejects_nearby_unsupported_host(
    monkeypatch: pytest.MonkeyPatch,
    os_name: str,
    version: str,
    distribution: str | None,
    machine: str,
    message: str,
) -> None:
    monkeypatch.setattr(
        "core.managed_runtime_platform._current_os_name",
        lambda: os_name,
    )
    monkeypatch.setattr(
        "core.managed_runtime_platform._current_os_version",
        lambda _os_name: (version, distribution),
    )
    monkeypatch.setattr(
        "core.managed_runtime_platform.platform.machine",
        lambda: machine,
    )

    with pytest.raises(ManagedRuntimeError, match=message):
        detect_native_runtime_target()


def _manifest(
    target: RuntimeTargetV1 | None = None,
    *,
    required_free_bytes: int = 300 * MIB,
) -> ReleaseManifestV1:
    target = target or _target("ubuntu-22.04-x86_64")
    python_key = {
        "ubuntu-22.04-x86_64": "cpython-3.13.13-linux-x86_64-gnu",
        "ubuntu-24.04-x86_64": "cpython-3.13.13-linux-x86_64-gnu",
        "windows-11-x86_64": "cpython-3.13.13-windows-x86_64-none",
        "macos-13-arm64": "cpython-3.13.13-darwin-aarch64-none",
    }[target.target_id]
    digest = "1" * 64
    return ReleaseManifestV1.from_json_object(
        {
            "schema_version": 1,
            "release_id": f"easyqc-1.0.0-{target.os}",
            "channel": "stable",
            "target": target.to_json_object(),
            "easyqc": {
                "version": "1.0.0",
                "source_filename": "easyqc-source.tar.gz",
                "source_sha256": digest,
                "entrypoint": "easyqc.py",
            },
            "uv": {
                "version": "0.11.29",
                "filename": "uv.exe" if target.os == "windows" else "uv",
                "size_bytes": 1,
                "sha256": "2" * 64,
            },
            "python": {
                "version": "3.13.13",
                "build": "20260720",
                "key": python_key,
                "filename": "python.tar.zst",
                "size_bytes": 1,
                "sha256": "3" * 64,
            },
            "lock": {
                "filename": "requirements.lock",
                "sha256": "4" * 64,
                "require_hashes": True,
            },
            "artifacts": [
                {
                    "kind": "easyqc-source",
                    "filename": "easyqc-source.tar.gz",
                    "size_bytes": 1,
                    "sha256": digest,
                },
                {
                    "kind": "lock",
                    "filename": "requirements.lock",
                    "size_bytes": 1,
                    "sha256": "4" * 64,
                },
                {
                    "kind": "notice",
                    "filename": "NOTICE.txt",
                    "size_bytes": 1,
                    "sha256": "5" * 64,
                },
            ],
            "sources": [],
            "notices": [
                {"filename": "NOTICE.txt", "sha256": "5" * 64}
            ],
            "expanded_version_bytes": 1,
            "required_free_bytes": required_free_bytes,
            "smoke_contract_version": 1,
        }
    )


@pytest.mark.parametrize(
    ("target_id", "scope", "user_root", "environment", "expected_root"),
    (
        (
            "ubuntu-22.04-x86_64",
            "user",
            "/home/测试 User/.local/share/easyqc",
            {},
            "/home/测试 User/.local/share/easyqc",
        ),
        ("ubuntu-24.04-x86_64", "system", None, {}, "/opt/easyqc"),
        (
            "windows-11-x86_64",
            "user",
            r"C:\Users\测试 User\AppData\Local\EasyQC",
            {},
            r"C:\Users\测试 User\AppData\Local\EasyQC",
        ),
        (
            "windows-11-x86_64",
            "system",
            None,
            {"ProgramFiles": r"C:\Program Files"},
            r"C:\Program Files\EasyQC",
        ),
        (
            "macos-13-arm64",
            "user",
            "/Users/测试 User/Library/Application Support/EasyQC",
            {},
            "/Users/测试 User/Library/Application Support/EasyQC",
        ),
        (
            "macos-13-arm64",
            "system",
            None,
            {},
            "/Library/Application Support/EasyQC",
        ),
    ),
)
def test_platform_roots_match_the_approved_target_scope_table(
    target_id: str,
    scope: str,
    user_root: str | None,
    environment: dict[str, str],
    expected_root: str,
) -> None:
    paths = resolve_runtime_paths(
        _target(target_id),
        scope,
        user_data_root=user_root,
        environment=environment,
    )

    pure_path = (
        PureWindowsPath(expected_root)
        if target_id.startswith("windows")
        else PurePosixPath(expected_root)
    )
    assert paths.install_root == expected_root
    assert paths.state_root == str(pure_path / "state")
    assert paths.transaction_lock == str(
        pure_path / "state" / "transaction.lock"
    )
    assert paths.scope == scope
    assert paths.target.target_id == target_id


@pytest.mark.parametrize(
    ("target_id", "scope", "user_root", "environment", "message"),
    (
        ("ubuntu-22.04-x86_64", "other", "/tmp/easyqc", {}, "scope"),
        ("ubuntu-22.04-x86_64", "user", "relative/easyqc", {}, "absolute"),
        (
            "ubuntu-22.04-x86_64",
            "user",
            "/tmp/easyqc/../escape",
            {},
            "canonical",
        ),
        ("ubuntu-22.04-x86_64", "user", "/", {}, "filesystem root"),
        (
            "windows-11-x86_64",
            "user",
            r"C:\EasyQC\..\escape",
            {},
            "canonical",
        ),
        ("windows-11-x86_64", "user", "C:\\", {}, "filesystem root"),
        ("windows-11-x86_64", "system", None, {}, "ProgramFiles"),
        (
            "windows-11-x86_64",
            "system",
            None,
            {"ProgramFiles": r"relative\Program Files"},
            "absolute",
        ),
    ),
)
def test_platform_roots_fail_loud_on_invalid_scope_or_anchor(
    target_id: str,
    scope: str,
    user_root: str | None,
    environment: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ManagedRuntimeError, match=message):
        resolve_runtime_paths(
            _target(target_id),
            scope,
            user_data_root=user_root,
            environment=environment,
        )


def test_default_user_root_uses_platformdirs_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = PurePosixPath("/home/测试 User/.local/share/easyqc")

    class FakePlatformDirs:
        def __init__(self, appname: str, *, appauthor: bool) -> None:
            assert appname == "easyqc"
            assert appauthor is False
            self.user_data_path = expected

    monkeypatch.setattr(
        "core.managed_runtime_platform.PlatformDirs",
        FakePlatformDirs,
    )
    monkeypatch.setattr(
        "core.managed_runtime_platform._current_os_name",
        lambda: "linux",
    )

    paths = resolve_runtime_paths(_target("ubuntu-22.04-x86_64"), "user")

    assert paths.install_root == str(expected)


def _passing_snapshot(*, free_bytes: int = 500 * MIB) -> HostPreflightSnapshot:
    return HostPreflightSnapshot(
        os_name="linux",
        os_version="22.04",
        distribution_id="ubuntu",
        arch="x86_64",
        available_free_bytes=free_bytes,
        root_writable=True,
        privileged=False,
        root_is_safe=True,
        available_native_libraries=("libxcb-cursor.so.0",),
    )


def test_preflight_passes_complete_supported_snapshot_without_writes(
    tmp_path: Path,
) -> None:
    install_root = (
        PurePosixPath("/tmp/easyqc-managed-runtime-tests")
        / tmp_path.name
        / "数据 root with spaces"
        / "easyqc"
    )
    paths = resolve_runtime_paths(
        _target("ubuntu-22.04-x86_64"),
        "user",
        user_data_root=str(install_root),
    )

    report = run_preflight(paths, _manifest(), snapshot=_passing_snapshot())

    assert report.passed
    assert report.target_id == "ubuntu-22.04-x86_64"
    assert report.scope == "user"
    assert report.install_root == str(install_root)
    assert report.required_free_bytes == 300 * MIB
    assert report.available_free_bytes == 500 * MIB
    assert {check.code: check.passed for check in report.checks} == {
        "HOST_TARGET": True,
        "ROOT_SAFETY": True,
        "SCOPE_PERMISSION": True,
        "DISK_SPACE": True,
        "NATIVE_PREREQUISITES": True,
    }
    report.require_passed()
    assert not Path(str(install_root)).exists()


@pytest.mark.parametrize(
    ("snapshot", "scope", "failed_code", "message"),
    (
        (
            replace(_passing_snapshot(), os_version="24.04"),
            "user",
            "HOST_TARGET",
            "22.04",
        ),
        (
            replace(_passing_snapshot(), distribution_id="debian"),
            "user",
            "HOST_TARGET",
            "Ubuntu",
        ),
        (
            replace(_passing_snapshot(), arch="arm64"),
            "user",
            "HOST_TARGET",
            "x86_64",
        ),
        (
            replace(
                _passing_snapshot(),
                os_name="windows",
                os_version="11",
                distribution_id=None,
            ),
            "user",
            "HOST_TARGET",
            "Ubuntu",
        ),
        (
            replace(_passing_snapshot(), available_free_bytes=10),
            "user",
            "DISK_SPACE",
            "free bytes",
        ),
        (
            replace(_passing_snapshot(), root_writable=False),
            "user",
            "SCOPE_PERMISSION",
            "not writable",
        ),
        (
            replace(_passing_snapshot(), root_is_safe=False),
            "user",
            "ROOT_SAFETY",
            "unsafe",
        ),
        (
            replace(_passing_snapshot(), available_native_libraries=()),
            "user",
            "NATIVE_PREREQUISITES",
            "sudo apt install libxcb-cursor0",
        ),
        (
            replace(_passing_snapshot(), privileged=False),
            "system",
            "SCOPE_PERMISSION",
            "privilege",
        ),
    ),
)
def test_preflight_reports_each_failure_without_scope_fallback_or_writes(
    snapshot: HostPreflightSnapshot,
    scope: str,
    failed_code: str,
    message: str,
    tmp_path: Path,
) -> None:
    if scope == "system":
        paths = resolve_runtime_paths(
            _target("ubuntu-22.04-x86_64"),
            scope,
        )
    else:
        install_root = (
            PurePosixPath("/tmp/easyqc-managed-runtime-tests")
            / tmp_path.name
            / "未创建 root"
            / "easyqc"
        )
        paths = resolve_runtime_paths(
            _target("ubuntu-22.04-x86_64"),
            scope,
            user_data_root=str(install_root),
        )

    report = run_preflight(paths, _manifest(), snapshot=snapshot)

    assert not report.passed
    assert report.scope == scope
    failed = {check.code: check for check in report.checks if not check.passed}
    assert failed_code in failed
    assert message.lower() in failed[failed_code].message.lower()
    with pytest.raises(ManagedRuntimeError, match=failed_code):
        report.require_passed()
    if scope == "user":
        assert not Path(paths.install_root).exists()


def test_preflight_rejects_manifest_for_a_different_target(tmp_path: Path) -> None:
    install_root = (
        PurePosixPath("/tmp/easyqc-managed-runtime-tests")
        / tmp_path.name
        / "easyqc"
    )
    paths = resolve_runtime_paths(
        _target("ubuntu-22.04-x86_64"),
        "user",
        user_data_root=str(install_root),
    )
    other = _manifest(_target("ubuntu-24.04-x86_64"))

    with pytest.raises(ManagedRuntimeError, match="manifest target"):
        run_preflight(paths, other, snapshot=_passing_snapshot())
