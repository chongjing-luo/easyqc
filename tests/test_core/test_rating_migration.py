"""Specification-first tests for the bounded rating migration candidate.

Task ID: rating-filename-migration
Purpose: plan legacy conversion and explicitly prepare a canonical-only,
inactive sibling candidate without modifying the active RatingFiles tree.
Input: one exact synthetic ``tmp_path/.../RatingFiles`` tree.
Output: a deterministic conflict report or a fully verified candidate tree.
Side effects: planning is read-only; apply may create the persistent writer
lock and one candidate, but never renames/prunes/overwrites active ratings.
Errors: invalid IDs, duplicates, casefold collisions, occupied targets,
coexistence, stale plans, symlinks, wrong roots, lock contention, and build
failures fail loud.
Split trigger: candidate activation or source archival/deletion is a separately
authorized task and is intentionally absent.

RF-7 remediation acceptance:
- apply never recreates a missing or moved active ``RatingFiles`` root;
- payload verification distinguishes JSON booleans, integers, and floats;
- manifest inputs use a versioned, POSIX-relative-path encoding;
- every copied ordinary file is either a rating JSON or the explicit lock-file
  exception; other files fail loud;
- clean apply performs one structural candidate parse per rating, rather than
  rebuilding a second payload-bearing plan or repeatedly parsing each file;
- CLI stdout is summary-only unless ``--details`` is explicitly selected.

Prior-art/reuse evidence:
- graph status: fresh at base SHA; the new untracked migration files were not
  indexed, so exact targeted inspection covered their definitions and callers;
- targeted search covered rating identity, scan, manifest, lock, CLI, legacy,
  and atomic-write code;
- reused: ``rating_identity`` validation/path codec, project writer lock and
  the existing rating payload model;
- new code is limited to payload-first legacy recovery, conflict planning and
  isolated candidate construction, including a lightweight one-file snapshot
  reader that keeps CLI output free of application logger side effects.

Doubt resolution:
- publishing canonical beside active legacy would make the application reject
  duplicate identities, so apply builds a complete sibling candidate instead;
- a hard interruption can only leave a reported ``.q-*`` sibling; the active
  tree stays valid and unchanged.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path

import pytest

from core.rating_identity import RatingIdentity, canonical_rating_path
from core.rating_migration import (
    MigrationApplyError,
    MigrationConflictError,
    MigrationPlanStaleError,
    apply_legacy_migration,
    plan_legacy_migration,
)
from core.rating_service import RatingService
from core.rating_write_lock import RatingWriteBusyError
from scripts.migrate_rating_files import main as migration_main


def _payload(
    module_name: str,
    rater: str,
    ezqcid: str,
    *,
    score: int = 1,
    schema_version: int = 1,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "name": module_name,
        "label": f"{module_name} label",
        "rater": rater,
        "ezqcid": ezqcid,
        "scores": {"1": {"value": score}},
        "tags": {"1": {"value": False}},
        "notes": "synthetic",
        "time": "2026-07-28 10:00:00",
        "code_exe": {},
    }


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _write_legacy(
    rating_root: Path,
    module_name: str,
    rater: str,
    ezqcid: str,
    *,
    mutable_suffix: str = "1._.tag",
    score: int = 1,
    schema_version: int = 1,
) -> Path:
    filename = (
        f"{module_name}._.{ezqcid}._.{rater}._.{mutable_suffix}.json"
    )
    return _write_json(
        rating_root / module_name / rater / filename,
        _payload(
            module_name,
            rater,
            ezqcid,
            score=score,
            schema_version=schema_version,
        ),
    )


def _write_canonical(
    rating_root: Path,
    module_name: str,
    rater: str,
    ezqcid: str,
    *,
    payload: dict[str, object] | None = None,
) -> Path:
    identity = RatingIdentity(module_name, rater, ezqcid)
    return _write_json(
        canonical_rating_path(rating_root, identity),
        payload or _payload(module_name, rater, ezqcid),
    )


def _issue_codes(plan: object) -> set[str]:
    return {issue.code for issue in plan.issues}


def _json_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.casefold() == ".json"
    }


def _candidate_roots(rating_root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in rating_root.parent.iterdir()
            if path.name.startswith(".q-")
        ),
        key=str,
    )


def _candidate_destination(
    candidate_root: Path,
    identity: RatingIdentity,
) -> Path:
    return canonical_rating_path(candidate_root, identity)


def test_plan_reports_illegal_identifier_without_writing(tmp_path: Path) -> None:
    rating_root = tmp_path / "RatingFiles"
    source = _write_legacy(rating_root, "bad-name", "rater1", "E001")
    before = _json_snapshot(rating_root)

    plan = plan_legacy_migration(rating_root)

    assert plan.has_conflicts
    assert "invalid_identifier" in _issue_codes(plan)
    assert plan.entries == ()
    assert source.exists()
    assert _json_snapshot(rating_root) == before
    assert _candidate_roots(rating_root) == []


def test_plan_reports_duplicate_legacy_identity(tmp_path: Path) -> None:
    rating_root = tmp_path / "RatingFiles"
    first = _write_legacy(
        rating_root,
        "ModuleA",
        "rater1",
        "E001",
        mutable_suffix="1._.tagA",
        score=1,
    )
    second = _write_legacy(
        rating_root,
        "ModuleA",
        "rater1",
        "E001",
        mutable_suffix="2._.tagB",
        score=2,
    )

    plan = plan_legacy_migration(rating_root)

    assert "duplicate_identity" in _issue_codes(plan)
    issue = next(item for item in plan.issues if item.code == "duplicate_identity")
    assert issue.paths == tuple(sorted((first, second)))
    assert plan.entries == ()


def test_plan_reports_casefold_collision_in_portable_scope(tmp_path: Path) -> None:
    rating_root = tmp_path / "RatingFiles"
    _write_legacy(rating_root, "ModuleA", "rater1", "E001")
    _write_legacy(rating_root, "modulea", "rater2", "E002")

    plan = plan_legacy_migration(rating_root)

    assert "casefold_collision" in _issue_codes(plan)
    issue = next(item for item in plan.issues if item.code == "casefold_collision")
    assert "module_name" in issue.message


def test_plan_reports_occupied_destination(tmp_path: Path) -> None:
    rating_root = tmp_path / "RatingFiles"
    _write_legacy(rating_root, "ModuleA", "rater1", "E001")
    destination = canonical_rating_path(
        rating_root,
        RatingIdentity("ModuleA", "rater1", "E001"),
    )
    _write_json(destination, _payload("ModuleA", "rater1", "OTHER"))

    plan = plan_legacy_migration(rating_root)

    assert "target_occupied" in _issue_codes(plan)
    issue = next(item for item in plan.issues if item.code == "target_occupied")
    assert issue.paths[-1] == destination
    assert plan.entries == ()


def test_plan_reports_legacy_and_canonical_coexistence(tmp_path: Path) -> None:
    rating_root = tmp_path / "RatingFiles"
    source = _write_legacy(rating_root, "ModuleA", "rater1", "E001")
    destination = _write_canonical(rating_root, "ModuleA", "rater1", "E001")

    plan = plan_legacy_migration(rating_root)

    assert "legacy_canonical_coexistence" in _issue_codes(plan)
    issue = next(
        item
        for item in plan.issues
        if item.code == "legacy_canonical_coexistence"
    )
    assert issue.paths == (source, destination)
    assert plan.entries == ()


def test_plan_rejects_wrong_root_and_dangling_symlink(tmp_path: Path) -> None:
    wrong_root = tmp_path / "project"
    wrong_root.mkdir()
    wrong_plan = plan_legacy_migration(wrong_root)
    assert "rating_root_name" in _issue_codes(wrong_plan)

    rating_root = tmp_path / "RatingFiles"
    rating_root.mkdir()
    dangling = rating_root / "dangling.json"
    try:
        dangling.symlink_to(rating_root / "missing.json")
    except OSError as exc:  # pragma: no cover - Windows privilege policy
        pytest.skip(f"symlink creation unavailable: {exc}")

    plan = plan_legacy_migration(rating_root)
    assert "source_symlink" in _issue_codes(plan)
    assert plan.entries == ()


def test_payload_first_legacy_recovery_supports_delimiter_like_ezqcid(
    tmp_path: Path,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    source = _write_legacy(rating_root, "ModuleA", "rater1", "A._.B")

    plan = plan_legacy_migration(rating_root)
    report = apply_legacy_migration(plan)

    assert not plan.has_conflicts
    assert plan.entries[0].identity.ezqcid == "A._.B"
    target = _candidate_destination(
        report.candidate_root,
        RatingIdentity("ModuleA", "rater1", "A._.B"),
    )
    assert target.exists()
    assert source.exists()


def test_conflicting_apply_performs_zero_writes(tmp_path: Path) -> None:
    rating_root = tmp_path / "RatingFiles"
    _write_legacy(
        rating_root,
        "ModuleA",
        "rater1",
        "E001",
        mutable_suffix="1._.tagA",
    )
    _write_legacy(
        rating_root,
        "ModuleA",
        "rater1",
        "E001",
        mutable_suffix="2._.tagB",
    )
    plan = plan_legacy_migration(rating_root)
    before = _json_snapshot(rating_root)

    with pytest.raises(MigrationConflictError):
        apply_legacy_migration(plan)

    assert _json_snapshot(rating_root) == before
    assert not (rating_root / ".easyqc-rating-write.lock").exists()
    assert _candidate_roots(rating_root) == []


def test_clean_apply_builds_canonical_only_inactive_candidate(
    tmp_path: Path,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    first = _write_legacy(rating_root, "ModuleA", "rater1", "E-001")
    second = _write_legacy(rating_root, "ModuleB", "rater2", "E002")
    active_before = _json_snapshot(rating_root)

    plan = plan_legacy_migration(rating_root)
    report = apply_legacy_migration(plan)

    assert not plan.has_conflicts
    assert report.applied
    assert report.activated is False
    assert report.applied_count == 2
    assert report.retained_source_root == rating_root
    assert report.retained_sources == tuple(sorted((first, second)))
    assert _json_snapshot(rating_root) == active_before
    assert first.exists() and second.exists()
    assert report.candidate_root.parent == rating_root.parent
    assert len(report.candidate_root.name) == len("RatingFiles")
    assert (report.candidate_root / ".easyqc-migration-complete").is_file()
    assert all(
        not hasattr(snapshot, "rating")
        and not hasattr(snapshot, "legacy_payload")
        for snapshot in plan.source_snapshots
    )
    marker_payload = json.loads(
        (report.candidate_root / ".easyqc-migration-complete").read_text(
            encoding="utf-8"
        )
    )
    assert marker_payload["source_manifest_sha256"] == plan.source_manifest_sha256
    assert (
        marker_payload["candidate_manifest_sha256"]
        == report.candidate_manifest_sha256
    )

    for path in report.canonical_files:
        record, issue = RatingService._scan_one(path)
        assert issue is None
        assert record is not None
        assert record.filename_format == "canonical"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 2
    assert all("._." not in path.name for path in report.canonical_files)
    assert all(
        not canonical_rating_path(rating_root, entry.identity).exists()
        for entry in plan.entries
    )


def test_candidate_does_not_downgrade_future_schema_version(
    tmp_path: Path,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    _write_legacy(
        rating_root,
        "ModuleA",
        "rater1",
        "E001",
        schema_version=7,
    )

    report = apply_legacy_migration(plan_legacy_migration(rating_root))
    target = _candidate_destination(
        report.candidate_root,
        RatingIdentity("ModuleA", "rater1", "E001"),
    )

    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == 7


def test_apply_rejects_stale_plan_before_candidate_build(tmp_path: Path) -> None:
    rating_root = tmp_path / "RatingFiles"
    source = _write_legacy(rating_root, "ModuleA", "rater1", "E001")
    plan = plan_legacy_migration(rating_root)
    destination = _write_canonical(
        rating_root,
        "ModuleA",
        "rater1",
        "E001",
        payload=_payload("ModuleA", "rater1", "OTHER"),
    )
    before = _json_snapshot(rating_root)

    with pytest.raises(MigrationPlanStaleError):
        apply_legacy_migration(plan)

    assert source.exists() and destination.exists()
    assert _json_snapshot(rating_root) == before
    assert _candidate_roots(rating_root) == []


def test_apply_does_not_recreate_moved_active_rating_root(
    tmp_path: Path,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    source = _write_legacy(rating_root, "ModuleA", "rater1", "E001")
    plan = plan_legacy_migration(rating_root)
    moved_root = tmp_path / "RatingFiles-moved"
    rating_root.rename(moved_root)

    with pytest.raises(MigrationApplyError):
        apply_legacy_migration(plan)

    assert not rating_root.exists()
    assert (moved_root / source.relative_to(rating_root)).is_file()
    assert _candidate_roots(rating_root) == []


def test_candidate_build_failure_cleans_candidate_and_keeps_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    first = _write_legacy(rating_root, "ModuleA", "rater1", "E001")
    second = _write_legacy(rating_root, "ModuleB", "rater2", "E002")
    plan = plan_legacy_migration(rating_root)
    before = _json_snapshot(rating_root)

    import core.rating_migration as migration

    real_write = migration._write_candidate_payload
    calls = 0

    def fail_second(destination: Path, payload: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic candidate write failure")
        real_write(destination, payload)

    monkeypatch.setattr(migration, "_write_candidate_payload", fail_second)

    with pytest.raises(MigrationApplyError):
        apply_legacy_migration(plan)

    assert first.exists() and second.exists()
    assert _json_snapshot(rating_root) == before
    assert _candidate_roots(rating_root) == []


def test_existing_incomplete_candidate_blocks_new_apply(tmp_path: Path) -> None:
    rating_root = tmp_path / "RatingFiles"
    _write_legacy(rating_root, "ModuleA", "rater1", "E001")
    incomplete = rating_root.parent / ".q-deadbeef"
    incomplete.mkdir()

    plan = plan_legacy_migration(rating_root)

    assert "incomplete_candidate_tree" in _issue_codes(plan)
    with pytest.raises(MigrationConflictError):
        apply_legacy_migration(plan)
    assert incomplete.exists()


def test_lock_contention_fails_without_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    _write_legacy(rating_root, "ModuleA", "rater1", "E001")
    plan = plan_legacy_migration(rating_root)
    before = _json_snapshot(rating_root)

    import core.rating_migration as migration

    @contextmanager
    def busy_lock(_target: Path, *, create_lock_root: bool = True):
        assert create_lock_root is False
        raise RatingWriteBusyError("synthetic busy project")
        yield  # pragma: no cover

    monkeypatch.setattr(migration, "rating_write_lock", busy_lock)

    with pytest.raises(MigrationApplyError, match="writer lock"):
        apply_legacy_migration(plan)

    assert _json_snapshot(rating_root) == before
    assert _candidate_roots(rating_root) == []


def test_cli_is_dry_run_by_default_and_apply_only_prepares_candidate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rating_root = tmp_path / "RatingFiles"
    source = _write_legacy(rating_root, "ModuleA", "rater1", "E001")
    active_destination = canonical_rating_path(
        rating_root,
        RatingIdentity("ModuleA", "rater1", "E001"),
    )

    assert migration_main([str(rating_root)]) == 0
    dry_run_output = json.loads(capsys.readouterr().out)
    assert dry_run_output["mode"] == "dry-run"
    assert dry_run_output["conflict_free"] is True
    assert dry_run_output["manifest_format_version"] == 1
    assert "entries" not in dry_run_output
    assert "issues" not in dry_run_output
    assert source.exists()
    assert not active_destination.exists()
    assert _candidate_roots(rating_root) == []

    assert migration_main([str(rating_root), "--details"]) == 0
    detailed_output = json.loads(capsys.readouterr().out)
    assert len(detailed_output["entries"]) == 1
    assert detailed_output["issues"] == []
    assert _candidate_roots(rating_root) == []

    assert migration_main([str(rating_root), "--apply"]) == 0
    apply_output = json.loads(capsys.readouterr().out)
    assert apply_output["mode"] == "apply-candidate"
    assert apply_output["applied"] is True
    assert apply_output["activated"] is False
    assert "entries" not in apply_output
    assert "canonical_files" not in apply_output
    candidate_root = Path(apply_output["candidate_root"])
    assert candidate_root.is_dir()
    assert source.exists()
    assert not active_destination.exists()
    assert _candidate_destination(
        candidate_root,
        RatingIdentity("ModuleA", "rater1", "E001"),
    ).exists()


def test_silent_payload_loss_is_rejected_and_candidate_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    source = _write_legacy(rating_root, "ModuleA", "rater1", "E001")
    plan = plan_legacy_migration(rating_root)
    before = _json_snapshot(rating_root)

    import core.rating_migration as migration

    real_write = migration._write_candidate_payload

    def silently_drop_scores(
        destination: Path,
        payload: dict[str, object],
    ) -> None:
        damaged = dict(payload)
        damaged.pop("scores", None)
        real_write(destination, damaged)

    monkeypatch.setattr(
        migration,
        "_write_candidate_payload",
        silently_drop_scores,
    )

    with pytest.raises(MigrationApplyError, match="payload differs"):
        apply_legacy_migration(plan)

    assert source.exists()
    assert _json_snapshot(rating_root) == before
    assert _candidate_roots(rating_root) == []


def test_manifest_encoding_is_versioned_and_uses_posix_relative_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.rating_migration as migration

    encoded_values: list[object] = []
    real_encode = migration._deterministic_json_bytes

    def capture_encode(value: object) -> bytes:
        encoded_values.append(value)
        return real_encode(value)

    class WindowsLikeRelativePath:
        def __str__(self) -> str:
            return r"ModuleA\rater1\record.json"

        def as_posix(self) -> str:
            return "ModuleA/rater1/record.json"

    class WindowsLikePath:
        def relative_to(self, _root: object) -> WindowsLikeRelativePath:
            return WindowsLikeRelativePath()

    monkeypatch.setattr(
        migration,
        "_deterministic_json_bytes",
        capture_encode,
    )

    migration._manifest_digest(
        object(),
        ((WindowsLikePath(), "a" * 64),),
    )

    assert {
        "format": "easyqc-rating-tree-manifest",
        "version": 1,
    } in encoded_values
    assert ["ModuleA/rater1/record.json", "a" * 64] in encoded_values


@pytest.mark.parametrize("replacement", [True, 1.0])
def test_payload_verification_distinguishes_json_scalar_types(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: object,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    source = _write_legacy(rating_root, "ModuleA", "rater1", "E001", score=1)
    plan = plan_legacy_migration(rating_root)
    before = _json_snapshot(rating_root)

    import core.rating_migration as migration

    real_write = migration._write_candidate_payload

    def replace_integer_with_equal_scalar(
        destination: Path,
        payload: dict[str, object],
    ) -> None:
        damaged = copy.deepcopy(payload)
        scores = damaged["scores"]
        assert isinstance(scores, dict)
        score = scores["1"]
        assert isinstance(score, dict)
        score["value"] = replacement
        real_write(destination, damaged)

    monkeypatch.setattr(
        migration,
        "_write_candidate_payload",
        replace_integer_with_equal_scalar,
    )

    with pytest.raises(MigrationApplyError, match="payload differs"):
        apply_legacy_migration(plan)

    assert source.exists()
    assert _json_snapshot(rating_root) == before
    assert _candidate_roots(rating_root) == []


def test_plan_rejects_non_json_regular_file_except_writer_lock(
    tmp_path: Path,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    source = _write_legacy(rating_root, "ModuleA", "rater1", "E001")
    auxiliary = rating_root / "ModuleA" / "rater1" / "notes.txt"
    auxiliary.write_text("must not be silently copied", encoding="utf-8")
    lock_file = rating_root / ".easyqc-rating-write.lock"
    lock_file.write_bytes(b"\0")

    plan = plan_legacy_migration(rating_root)

    assert plan.has_conflicts
    issue = next(
        item for item in plan.issues if item.code == "source_non_json_file"
    )
    assert issue.paths == (auxiliary,)
    with pytest.raises(MigrationConflictError):
        apply_legacy_migration(plan)
    assert source.is_file()
    assert auxiliary.is_file()
    assert lock_file.is_file()
    assert _candidate_roots(rating_root) == []


def test_candidate_verification_rejects_non_json_file_added_during_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    source = _write_legacy(rating_root, "ModuleA", "rater1", "E001")
    plan = plan_legacy_migration(rating_root)

    import core.rating_migration as migration

    real_copy = migration._copy_active_tree

    def copy_then_add_auxiliary(
        source_root: Path,
        candidate_root: Path,
    ) -> None:
        real_copy(source_root, candidate_root)
        (candidate_root / "untracked.bin").write_bytes(b"not in manifest")

    monkeypatch.setattr(migration, "_copy_active_tree", copy_then_add_auxiliary)

    with pytest.raises(MigrationApplyError, match="ordinary non-JSON"):
        apply_legacy_migration(plan)

    assert source.is_file()
    assert _candidate_roots(rating_root) == []


def test_apply_structurally_parses_each_candidate_record_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    record_count = 12
    for index in range(record_count):
        _write_legacy(
            rating_root,
            "ModuleA",
            "rater1",
            f"E{index:03d}",
        )
    plan = plan_legacy_migration(rating_root)

    import core.rating_migration as migration

    real_snapshot_one = migration._snapshot_one
    parsed_paths: list[Path] = []

    def count_snapshot(
        root: Path,
        path: Path,
    ):
        parsed_paths.append(path)
        return real_snapshot_one(root, path)

    monkeypatch.setattr(migration, "_snapshot_one", count_snapshot)

    report = apply_legacy_migration(plan)

    assert report.applied_count == record_count
    assert len(parsed_paths) == record_count
    assert all(path.is_relative_to(report.candidate_root) for path in parsed_paths)


def test_marker_manifests_detect_candidate_and_source_tampering(
    tmp_path: Path,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    source = _write_legacy(rating_root, "ModuleA", "rater1", "E001")
    plan = plan_legacy_migration(rating_root)
    report = apply_legacy_migration(plan)
    marker = json.loads(
        (report.candidate_root / ".easyqc-migration-complete").read_text(
            encoding="utf-8"
        )
    )
    assert marker["manifest_format_version"] == 1

    import core.rating_migration as migration

    target = report.canonical_files[0]
    candidate_payload = json.loads(target.read_text(encoding="utf-8"))
    candidate_payload["notes"] = "tampered candidate"
    _write_json(target, candidate_payload)
    actual_candidate_manifest = migration._manifest_digest(
        report.candidate_root,
        (
            (path, migration._sha256_file(path))
            for path in report.canonical_files
        ),
    )

    source_payload = json.loads(source.read_text(encoding="utf-8"))
    source_payload["notes"] = "tampered source"
    _write_json(source, source_payload)
    actual_source_manifest = migration._manifest_digest(
        rating_root,
        (
            (snapshot.path, migration._sha256_file(snapshot.path))
            for snapshot in plan.source_snapshots
        ),
    )

    assert actual_candidate_manifest != marker["candidate_manifest_sha256"]
    assert actual_source_manifest != marker["source_manifest_sha256"]


def test_apply_detects_source_added_after_candidate_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    source = _write_legacy(rating_root, "ModuleA", "rater1", "E001")
    plan = plan_legacy_migration(rating_root)

    import core.rating_migration as migration

    real_copy = migration._copy_active_tree
    added_identity = RatingIdentity("ModuleB", "rater2", "E002")

    def copy_then_add_source(
        source_root: Path,
        candidate_root: Path,
    ) -> None:
        real_copy(source_root, candidate_root)
        _write_canonical(
            source_root,
            added_identity.module_name,
            added_identity.rater,
            added_identity.ezqcid,
        )

    monkeypatch.setattr(migration, "_copy_active_tree", copy_then_add_source)

    with pytest.raises(MigrationApplyError, match="source tree manifest changed"):
        apply_legacy_migration(plan)

    assert source.exists()
    assert canonical_rating_path(rating_root, added_identity).exists()
    assert _candidate_roots(rating_root) == []
