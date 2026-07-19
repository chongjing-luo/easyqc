"""Run the complete EasyQC suite without mixing GUI runtimes in one process.

Input: the repository test tree and the invoking Python executable.
Output: one result per non-GUI, tkinter, and Qt pytest child plus an aggregate
exit status. The script only starts child processes and writes no project data.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TK_TARGETS = (
    "tests/test_gui",
    "tests/test_characterization",
    "tests/test_integration/test_ccnppeki_compat_fixture.py",
)
QT_TARGETS = (
    "tests/test_gui_qt",
    "tests/test_scripts/test_qt_preview_entry.py",
)
NON_GUI_IGNORES = (*TK_TARGETS, *QT_TARGETS)


@dataclass(frozen=True)
class TestLane:
    """One pytest child with one GUI-runtime boundary."""

    name: str
    targets: tuple[str, ...]
    ignores: tuple[str, ...] = ()
    needs_tk_display: bool = False
    qt_offscreen: bool = False
    pytest_plugins: tuple[str, ...] = ()


LANES = (
    TestLane("non_gui", ("tests",), ignores=NON_GUI_IGNORES),
    TestLane("tk", TK_TARGETS, needs_tk_display=True),
    TestLane(
        "qt",
        QT_TARGETS,
        qt_offscreen=True,
        pytest_plugins=("pytestqt.plugin",),
    ),
)


def _validate_targets(project_root: Path) -> None:
    for target in dict.fromkeys(
        target
        for lane in LANES
        for target in (*lane.targets, *lane.ignores)
    ):
        if not (project_root / target).exists():
            raise ValueError(f"missing test target: {target}")


def _resolve_xvfb(
    *,
    platform_name: str,
    environ: Mapping[str, str],
    which: Callable[[str], str | None],
) -> str | None:
    if platform_name != "Linux" or environ.get("DISPLAY"):
        return None
    executable = which("xvfb-run")
    if executable is None:
        raise ValueError("headless Linux tkinter tests require xvfb-run")
    return executable


def _command_for(lane: TestLane, python: Path, xvfb_run: str | None) -> list[str]:
    command = [str(python), "-m", "pytest", *lane.targets]
    command.extend(f"--ignore={target}" for target in lane.ignores)
    for plugin in lane.pytest_plugins:
        command.extend(("-p", plugin))
    if lane.needs_tk_display and xvfb_run is not None:
        command = [xvfb_run, "-a", *command]
    return command


def run_test_matrix(
    *,
    project_root: Path = PROJECT_ROOT,
    python: Path = Path(sys.executable),
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> int:
    """Run every matrix lane and return 0, 1 for tests, or 2 for preflight."""

    project_root = project_root.resolve()
    base_environment = dict(os.environ if environ is None else environ)
    detected_platform = platform.system() if platform_name is None else platform_name
    try:
        _validate_targets(project_root)
        xvfb_run = _resolve_xvfb(
            platform_name=detected_platform,
            environ=base_environment,
            which=which,
        )
    except ValueError as error:
        print(f"ERROR test matrix preflight: {error}", file=sys.stderr)
        return 2

    failed_lanes: list[str] = []
    for lane in LANES:
        command = _command_for(lane, python, xvfb_run)
        child_environment = base_environment.copy()
        # Third-party pytest entry points can import GUI bindings before test
        # collection.  Make plugin loading deterministic and prevent caller
        # options from injecting pytest-qt into the tkinter process.
        child_environment.pop("PYTEST_ADDOPTS", None)
        child_environment.pop("PYTEST_PLUGINS", None)
        child_environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        if lane.qt_offscreen:
            child_environment["QT_QPA_PLATFORM"] = "offscreen"
        print(f"RUN {lane.name}: {shlex.join(command)}", flush=True)
        completed = runner(
            command,
            cwd=project_root,
            env=child_environment,
            check=False,
        )
        if completed.returncode == 0:
            print(f"PASS {lane.name}", flush=True)
        else:
            failed_lanes.append(lane.name)
            print(f"FAIL {lane.name} (exit {completed.returncode})", flush=True)

    if failed_lanes:
        print(f"FAIL test matrix: {', '.join(failed_lanes)}", flush=True)
        return 1
    print("PASS test matrix", flush=True)
    return 0


def main(_argv: Sequence[str] | None = None) -> int:
    return run_test_matrix()


if __name__ == "__main__":
    raise SystemExit(main())
