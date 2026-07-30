"""Run the complete EasyQC suite with Qt isolated from toolkit-free tests.

Input: the repository test tree and the invoking Python executable.
Output: one result per non-GUI and Qt pytest child plus an aggregate
exit status. The script only starts child processes and writes no project data.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QT_TARGETS = (
    "tests/test_gui_qt",
    "tests/test_scripts/test_qt_entry.py",
)
NON_GUI_IGNORES = QT_TARGETS


@dataclass(frozen=True)
class TestLane:
    """One pytest child with one GUI-runtime boundary."""

    name: str
    targets: tuple[str, ...]
    ignores: tuple[str, ...] = ()
    qt_offscreen: bool = False
    pytest_plugins: tuple[str, ...] = ()


LANES = (
    TestLane("non_gui", ("tests",), ignores=NON_GUI_IGNORES),
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


def _command_for(lane: TestLane, python: Path) -> list[str]:
    command = [str(python), "-m", "pytest", *lane.targets]
    command.extend(f"--ignore={target}" for target in lane.ignores)
    for plugin in lane.pytest_plugins:
        command.extend(("-p", plugin))
    return command


def run_test_matrix(
    *,
    project_root: Path = PROJECT_ROOT,
    python: Path = Path(sys.executable),
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[object]] = subprocess.run,
) -> int:
    """Run every matrix lane and return 0, 1 for tests, or 2 for preflight."""

    project_root = project_root.resolve()
    base_environment = dict(os.environ if environ is None else environ)
    try:
        _validate_targets(project_root)
    except ValueError as error:
        print(f"ERROR test matrix preflight: {error}", file=sys.stderr)
        return 2

    failed_lanes: list[str] = []
    for lane in LANES:
        command = _command_for(lane, python)
        child_environment = base_environment.copy()
        # Third-party pytest entry points can import GUI bindings before test
        # collection. Make plugin loading deterministic and prevent caller
        # options from injecting pytest-qt into the toolkit-free process.
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
