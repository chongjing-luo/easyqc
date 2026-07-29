# QC list dialog deletion and import-conflict design

Status: approved direction from the user's instructions on 2026-07-26; written
specification awaiting final review.

## Goal

Make row and column deletion work like EasyQC's Filter and Column display
tools: the user opens a dedicated configuration dialog, defines all targets,
reviews the impact, and confirms one operation. Table selection is not an
input to deletion.

At the same time, make QC List Import support three explicit write modes and
deterministic row/column conflict policies instead of failing on every overlap
or producing ambiguous suffix columns.

This feature changes only the QC-list CSV or the detached import draft.
Historical rating JSON files remain outside every deletion and import path.

## Considered approaches

### A. Dedicated destructive dialogs reusing the existing editors — selected

- Row deletion embeds the same grouped `FilterPanel` used by Filter.
- Column deletion uses the same searchable checklist interaction as Column
  display, but check marks mean “delete” and ordering/pinning controls are not
  shown.
- Import mode and conflict policy are explicit, contextual controls.

This has the clearest intent, supports large tables, and keeps destructive
actions independent from transient cell/row selection.

### B. Delete the current filtered view or currently hidden columns

This minimizes new UI, but makes the target depend on unrelated view state.
Users could delete more than expected after forgetting an active filter or
hidden-column setting. Rejected as too implicit for destructive actions.

### C. Keep spreadsheet-style selected-row/current-column deletion

This is quick for a few visible records but does not scale and is easy to
misapply after sorting, filtering or paging. It is explicitly rejected by the
user.

## Row deletion

### Interaction

Clicking `删除行` opens a modal `Delete rows` dialog; it never immediately
deletes selected table rows.

The main area is the existing grouped filter editor:

- the Pre-QC dialog starts with the currently applied filter, when one exists;
- the import-draft dialog starts with the currently applied preview filter;
- the filter may be freely changed without changing the ordinary table view;
- an empty filter is rejected, so an accidental click cannot delete every row;
- the dialog evaluates the draft against the full source table, not only the
  current page;
- it displays the exact matching count before confirmation.

After the user applies the deletion draft, EasyQC resolves it to stable import
draft positions or exact `easyqcid` values and asks for a final confirmation:

```text
将删除 128 行。
现有评分记录不会被删除。是否继续？
```

Zero matches produce an inline error and no confirmation. Cancelling changes
nothing.

### Persistence

- QC List Import removes the resolved positions from its detached draft and
  rebuilds the preview once.
- Pre-QC passes the exact resolved `easyqcid` tuple through the existing
  background mutation controller to `ConfigurationService.delete_subject_rows`.
- Core reloads and validates the authoritative list, atomically replaces
  `Table/easyqc_all.csv`, and publishes `SUBJECTS_CHANGED` only after success.
- Historical rating files are neither enumerated nor modified.

The old multi-selection requirement and selection-enabled state are removed.
The import row context menu opens the same condition-based deletion dialog
instead of deleting selected rows.

## Column deletion

Clicking `删除列` opens a modal searchable checklist, independent of the current
cell. Multiple columns may be checked and deleted in one operation.

- The list shows the table's complete source schema, not only visible columns.
- Search narrows the list without changing checked items.
- In Pre-QC, `easyqcid` remains visible but disabled and marked protected.
- In QC List Import, `easyqcid` may be checked; the existing write validation
  then prevents writing until a valid identity column is restored.
- No selection is an inline error.
- Confirmation lists the exact column count and names.

The dialog reuses the visual language of Column display but has no reorder,
pin or visibility semantics. It returns only a unique tuple of exact column
names. The import draft uses `TableTransformEngine.drop_columns`; Pre-QC uses
the existing validated atomic Core deletion API.

## QC List Import write modes

The current radio pair becomes one explicit `写入方式` control with three
values. A second contextual `冲突处理` control is visible only when that mode
has a conflict decision.

### 1. 按 `easyqcid` 合并列

New columns are added by identity. Incoming identities not currently present
remain allowed, preserving the current outer-merge behavior.

When an incoming column name already exists in the Pre-QC list:

- `保留已有值` (default): matching existing identities keep their current
  values; new identities still receive the incoming values, and new columns
  are still imported.
- `用导入值更新`: for matching identities, a non-missing and non-empty
  incoming value replaces the existing value; a missing or empty incoming
  value leaves the existing value unchanged. New identities receive their
  incoming values.

EasyQC never creates automatic `_x` / `_y` suffix columns.
“Empty” means null or a whitespace-only string; numeric zero and boolean false
are valid update values.

### 2. 追加行

The incoming and existing tables must contain the same column set. Column order
may differ and is normalized to the existing order.

When an incoming `easyqcid` already exists:

- `去除重复` (default): retain the existing row and skip the incoming duplicate.
- `用导入行替换`: replace the complete existing row at its current position
  with the incoming row.

New identities are appended in incoming order. Existing list order remains
stable. Duplicate `easyqcid` values inside the import draft itself are rejected
because no deterministic winner was selected by the user.

### 3. 替换现有名单

The validated import draft becomes the entire Pre-QC list using the existing
atomic `replace_subjects` path. A stronger confirmation shows existing and
replacement row/column counts and states that removed identities' ratings are
retained but may become temporarily unjoined.

No conflict-policy control is shown in this mode.

## Duplicate columns inside the import draft

A draft with duplicate column names is invalid and cannot be written. EasyQC
reports the exact duplicate names and asks the user to delete or rename them in
the draft. It never guesses which same-named column should win.

## Import summary and confirmation

The status area updates when write mode or conflict policy changes. It reports:

- current rows and columns;
- incoming rows and columns;
- matching identities;
- new identities;
- duplicate identities to skip or replace;
- overlapping columns to preserve or update.

Writing always requires confirmation containing the chosen mode, conflict
policy and affected counts. The candidate is recomputed and validated in the
background immediately before the atomic save. Failure keeps both the
authoritative list and import draft unchanged and emits no success event.

## Core boundary

GUI continues to call Core; Core does not import GUI.

The Core import operation has one input contract:

```text
Input: validated incoming DataFrame + import mode + compatible conflict policy
Output: deterministic candidate written atomically to TABLE_ALL
Side effects: TABLE_ALL replacement and optional SUBJECTS_CHANGED event only
Errors: invalid mode/policy pair, duplicate identities/columns, schema mismatch,
        constant collision or write failure
Split trigger: rating deletion, undo/history, cross-project import or formula
               persistence is a separate feature
```

Compatibility entry points may map their old `rows` and `columns` modes to the
strict legacy policies, but the Qt import page uses the new explicit contract.

## Safety and concurrency

- Import drafts remain detached until confirmed.
- All authoritative writes stay off the Qt thread.
- Project switching, repeated writes, deletion and close are disabled while a
  list transaction is active.
- Validation finishes before the atomic replacement.
- A failed replacement publishes no success event.
- No operation imports the rating service or scans, writes, renames or removes
  `RatingFiles`.

## Acceptance criteria

1. Delete rows opens a filter-style dialog and does not depend on selected
   rows.
2. An empty or zero-match row-deletion draft cannot write.
3. Delete columns opens a searchable multi-column checklist and does not
   depend on the current cell.
4. Pre-QC protects `easyqcid`; import drafts may delete it.
5. Replace mode atomically replaces only the authoritative list.
6. Append + deduplicate keeps existing duplicates and appends only new rows.
7. Append + replace updates complete duplicate rows in place and appends new
   identities in order.
8. Merge + preserve ignores overlapping incoming columns without suffixes.
9. Merge + update changes only non-empty incoming values, adds new columns,
   and creates no suffixes.
10. Duplicate import-draft identities or duplicate internal column names fail
    before writing.
11. Chinese and English labels, summaries, confirmations and errors are
    covered.
12. Success and failure retain all rating paths and bytes.
13. Focused Qt/Core tests, full regression, layering and runtime-data
    isolation checks pass.

## Test strategy

- Core unit tests cover every valid mode/policy pair, ordering, blank update
  values, schema mismatch, internal duplicates, invalid pairs, atomic failure,
  event timing and byte-for-byte rating retention.
- Dialog tests cover filter draft construction, empty/zero-match behavior,
  checked-column persistence through search, protected identity and
  cancellation.
- Import-page tests cover contextual controls, summaries, confirmations,
  background responsiveness and one-event refresh.
- Product-shell tests cover Pre-QC integration, no-selection operation,
  project-control locking and retained ratings.
- Full regression and architecture/data-isolation tests prove the feature does
  not enter real runtime data or violate GUI → Core → Models layering.
