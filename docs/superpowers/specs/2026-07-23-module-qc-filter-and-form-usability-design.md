# Module QC filter and form usability design

Status: approved by the user's 2026-07-23 “执行” instruction.

## Scope

This change delivers three connected Qt usability improvements:

1. give every QC module its own structured row filter, calculated from the
   project's complete QC list;
2. replace circular QC score choices with exclusive rectangular buttons that
   visibly remain pressed when selected;
3. make the constant-value column consume the available table width while the
   two row actions remain compact and right-aligned.

The change does not make the pre-QC-list or QC-results page state authoritative
for a module. It does not save resolved identity lists, reuse the old GUI's
text/SQL editor, add a database, change rating meaning, or add a broad Qt
stylesheet.

## Product rules

- The pre-QC list and QC results are both views of the complete project list.
  Their filter, sort and column states remain page-local viewing state.
- Each module independently owns one optional row filter.
- An absent or empty module filter means the complete project list.
- A module filter uses only ordinary columns from the complete project list.
  Rating-derived result columns, sort rules, column visibility and derived
  formulas are not part of a module filter.
- A module queue is recalculated from the current complete list whenever QC is
  started. The queue preserves complete-list row order.
- Saving a rule, rather than a resolved `ezqcid` list, keeps module membership
  current when rows or values in the complete list change.

## Considered approaches

### A. Structured per-module rule

Persist the same typed `FilterExpression` semantics used by the Qt table
workspace, then evaluate it through Core against the complete subject table.
This supports the current grouped AND/OR editor, typed operators and validation
without presenting JSON to the user. This is the selected approach.

### B. Persist the resolved identity list

This is simple at launch time but becomes stale when the complete list changes
and can add up to 100,000 identifiers to settings. It is rejected.

### C. Continue the legacy text/SQL rule

This preserves the old editor directly but cannot represent the current Qt
filter model safely or completely. It also reintroduces an interaction the user
explicitly rejected. It is retained only as a bounded read-compatibility input.

## Persistence contract

The authoritative module payload gains an optional `qc_filter` object:

```json
{
  "schema_version": 1,
  "group_join": "all",
  "groups": [
    {
      "group_id": "group-1",
      "join": "all",
      "conditions": [
        {
          "column": "site",
          "operator": "==",
          "value": "A",
          "condition_id": "condition-1",
          "enabled": true
        }
      ]
    }
  ]
}
```

This is persistence only, not a JSON-editing interface. The Qt GUI always uses
the existing visual filter builder. The contract has bounded groups and
conditions, strict field validation, JSON-safe values and an explicit schema
version. Empty `groups` is the canonical representation of no filter.

`qc_filter` is included in settings module payloads, exported modules and the
full module snapshot embedded in rating JSON. Existing files without the field
normalize to an empty filter.

Legacy `select_filter` has no authority once `qc_filter` exists. A supported
legacy simple row filter may be converted in Core to the structured form when a
module is first opened. The old string is never displayed or edited in Qt.
After the user confirms a new structured filter, persistence writes
`qc_filter` and clears that module's legacy `select_filter`, preventing two
conflicting authorities. An unsupported legacy filter is not silently treated
as the complete list: the module shows a visible compatibility error and QC
launch remains blocked until the user replaces or clears it with the new
visual editor.

All settings and rating writes continue through the existing atomic Core
writers. No real project is migrated merely by opening it; conversion becomes
durable only after an explicit module-filter save or another explicit module
save.

## Core data flow

1. Core receives a module and the current complete subject DataFrame.
2. It normalizes `qc_filter` into a `FilterExpression` and validates every
   referenced column and typed value with `TableViewService`.
3. It applies only the row filter and retains the read-only source-position
   result. It does not apply a page's sort, columns or pagination state.
4. It resolves the matching positions to an ordered `ezqcid` tuple.
5. `ProjectContextService.create_qc_workflow()` validates that the identities
   are unique, nonblank and part of the current project before materializing
   the bounded workflow subject table.
6. The QC controller receives exactly that module-specific queue.

For the normal maximum of 100,000 rows, filtering stays on the existing
pandas/position-only path. Match-count previews run through the current Qt task
runner so the GUI event loop remains responsive. No second full authoritative
table, cache or persisted identity list is introduced.

## Module-page interaction

The selected module editor gains a compact “质控名单” section after basic
module information and before scores:

- a summary reading “全部名单” or “已筛选：N 条”;
- `设置筛选`, opening the existing Qt `FilterDialog` against ordinary complete
  list columns;
- `清除筛选`, returning the module to the complete list.

The module list itself does not gain another count or repeated filter label.
Its existing row-owned `启动质控` button uses the saved rule.

Filter editing is a focused transaction separate from the rest of the module
form. Applying it validates and saves only the selected module's `qc_filter`;
it must not overwrite unsaved name, label, rater, score, tag or viewer-command
widgets. Cancelling closes the dialog with no state or file change. Clear
requires the same validation/save route and updates the summary to “全部名单”.

If the complete list changes, the displayed match count is recalculated. A
missing referenced column or invalid migrated value produces a module-local
error while preserving the saved rule for correction; it never degrades
silently to all rows.

## QC-page interaction

The existing `筛选名单` action stops being a text search over the already-built
queue. It opens the same Qt `FilterDialog`, initialized from the current
module's saved rule and limited to complete-list columns.

Applying from the QC controller follows a transactional sequence:

1. reject the change while a rating draft is dirty;
2. validate and preview the new filter against the current complete list;
3. reject an empty match without changing settings or the live workflow;
4. retain the current identity when it still matches, otherwise select the
   first matched identity;
5. prepare the complete replacement workflow and hidden controller in memory;
6. atomically persist the module rule;
7. install and show the already-prepared controller.

The action tooltip and module-page summary show the matched count. Clearing the
filter rebuilds the queue from the complete list. Previous/next, save-next and
double-click navigation operate only inside the active module queue.

The old free-text queue-search behavior is removed from `筛选名单`; it is not a
second filter layer. A future quick-search field would be a separate,
explicitly designed feature.

## Rectangular score choices

Every score option, including “未评”, becomes a checkable `QPushButton` inside
the existing exclusive `QButtonGroup`.

- Pressing a button immediately updates the same score draft value as today.
- The selected button stays in the native checked/pressed state.
- Mouse press/release uses the platform's native sunken feedback.
- Space/keyboard activation, focus indication, accessible names, read-only
  disabling and legacy-value display remain supported.
- A saved value that is no longer in the configured choices appears as one
  disabled rectangular legacy-value button, matching the current fail-visible
  behavior.

No broad QSS, forced Qt style, custom painting or bundled font is introduced.
The control is intentionally platform-native so it does not clip or lose input
behavior across supported operating systems.

## Constant-table layout

The constants table keeps its three logical columns but changes resize policy:

- constant name: content-sized with a practical minimum;
- value: stretch, consuming all remaining width;
- actions: resize-to-contents at the far right.

The action cell owns one zero-margin horizontal layout containing the existing
edit and delete buttons aligned right. Button text and behavior are unchanged.
Long values use the existing tooltip and remain reachable without forcing the
operation column to stretch. Reduced-width windows preserve both action buttons
and allow the value column to yield before controls are clipped.

## Error and transaction behavior

- Invalid module filters, unknown columns, incompatible typed values, duplicate
  condition IDs and limit violations show an owning-page error and write
  nothing.
- A zero-row filter is valid as a draft/preview but cannot replace a live QC
  queue; launch or QC-page apply explains that the current module matches no
  rows.
- A module with a saved zero-row result remains saved but cannot start QC until
  the list changes or the filter is edited/cleared.
- Dirty rating state blocks filter replacement.
- A failed candidate-workflow build or settings write discards the candidate,
  leaves both the saved rule and previous live controller unchanged, and does
  not produce a partial queue.
- Score and constant UI changes do not alter persistence schemas or value
  meaning.

## Verification

Automated tests must cover:

- strict `qc_filter` round-trip, empty normalization and full rating-payload
  preservation;
- supported legacy row-filter conversion and fail-visible unsupported legacy
  input without opening the old editor;
- independent filters for two modules over one complete list;
- absent/empty filter producing the complete list in source order;
- grouped AND/OR and typed operators producing the same identities as the Qt
  table filter;
- result-only columns being unavailable to module filters;
- module-page set, cancel, clear, summary count and atomic focused save;
- module-row launch using only the selected module's matched identities;
- QC-page apply retaining the current identity when possible and otherwise
  selecting the first match;
- dirty-draft, zero-match, invalid-filter and persistence-failure preservation
  of the existing controller;
- previous/next, save-next and double-click staying inside the module queue;
- checkable rectangular score buttons, exclusive checked state, draft/save,
  read-only and legacy-value behavior;
- constants name/value/action resize modes, right-aligned two-button cell and
  reduced-width reachability;
- 100,000-row filter responsiveness on the existing position-only path;
- focused Core/Qt tests and the complete Ubuntu Xvfb repository suite.

## Non-goals

- Sharing one module filter across modules.
- Making pre-QC-list or QC-results viewing state change a module.
- Saving a resolved identity list.
- Allowing rating-derived columns in a module rule.
- Restoring the old GUI's text, shorthand, SQL or JSON editor.
- Adding a quick-search layer inside the QC queue.
- Changing the default GUI, removing tkinter rollback code, or claiming native
  Windows/macOS verification.
