import os
import subprocess
from pathlib import Path

import pytest

import build as build_script


def test_start_script_self_locates_easyqc_root(easyqc_root: Path) -> None:
    source = (easyqc_root / "start.sh").read_text(encoding="utf-8")

    assert "BASH_SOURCE[0]" in source
    assert 'cd "$SCRIPT_DIR"' in source
    assert 'python "$SCRIPT_DIR/easyqc.py" "$@"' in source
    assert "/home/ubuntu/Softwares/easyqc" not in source


def test_setup_generates_self_locating_start_script(easyqc_root: Path) -> None:
    source = (easyqc_root / "setup.sh").read_text(encoding="utf-8")

    assert 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in source
    assert 'SCRIPT_DIR="\\$(cd "\\$(dirname "\\${BASH_SOURCE[0]}")" && pwd)"' in source
    assert 'python "\\$SCRIPT_DIR/easyqc.py" "\\$@"' in source
    assert "/home/ubuntu/Softwares/easyqc" not in source


def test_setup_supports_check_only_mode(easyqc_root: Path) -> None:
    source = (easyqc_root / "setup.sh").read_text(encoding="utf-8")

    assert "CHECK_ONLY=false" in source
    assert "-check|--check)" in source
    assert "check_environment || exit 1" in source
    assert "verify_installation || exit 1" in source
    assert "check_start_script || exit 1" in source
    assert "检查完成：Linux 主线环境和启动脚本可用" in source


def test_setup_uses_tuple_python_version_check(easyqc_root: Path) -> None:
    source = (easyqc_root / "setup.sh").read_text(encoding="utf-8")

    assert "python_version_at_least_310()" in source
    assert "sys.version_info >= (3, 10)" in source
    assert "python_version_at_least_310 python3.10" in source
    assert "python_version_at_least_310 python3" in source
    assert '[[ "$PYTHON_VERSION" > "3.10" ]]' not in source


def test_setup_detects_stale_copied_virtualenv_paths(easyqc_root: Path) -> None:
    source = (easyqc_root / "setup.sh").read_text(encoding="utf-8")

    assert "EXPECTED_PREFIX=" in source
    assert "ACTIVE_PREFIX=" in source
    assert "环境路径不匹配，需要重新创建" in source
    assert 'readlink -f "$ACTIVE_PREFIX"' in source
    assert 'readlink -f "$EXPECTED_PREFIX"' in source
    assert "PIP_SHEBANG=" in source
    assert "pip 入口脚本路径不匹配，需要重新创建" in source


def test_setup_verification_fails_loudly_on_missing_dependencies(easyqc_root: Path) -> None:
    source = (easyqc_root / "setup.sh").read_text(encoding="utf-8")

    assert "错误：部分依赖验证失败" in source
    assert "from PySide6.QtWidgets import QApplication" in source
    assert "|| echo \"⚠ 部分依赖验证失败\"" not in source


def test_setup_verifies_only_required_dependencies(easyqc_root: Path) -> None:
    source = (easyqc_root / "setup.sh").read_text(encoding="utf-8")

    assert "验证主要依赖..." in source
    assert "所有依赖验证完成" in source


def test_setup_and_build_verify_the_qt_runtime(easyqc_root: Path) -> None:
    setup_source = (easyqc_root / "setup.sh").read_text(encoding="utf-8")
    build_source = (easyqc_root / "build.py").read_text(encoding="utf-8")
    requirements = (easyqc_root / "requirements.txt").read_text(encoding="utf-8")

    assert "from PySide6.QtWidgets import QApplication" in setup_source
    assert '"PySide6"' in build_source
    assert "PySide6-Essentials==6.11.1" in requirements


def test_spec_and_build_include_platformdirs_metadata_and_notice(
    easyqc_root: Path,
) -> None:
    spec_source = (easyqc_root / "easyqc.spec").read_text(encoding="utf-8")
    build_source = (easyqc_root / "build.py").read_text(encoding="utf-8")

    assert 'copy_metadata("platformdirs")' in spec_source
    assert '"platformdirs"' in build_source
    assert "write_common_license_bundle(artifact)" in build_source
    assert "verify_common_bundle(artifact)" in build_source


def test_setup_and_build_fail_loudly_on_missing_linux_qt_runtime(
    easyqc_root: Path,
    monkeypatch,
) -> None:
    setup_source = (easyqc_root / "setup.sh").read_text(encoding="utf-8")
    build_source = (easyqc_root / "build.py").read_text(encoding="utf-8")

    assert "check_qt_platform_dependencies" in setup_source
    assert "QT_MISSING_LIBRARIES" in setup_source
    assert "=> not found" in setup_source
    assert "sort -u" in setup_source
    assert "check_qt_platform_dependencies" in build_source
    assert "libxcb-cursor.so.0" in build_source

    monkeypatch.setattr(build_script, "SYSTEM", "Linux")

    monkeypatch.setattr(
        build_script,
        "_missing_linux_qt_libraries",
        lambda: ("libxcb-cursor.so.0",),
    )

    with pytest.raises(SystemExit):
        build_script.check_qt_platform_dependencies()


def test_build_smoke_starts_packaged_qt_offscreen(easyqc_root: Path) -> None:
    build_source = (easyqc_root / "build.py").read_text(encoding="utf-8")

    assert '"QT_QPA_PLATFORM": "offscreen"' in build_source
    assert '[str(binary)]' in build_source
    assert "subprocess.Popen" in build_source


def test_linux_cursor_deb_rejects_wrong_size_before_hashing_or_extraction(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    deb = tmp_path / "libxcb-cursor0.deb"
    deb.write_bytes(b"not-the-approved-package")
    monkeypatch.setattr(build_script, "SYSTEM", "Linux")
    monkeypatch.setattr(build_script, "ARCH", "x86_64")
    monkeypatch.setattr(
        build_script,
        "_sha256_file",
        lambda _path: pytest.fail("wrong-size input must not be hashed"),
        raising=False,
    )
    monkeypatch.setattr(
        build_script.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("wrong-size input must not be extracted"),
    )

    with pytest.raises(SystemExit):
        build_script.verify_linux_cursor_deb(deb)

    assert "大小" in capsys.readouterr().out


def test_linux_cursor_deb_rejects_wrong_hash_before_metadata_or_extraction(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    deb = tmp_path / "libxcb-cursor0.deb"
    deb.write_bytes(b"d" * 10_538)
    monkeypatch.setattr(build_script, "SYSTEM", "Linux")
    monkeypatch.setattr(build_script, "ARCH", "x86_64")
    monkeypatch.setattr(build_script, "_sha256_file", lambda _path: "wrong")
    monkeypatch.setattr(
        build_script.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("wrong hash must stop before dpkg-deb"),
    )

    with pytest.raises(SystemExit):
        build_script.verify_linux_cursor_deb(deb)

    assert "SHA-256" in capsys.readouterr().out


def test_linux_cursor_deb_rejects_wrong_control_before_extraction(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    deb = tmp_path / "libxcb-cursor0.deb"
    deb.write_bytes(b"d" * 10_538)
    observed = []
    monkeypatch.setattr(build_script, "SYSTEM", "Linux")
    monkeypatch.setattr(build_script, "ARCH", "x86_64")
    monkeypatch.setattr(
        build_script,
        "_sha256_file",
        lambda _path: build_script.LINUX_CURSOR_DEB_SHA256,
    )

    def fake_run(command, **_kwargs):
        observed.append(command)
        if command[:2] != ["dpkg-deb", "-f"]:
            pytest.fail("wrong control metadata must stop before extraction")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "Package: libxcb-cursor0\n"
                "Version: 0.1.2-unapproved\n"
                "Architecture: amd64\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(build_script.subprocess, "run", fake_run)

    with pytest.raises(SystemExit):
        build_script.verify_linux_cursor_deb(deb)

    assert len(observed) == 1
    assert "元数据不匹配" in capsys.readouterr().out


def test_linux_cursor_input_is_rejected_on_non_linux_before_hashing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    deb = tmp_path / "libxcb-cursor0.deb"
    deb.write_bytes(b"d" * 10_538)
    monkeypatch.setattr(build_script, "SYSTEM", "Darwin")
    monkeypatch.setattr(
        build_script,
        "_sha256_file",
        lambda _path: pytest.fail("non-Linux input must stop before hashing"),
    )

    with pytest.raises(SystemExit):
        build_script.verify_linux_cursor_deb(deb)


def test_linux_cursor_deb_normalizes_exact_package_into_controlled_sysroot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    deb = tmp_path / "libxcb-cursor0_0.1.1-4ubuntu1_amd64.deb"
    deb.write_bytes(b"d" * 10_538)
    build_dir = tmp_path / "build"
    notice = tmp_path / "xcb-util-cursor.txt"
    notice.write_text("complete MIT/X notice\n", encoding="utf-8")

    monkeypatch.setattr(build_script, "SYSTEM", "Linux")
    monkeypatch.setattr(build_script, "ARCH", "x86_64")
    monkeypatch.setattr(build_script, "BUILD_DIR", build_dir)
    monkeypatch.setattr(build_script, "LINUX_CURSOR_NOTICE", notice, raising=False)

    def fake_sha256(path: Path) -> str:
        path = Path(path)
        if path == deb:
            return build_script.LINUX_CURSOR_DEB_SHA256
        if path.name == "libxcb-cursor.so.0":
            return build_script.LINUX_CURSOR_LIBRARY_SHA256
        raise AssertionError(f"unexpected hash path: {path}")

    def fake_run(command, **kwargs):
        assert isinstance(command, list)
        assert kwargs.get("shell", False) is False
        assert kwargs["timeout"] > 0
        if command[:2] == ["dpkg-deb", "-f"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "Package: libxcb-cursor0\n"
                    "Version: 0.1.1-4ubuntu1\n"
                    "Architecture: amd64\n"
                ),
                stderr="",
            )
        if command[:2] == ["dpkg-deb", "-x"]:
            sysroot = Path(command[3])
            library_dir = sysroot / "usr/lib/x86_64-linux-gnu"
            library_dir.mkdir(parents=True)
            real_library = library_dir / "libxcb-cursor.so.0.0.0"
            real_library.write_bytes(b"verified-elf")
            (library_dir / "libxcb-cursor.so.0").symlink_to(real_library.name)
            copyright_path = sysroot / "usr/share/doc/libxcb-cursor0/copyright"
            copyright_path.parent.mkdir(parents=True)
            copyright_path.write_bytes(notice.read_bytes())
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(build_script, "_sha256_file", fake_sha256, raising=False)
    monkeypatch.setattr(build_script.subprocess, "run", fake_run)

    runtime = build_script.verify_linux_cursor_deb(deb)

    assert runtime.library_path.name == "libxcb-cursor.so.0"
    assert runtime.library_path.is_symlink()
    assert runtime.library_path.resolve().is_relative_to(build_dir.resolve())
    assert runtime.copyright_path.read_bytes() == notice.read_bytes()


def test_pyinstaller_spec_requires_the_verified_linux_cursor_input(
    easyqc_root: Path,
) -> None:
    source = (easyqc_root / "easyqc.spec").read_text(encoding="utf-8")

    assert "EASYQC_LINUX_CURSOR_LIBRARY" in source
    assert "binaries=binaries" in source
    assert "Linux cursor library" in source
    assert "hashlib.sha256" in source
    assert build_script.LINUX_CURSOR_LIBRARY_SHA256 in source


def test_pyinstaller_revalidates_cursor_before_starting(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cursor = tmp_path / "libxcb-cursor.so.0"
    cursor.write_bytes(b"tampered")
    notice = tmp_path / "copyright"
    notice.write_text("notice", encoding="utf-8")
    runtime = build_script.VerifiedLinuxCursorRuntime(cursor, notice)
    monkeypatch.setattr(build_script, "SYSTEM", "Linux")
    monkeypatch.setattr(build_script, "_sha256_file", lambda _path: "wrong")
    monkeypatch.setattr(
        build_script.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("PyInstaller must not start"),
    )

    with pytest.raises(SystemExit):
        build_script.run_pyinstaller(runtime)


def test_linux_bundle_writes_and_verifies_license_provenance_and_cursor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact = tmp_path / "EasyQC"
    internal = artifact / "_internal"
    plugin = internal / "PySide6/Qt/plugins/platforms/libqxcb.so"
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(b"plugin")
    packaged_cursor = internal / "libxcb-cursor.so.0"
    packaged_cursor.write_bytes(b"verified-cursor")
    source_cursor = tmp_path / "libxcb-cursor.so.0"
    source_cursor.write_bytes(b"verified-cursor")
    source_notice = tmp_path / "xcb-util-cursor.txt"
    source_notice.write_text("complete MIT/X notice\n", encoding="utf-8")
    runtime_notice = tmp_path / "copyright"
    runtime_notice.write_bytes(source_notice.read_bytes())
    runtime = build_script.VerifiedLinuxCursorRuntime(
        library_path=source_cursor,
        copyright_path=runtime_notice,
    )

    monkeypatch.setattr(build_script, "SYSTEM", "Linux")
    monkeypatch.setattr(build_script, "LINUX_CURSOR_NOTICE", source_notice, raising=False)
    monkeypatch.setattr(
        build_script,
        "_sha256_file",
        lambda path: (
            build_script.LINUX_CURSOR_LIBRARY_SHA256
            if Path(path).name == "libxcb-cursor.so.0"
            else pytest.fail(f"unexpected hash path: {path}")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        build_script.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=f"libxcb-cursor.so.0 => {packaged_cursor}\n",
            stderr="",
        ),
    )

    build_script.write_linux_license_bundle(artifact, runtime)
    build_script.verify_linux_bundle(artifact, runtime)

    license_dir = artifact / "THIRD_PARTY_LICENSES"
    assert (license_dir / "xcb-util-cursor.txt").read_bytes() == source_notice.read_bytes()
    provenance = (license_dir / "libxcb-cursor0-PROVENANCE.txt").read_text(
        encoding="utf-8"
    )
    assert "Ubuntu 22.04 x86_64" in provenance
    assert build_script.LINUX_CURSOR_DEB_SHA256 in provenance
    assert build_script.LINUX_CURSOR_LIBRARY_SHA256 in provenance
    assert str(tmp_path) not in provenance


def test_linux_bundle_rejects_cursor_symlink_outside_artifact(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    artifact = tmp_path / "EasyQC"
    internal = artifact / "_internal"
    plugin = internal / "PySide6/Qt/plugins/platforms/libqxcb.so"
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(b"plugin")
    outside_cursor = tmp_path / "host/libxcb-cursor.so.0"
    outside_cursor.parent.mkdir()
    outside_cursor.write_bytes(b"verified-cursor")
    (internal / "libxcb-cursor.so.0").symlink_to(outside_cursor)
    source_notice = tmp_path / "xcb-util-cursor.txt"
    source_notice.write_text("complete MIT/X notice\n", encoding="utf-8")
    runtime_notice = tmp_path / "copyright"
    runtime_notice.write_bytes(source_notice.read_bytes())
    runtime = build_script.VerifiedLinuxCursorRuntime(
        library_path=outside_cursor,
        copyright_path=runtime_notice,
    )

    monkeypatch.setattr(build_script, "SYSTEM", "Linux")
    monkeypatch.setattr(build_script, "LINUX_CURSOR_NOTICE", source_notice)
    monkeypatch.setattr(
        build_script,
        "_sha256_file",
        lambda _path: build_script.LINUX_CURSOR_LIBRARY_SHA256,
    )
    monkeypatch.setattr(
        build_script.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("outside cursor must fail before ldd"),
    )

    build_script.write_linux_license_bundle(artifact, runtime)
    with pytest.raises(SystemExit):
        build_script.verify_linux_bundle(artifact, runtime)

    assert "产物目录之外" in capsys.readouterr().out


def test_linux_native_smoke_removes_external_library_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    binary = tmp_path / "EasyQC"
    binary.write_bytes(b"binary")
    observed = []

    monkeypatch.setattr(build_script, "SYSTEM", "Linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/untrusted/build/sysroot")
    monkeypatch.setenv("LD_PRELOAD", "/untrusted/preload.so")
    monkeypatch.setattr(build_script.shutil, "which", lambda _name: "/usr/bin/xvfb-run")
    monkeypatch.setattr(
        build_script.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout="EasyQC help",
            stderr="",
        ),
    )
    monkeypatch.setattr(
        build_script,
        "_expect_process_alive",
        lambda command, env, label: observed.append((command, env, label)),
        raising=False,
    )

    build_script.smoke_test(binary)

    assert observed[0][1]["QT_QPA_PLATFORM"] == "offscreen"
    native_command, native_env, native_label = observed[1]
    assert native_command[:2] == ["/usr/bin/xvfb-run", "-a"]
    assert native_env["QT_QPA_PLATFORM"] == "xcb"
    assert native_env.get("LD_LIBRARY_PATH") is None
    assert native_env.get("LD_PRELOAD") is None
    assert native_label == "Qt native xcb"


def test_common_bundle_writes_and_verifies_platformdirs_notice_and_metadata(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "EasyQC"
    metadata = artifact / "_internal/platformdirs-4.10.1.dist-info/METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        "Metadata-Version: 2.4\nName: platformdirs\nVersion: 4.10.1\n",
        encoding="utf-8",
    )

    notice_path = build_script.write_common_license_bundle(artifact)
    build_script.verify_common_bundle(artifact)

    assert notice_path == artifact / "THIRD_PARTY_LICENSES/platformdirs.txt"
    assert notice_path.read_bytes() == build_script.PLATFORMDIRS_NOTICE.read_bytes()


def test_common_bundle_rejects_internal_runtime_logs(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "EasyQC"
    metadata = artifact / "_internal/platformdirs-4.10.1.dist-info/METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        "Metadata-Version: 2.4\nName: platformdirs\nVersion: 4.10.1\n",
        encoding="utf-8",
    )
    build_script.write_common_license_bundle(artifact)
    generated_log = artifact / "_internal/logs/easyqc_test.log"
    generated_log.parent.mkdir()
    generated_log.write_text("bundle-relative\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        build_script.verify_common_bundle(artifact)


def test_packaging_smoke_rejects_generated_internal_logs(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "EasyQC"
    baseline = artifact / "_internal/baseline.bin"
    baseline.parent.mkdir(parents=True)
    baseline.write_bytes(b"baseline")
    before = build_script._artifact_manifest(artifact)
    assert before.schema == "easyqc-release-artifact-manifest-v1"
    generated_log = artifact / "_internal/logs/easyqc_test.log"
    generated_log.parent.mkdir()
    generated_log.write_text("smoke-only\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        build_script._verify_artifact_unchanged_after_smoke(artifact, before)

    assert generated_log.exists()


def test_packaging_smoke_rejects_any_other_artifact_mutation(
    tmp_path: Path,
    capsys,
) -> None:
    artifact = tmp_path / "EasyQC"
    baseline = artifact / "_internal/baseline.bin"
    baseline.parent.mkdir(parents=True)
    baseline.write_bytes(b"baseline")
    before = build_script._artifact_manifest(artifact)
    unexpected = artifact / "_internal/unexpected.bin"
    unexpected.write_bytes(b"unexpected")

    with pytest.raises(SystemExit):
        build_script._verify_artifact_unchanged_after_smoke(artifact, before)

    assert unexpected.exists()
    assert "改变了候选产物" in capsys.readouterr().out


def test_packaging_smoke_manifest_detects_same_size_same_mtime_rewrite(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "EasyQC"
    baseline = artifact / "_internal/baseline.bin"
    baseline.parent.mkdir(parents=True)
    baseline.write_bytes(b"baseline")
    original = baseline.stat()
    before = build_script._artifact_manifest(artifact)
    baseline.write_bytes(b"tampered")
    baseline.touch()
    baseline.chmod(original.st_mode)
    baseline_stat = baseline.stat()
    os.utime(
        baseline,
        ns=(baseline_stat.st_atime_ns, original.st_mtime_ns),
    )

    with pytest.raises(SystemExit):
        build_script._verify_artifact_unchanged_after_smoke(artifact, before)


def test_verify_output_counts_symlink_metadata_without_following_target(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    dist_dir = tmp_path / "dist"
    artifact = dist_dir / build_script.APP_NAME
    internal = artifact / "_internal"
    internal.mkdir(parents=True)
    executable = artifact / build_script.EXE_NAME
    executable.write_bytes(b"easyqc")
    payload = internal / "payload.bin"
    payload.write_bytes(b"x" * (1024 * 1024))
    payload_link = internal / "payload-link"
    try:
        payload_link.symlink_to(payload.name)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlinks are unavailable on this platform: {exc}")

    monkeypatch.setattr(build_script, "DIST_DIR", dist_dir)
    expected_bytes = sum(
        path.lstat().st_size
        for path in (artifact, executable, internal, payload, payload_link)
    )

    reported_mb = build_script.verify_output()
    reported_bytes = round(reported_mb * 1024 * 1024)

    assert reported_bytes == expected_bytes
    assert reported_mb == pytest.approx(expected_bytes / (1024 * 1024))
    assert f"{expected_bytes / (1024 * 1024):.0f} MB" in capsys.readouterr().out


def test_verify_output_keeps_missing_binary_failure_loud(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    dist_dir = tmp_path / "dist"
    (dist_dir / build_script.APP_NAME).mkdir(parents=True)
    monkeypatch.setattr(build_script, "DIST_DIR", dist_dir)

    with pytest.raises(SystemExit):
        build_script.verify_output()

    assert "未找到可执行文件" in capsys.readouterr().out


def test_readme_documents_qt_only_product_and_runtime_contract(easyqc_root: Path) -> None:
    readme = (easyqc_root / "README.md").read_text(encoding="utf-8")
    requirements = (easyqc_root / "requirements.txt").read_text(encoding="utf-8")

    assert "python easyqc.py             # 所有平台" in readme
    assert "唯一图形界面" in readme
    assert "JSON/CSV" in readme
    assert "libxcb-cursor0" in readme
    assert "533.219 MiB" in readme
    assert "非发布阈值" in readme
    assert ".venv/bin/python scripts/run_test_matrix.py" in readme
    assert [
        line.strip()
        for line in requirements.splitlines()
        if line.strip().lower().startswith("pandas")
    ] == ["pandas>=1.3.0,<3"]


def test_pyinstaller_spec_does_not_collect_pandas_test_tree(easyqc_root: Path) -> None:
    spec_source = (easyqc_root / "easyqc.spec").read_text(encoding="utf-8")

    assert 'collect_submodules("pandas")' not in spec_source
    assert 'for _pkg in ["pandas"]' not in spec_source
    assert '"pandas._libs.tslibs"' in spec_source


def test_readme_separates_visual_table_ui_from_structured_core_contract(easyqc_root: Path) -> None:
    # NOTE: this test previously asserted requirements.txt contained
    # 'scikit-learn>=1.0.0', but the codebase never imports sklearn — ADR-006
    # replaced the external SQL/pandasql query engine with the built-in
    # TableTransformEngine + ExpressionParser (AST whitelist), so sklearn is not
    # a dependency. The assertion was stale and is removed (T-INFRA-1).
    readme = (easyqc_root / "README.md").read_text(encoding="utf-8")

    assert "无需查看、粘贴或编辑 JSON" in readme
    assert "结构化 Core 契约" in readme
    assert "easyqc_back/" in readme
    assert "不作为日常启动目标" in readme


def test_easyqc_back_is_marked_as_reference_only(easyqc_root: Path) -> None:
    deprecated = (easyqc_root.parent / "easyqc_back" / "DEPRECATED.md").read_text(encoding="utf-8")

    assert "old EasyQC implementation" in deprecated
    assert "Do not use this directory as the daily application entry point" in deprecated
    assert "Do not add features or bug fixes here" in deprecated


def test_flat_layout_imports_work_from_easyqc_root(easyqc_root: Path) -> None:
    """P3-F: this project uses a flat layout (no package __init__.py at root;
    easyqc.py injects the project root onto sys.path and imports siblings as
    `from core.X`, `from utils.X`, `from models.X`). This test pins that the
    flat-layout import contract holds, so a future restructure to a real
    package does not silently break the documented run mode (`python easyqc.py`).
    The documented layout decision lives in README.md."""
    # easyqc.py must exist at the root and reference the flat import path
    entry = (easyqc_root / "easyqc.py").read_text(encoding="utf-8")
    assert "sys.path" in entry, "easyqc.py should inject sys.path for flat imports"
    # the three sibling packages exist as flat directories
    assert (easyqc_root / "core").is_dir()
    assert (easyqc_root / "utils").is_dir()
    assert (easyqc_root / "models").is_dir()
