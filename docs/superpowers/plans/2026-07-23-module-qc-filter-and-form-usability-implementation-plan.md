# Implementation plan: module QC filter and form usability

Status: approved by user on 2026-07-23 ("执行").

Source design:
`docs/superpowers/specs/2026-07-23-module-qc-filter-and-form-usability-design.md`

## Overview

Implement the approved per-module structured QC filter on the existing
position-only table path, connect the same Qt visual filter builder to the
module page and QC controller, then make the two independent native-widget
repairs for score choices and the constants table. The work remains serial
because the slices share module payloads, `QtMainWindow`,
`QtProjectConfigWorkspace` and `QtQcWorkspace`.

## Architecture decisions

- The complete subject table is the only module-filter input.
- The persisted authority is versioned `qc_filter`; an empty filter means the
  complete list.
- Existing `select_filter` is read-only migration input. Qt never displays or
  writes legacy text/SQL.
- Module filters reuse `FilterExpression`, `TableViewService` validation and
  source-position results; no resolved ID list is persisted.
- Core owns filter normalization, matching identities and atomic module
  persistence. GUI owns dialogs, status and controller replacement.
- QC filter replacement prepares the new workflow/controller before the atomic
  settings commit, then swaps controllers.
- Score choices use native exclusive checkable `QPushButton` controls.
- Constant values stretch; the compact action column stays at the far right.

## Dependency graph

```text
T1 filter payload contract + legacy normalization
  └─ T2 Core persistence + module queue resolution
       ├─ T3 module-page filter transaction
       └─ T4 QC-page filter transaction and controller swap

T5 rectangular score choices
T6 constants-table sizing

T1–T6
  └─ T7 capacity, integration and full regression closure
```

T5 and T6 are logically independent but remain serial because T5 shares
`gui_qt/qc_workspace.py` with T4 and T6 shares
`gui_qt/project_config_workspace.py` with T3.

## Phase 1: filter contract and Core

### Task 1: Define the structured module-filter contract

**Description:** Add the optional `qc_filter` module payload, strict
serialization helpers for the existing `FilterExpression` shape, and bounded
conversion of supported legacy row-only `select_filter` values. Unsupported
legacy input must remain visible as a compatibility error.

**Acceptance criteria:**

- [ ] Missing/empty `qc_filter` normalizes to an empty `FilterExpression`, and
      non-empty grouped filters round-trip without loss.
- [ ] `QCModule` settings/export/rating payload round-trips preserve
      `qc_filter` while continuing to read modules without the field.
- [ ] Supported legacy simple row filters convert; unsupported legacy
      transforms fail loudly and are never interpreted as all rows.

**Verification:**

- [ ] RED then GREEN:
      `.venv/bin/python -m pytest tests/test_models/test_qcmodule_models.py tests/test_models/test_table_view_state.py tests/test_core/test_module_filter.py -q`
- [ ] Layering check:
      `.venv/bin/python -m pytest tests/test_architecture/test_project_layering.py -q`

**Dependencies:** None.

**Files likely touched:**

- `models/qcmodule.py`
- `models/table_view_state.py`
- `core/module_filter.py` (new)
- `tests/test_models/test_qcmodule_models.py`
- `tests/test_core/test_module_filter.py` (new)

**Estimated scope:** Medium, 5 files.

### Task 2: Persist filters and resolve module queues in Core

**Description:** Add one atomic module-filter update path and make
`ProjectContextService` resolve the selected module's identities from the
current complete subject table. Keep explicit identity validation and original
row order.

**Acceptance criteria:**

- [ ] Two modules can persist different filters without modifying each other's
      payload or unrelated module fields.
- [ ] Empty filter yields every `easyqcid` in complete-list order; grouped filters
      yield exactly the `TableViewService` source-position matches.
- [ ] Invalid/zero-match launch reports a clear error; persistence failure
      leaves settings and the current project context unchanged.

**Verification:**

- [ ] RED then GREEN:
      `.venv/bin/python -m pytest tests/test_core/test_configuration_service.py tests/test_core/test_project_context_service.py tests/test_core/test_project_service.py -q`
- [ ] Rating compatibility:
      `.venv/bin/python -m pytest tests/test_core/test_rating_service.py tests/test_models/test_rating_models.py -q`

**Dependencies:** Task 1.

**Files likely touched:**

- `core/project_service.py`
- `core/configuration_service.py`
- `core/project_context_service.py`
- `tests/test_core/test_configuration_service.py`
- `tests/test_core/test_project_context_service.py`

**Estimated scope:** Medium, 5 files.

## Checkpoint A: Core filter path

- [ ] Task 1 and Task 2 focused tests pass.
- [ ] Models still import no project-internal modules.
- [ ] No SQL/eval/new persistence engine is introduced.
- [ ] Existing modules without `qc_filter` still load.
- [ ] The product worktree contains no touched real project/runtime data.

## Phase 2: module and QC interactions

### Task 3: Add the module-page visual filter transaction

**Description:** Add the compact “质控名单” summary, set and clear actions to
the selected module editor. Reuse `FilterDialog` with ordinary complete-list
profiles and route the focused save through Core without overwriting other
module-form fields.

**Acceptance criteria:**

- [ ] Set opens the current Qt visual filter builder with the selected module's
      rule; cancel performs no write.
- [ ] Apply/clear atomically changes only that module's filter and updates
      “全部名单” or “已筛选：N 条”.
- [ ] Unsaved module-form values are either preserved during the focused
      transaction or the transaction is blocked with an explicit save/discard
      message; they are never silently overwritten.

**Verification:**

- [ ] RED then GREEN:
      `.venv/bin/python -m pytest tests/test_gui_qt/test_qt_project_config_workspace.py tests/test_gui_qt/test_qt_product_shell.py -q`
- [ ] Reduced viewport check remains green in the same focused suite.

**Dependencies:** Task 2.

**Files likely touched:**

- `gui_qt/project_config_workspace.py`
- `gui_qt/main_window.py`
- `tests/test_gui_qt/test_qt_project_config_workspace.py`
- `tests/test_gui_qt/test_qt_product_shell.py`

**Estimated scope:** Medium, 4 files.

### Task 4: Replace QC text search with the module filter

**Description:** Make `筛选名单` request the same visual `FilterDialog` from the
top-level controller, prepare a replacement module workflow from the complete
list, persist only after the candidate is ready, and swap controllers without a
partial queue.

**Acceptance criteria:**

- [ ] QC set/clear uses complete-list columns and changes the current module's
      saved rule; no text-search filter remains behind the action.
- [ ] Current identity is retained when still matched, otherwise the first
      matched identity opens; previous/next/save-next/double-click stay inside
      the new queue.
- [ ] Dirty draft, zero match, invalid filter, candidate-build failure and
      persistence failure leave the old controller and saved rule unchanged
      with a visible local error.

**Verification:**

- [ ] RED then GREEN:
      `.venv/bin/python -m pytest tests/test_gui_qt/test_qt_qc_workspace.py tests/test_gui_qt/test_qt_product_shell.py -q`
- [ ] Core queue validation remains green:
      `.venv/bin/python -m pytest tests/test_core/test_project_context_service.py -q`

**Dependencies:** Task 2 and Task 3.

**Files likely touched:**

- `gui_qt/qc_workspace.py`
- `gui_qt/main_window.py`
- `tests/test_gui_qt/test_qt_qc_workspace.py`
- `tests/test_gui_qt/test_qt_product_shell.py`

**Estimated scope:** Medium, 4 files.

## Checkpoint B: end-to-end module filtering

- [ ] Module-page filter save followed by module-row launch produces the exact
      module queue.
- [ ] QC-page filter replacement is transactional.
- [ ] Two modules retain independent rules across restart.
- [ ] Pre-QC-list and QC-results viewing filters remain independent and do not
      mutate modules.
- [ ] Result-only/rating columns cannot enter a module rule.

## Phase 3: native control and layout repairs

### Task 5: Render score options as pressed rectangular buttons

**Description:** Replace each score `QRadioButton` with an exclusive,
checkable native `QPushButton`, retaining the exact draft/save/read-only and
legacy-value behavior.

**Acceptance criteria:**

- [ ] Every configured choice and “未评” is rectangular and exactly one is
      checked.
- [ ] Click/keyboard activation updates the same score value; selected state
      remains visibly checked and read-only mode disables changes.
- [ ] Unknown saved values appear as one disabled rectangular legacy button
      without losing the value.

**Verification:**

- [ ] RED then GREEN:
      `.venv/bin/python -m pytest tests/test_gui_qt/test_qt_qc_workspace.py tests/test_core/test_qc_workflow_service.py -q`

**Dependencies:** Task 4, because both change the QC workspace.

**Files likely touched:**

- `gui_qt/qc_workspace.py`
- `tests/test_gui_qt/test_qt_qc_workspace.py`

**Estimated scope:** Small, 2 files.

### Task 6: Give constants values the stretch column

**Description:** Set explicit header resize modes and right-align the two
existing per-row actions in a compact operation cell.

**Acceptance criteria:**

- [ ] Name is content-sized, value stretches, and operation is
      resize-to-contents at the far right.
- [ ] Edit/delete remain visible, right-aligned and functional after refresh,
      filtering and reduced-width resize.
- [ ] Long values retain their tooltip and gain the available width instead of
      expanding the operation column.

**Verification:**

- [ ] RED then GREEN:
      `.venv/bin/python -m pytest tests/test_gui_qt/test_qt_project_config_workspace.py -q`

**Dependencies:** Task 3, because both change the configuration workspace.

**Files likely touched:**

- `gui_qt/project_config_workspace.py`
- `tests/test_gui_qt/test_qt_project_config_workspace.py`

**Estimated scope:** Small, 2 files.

## Checkpoint C: native UI behavior

- [ ] Score buttons have native checked/pressed behavior without broad QSS.
- [ ] Constant controls remain reachable at the approved reduced viewport.
- [ ] Qt component tests pass with the host/native style.
- [ ] No custom painting, forced Fusion style or bundled font is introduced.

## Phase 4: integration and capacity closure

### Task 7: Verify 100,000-row filtering and full regression

**Description:** Exercise the final feature path at the approved capacity,
review the combined diff across the five quality axes, and close the Ubuntu
evidence without claiming deferred platforms.

**Acceptance criteria:**

- [ ] A 100,000-row complete list can calculate a module filter and prepare its
      queue through the existing position-only path without freezing the GUI
      event loop.
- [ ] Focused Core/Qt integration, layering, compilation and diff hygiene pass.
- [ ] The complete Ubuntu Xvfb suite passes with only declared protected-fixture
      skips.

**Verification:**

- [ ] Focused Qt suite:
      `xvfb-run -a .venv/bin/python -m pytest tests/test_gui_qt -q`
- [ ] Capacity probe: use the existing table benchmark harness with a
      module-filter scenario; record command, elapsed time and peak RSS under
      `Tmp/run/`.
- [ ] Full suite:
      `xvfb-run -a .venv/bin/python -m pytest -q`
- [ ] Hygiene:
      `.venv/bin/python -m compileall -q core gui_qt models tests`
      and `git diff --check`.

**Dependencies:** Tasks 1–6.

**Files likely touched:**

- No production file is planned.
- Focused tests or the existing benchmark harness only if a missing permanent
  capacity guard is discovered before implementation.

**Estimated scope:** Small verification packet.

## Checkpoint D: completion

- [ ] Every source-design acceptance item maps to a passing test or recorded
      native/manual check.
- [ ] Full Ubuntu suite passes.
- [ ] Code review approves correctness, simplicity, layering, security and
      performance.
- [ ] No real runtime file is staged or modified.
- [ ] Task evidence is archived and a durable `dev/log/` completion entry
      exists.
- [ ] No push, Qt-default switch, tkinter removal, Windows/macOS claim or Docker
      work occurs without separate authorization.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Legacy filter cannot represent the new grouped model | High | Convert only bounded supported row filters; block unsupported rules visibly until replaced/cleared |
| Module filter accidentally uses rating columns | High | Build profiles only from the complete subject table and assert result-only columns are absent |
| Saving from QC closes or replaces the wrong controller | High | Prepare candidate first, save atomically second, swap last; keep context revision checks |
| Configuration refresh overwrites module-form edits | Medium | Preserve the form or block focused filter save until explicit save/discard |
| Filter matches zero rows | Medium | Allow preview/saved module rule but reject QC launch/replacement with a clear error |
| 100,000-row count blocks the event loop | Medium | Run validation/query through the existing background task controller and retain position-only results |
| Native checked button appearance varies | Low | Use standard checkable `QPushButton`; no pixel-identical claim or broad styling |
| Constants actions clip at narrow width | Low | Resize operation to contents and test the approved reduced viewport |

## Parallelization

No implementation tasks should run in parallel. T1/T2 establish shared
persistence and queue contracts; T3/T4 share `QtMainWindow`; T4/T5 share
`QtQcWorkspace`; T3/T6 share `QtProjectConfigWorkspace`. Serial execution avoids
conflicting edits and keeps every checkpoint independently green.

## Open questions

None. The user approved per-module independent filters, complete-list fallback,
the new Qt visual filter only, rectangular pressed score buttons and the
constant-table layout.
