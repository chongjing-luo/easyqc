# Score ordering, wrapping module tags, and complete long results

## Status

- Date: 2026-08-06
- User approval: approved with “执行” after selecting option A for both long-table master columns and score-order controls
- Scope: Qt project/module-template editors and the derived QC-results long projection
- Storage impact: none; module and rating JSON remain schema version 3

## Problem

Three related usability gaps remain in the module/results workflow:

1. In the project module editor, Add Score and Remove Score appear below the
   tag editor, and neither module editor can reorder score rows.
2. Module tag chips continue horizontally inside a scroll area instead of
   wrapping and increasing the tag section height.
3. The QC-results long view deliberately allowlists only rating facts, so the
   Master QC list fields needed to interpret each rated item disappear.

## Goals

1. Put score-row actions immediately below the score table and above Tags in
   both project-module and cross-project/template-module editors.
2. Add stable Up and Down score-row controls. The visible row order is the save
   order and is re-keyed to numeric strings `1..N` by the existing save path.
3. Wrap tag chips onto additional rows and grow the tag editor vertically;
   never extend the chip strip indefinitely or add an internal horizontal bar.
4. Add every Master QC list column to each actual rating row in long results,
   without losing or silently overwriting colliding columns.
5. Preserve the current one-rating-load context, composite row identity,
   bilingual behavior, JSON/CSV authorities, and GUI → Core → Models layering.

## Non-goals

- Drag-and-drop score ordering.
- Reordering score options inside one score row.
- Persisting a separate score-order field or changing the module JSON schema.
- Configurable subsets of Master QC list columns in long mode.
- Fabricating unrated module/rater combinations or unrated long rows.
- Exposing viewer commands, rating filenames/paths, module payloads, or other
  flattened internal rating fields.
- Changing wide-results behavior, rating identity, rating filenames, or the
  new-column Formula system.

## Considered approaches

### A. Core-owned merge with shared native GUI helpers — selected

- Add shared score-table row movement and button-state behavior.
- Replace the tag editor's horizontal scroll strip with a native flow layout.
- Join the validated Master QC list to the sanitized professional long rating
  frame in Core before constructing `TableViewService`.

This keeps persistence and data projection out of GUI code, produces one
behavior in both module editors, and is the smallest design that satisfies all
three requests.

### B. GUI-only long-table join and direct table manipulation — rejected

This is superficially shorter but makes the results page own data contracts,
duplicates Core validation, and risks different export/filter/right-click data
from the displayed data.

### C. Configurable/lazy long-view schema plus drag ordering — rejected

This could reduce memory or offer more controls, but introduces another schema
configuration surface, asynchronous mode loading, and platform-sensitive drag
behavior that the user did not request.

## Score editor design

### Layout

Both module editors use this vertical order:

```text
Scores title
Score table
[Add score] [Remove score] [Move up] [Move down]
Tags title / group
Wrapping tag editor
```

The project editor moves its existing score toolbar from below the tag editor
to directly below the score table. The template editor retains its current
score action row and adds Move Up and Move Down in the same order.

### Movement contract

A new focused `gui_qt/module_score_table.py` owns the existing native-height
helper plus score movement/state helpers, so both editors use one behavior.
Its movement operation receives one `QTableWidget` and delta `-1` or `+1`:

- a non-widget input or unsupported delta raises before mutation;
- no selected row or a boundary move changes nothing;
- a valid move transfers the complete row, including both cells and their Qt
  item metadata;
- the moved row and original current column remain selected;
- the table height is resynchronized, although a pure move does not change row
  count;
- the helper returns whether a move occurred and performs no persistence.

Move Up is enabled only when the selected row is greater than zero. Move Down
is enabled only when the selected row is before the last row. Add, remove,
load, clear, discard, selection change, and language change refresh these
states. Existing minimum-row/deletion semantics are otherwise retained.

### Save behavior

Both existing save adapters already enumerate visible table rows and rebuild
the score mapping with keys `"1"`, `"2"`, … . They remain authoritative. A
move therefore changes order without adding a schema field or mutating the
saved module until the user presses Save.

## Wrapping tag editor design

`ModuleTagEditor` keeps its ordered `_TagDraft` model and edit/add/remove
transactions. Only layout ownership changes:

- replace `QScrollArea` + one `QHBoxLayout` with a private native `FlowLayout`;
- lay out complete chip frames from left to right and wrap at the current
  available width;
- implement Qt height-for-width behavior and call `updateGeometry()` after
  add/edit/remove/load, resize, font/style, and language changes;
- let the surrounding module editor scroll vertically when the full editor is
  taller than the page;
- show no internal horizontal or vertical tag scrollbar;
- retain native size hints, spacing tokens, keyboard focus, accessible names,
  separate edit/remove targets, duplicate labels, and exact user text.

An individually overlong tag is elided to the available chip-width limit and
keeps its full text in its tooltip and edit dialog. This prevents one label
from forcing horizontal page growth while preserving the stored label exactly.

## Complete professional long-results design

### Two-step projection boundary

The existing `professional_long_results(raw_rating_frame)` remains the strict
rating-fact allowlist. A second Core operation attaches Master QC list columns
to that already sanitized frame:

```text
raw schema-v3 ratings
  -> professional_long_results
  -> attach_master_columns_to_long(professional_long, master_qc_list)
  -> TableViewService
  -> Qt QC Results long mode / filter / sort / export / row menu
```

The GUI never joins DataFrames and never sees excluded raw rating fields.

### Row and column contract

- One output row exists for each actual unique
  `(easyqcid, module_name, rater)` rating identity.
- Master rows without a rating do not appear in long mode.
- Every long rating must match exactly one Master QC list row by `easyqcid`;
  missing matches fail loudly instead of producing partly contextualized rows.
- The Master QC list must have a nonblank, unique `easyqcid` and no duplicate
  column names.

Output order is:

```text
easyqcid
all remaining Master QC list columns in original order
module_name, rater
score1..N in natural numeric order
tag1..N in natural numeric order
notes, time
```

### Collision policy

`easyqcid` is the shared join key and appears once. Every other Master QC list
column is preserved:

1. retain its original name when that name is not already reserved by a long
   rating column and has not already been assigned;
2. otherwise prefix it with `master.`;
3. if the prefixed name still collides, prefix `master.` repeatedly until the
   name is unique.

Examples:

```text
Master column notes        -> master.notes
Master column score1       -> master.score1
Master columns notes and master.notes
                           -> master.notes and master.master.notes
```

No column is silently dropped or overwritten. The renaming is deterministic
for a fixed Master QC list schema.

### Empty and refresh behavior

With no ratings, long mode has zero rows but still exposes the complete Master
column schema plus `module_name`, `rater`, `notes`, and `time`. A project refresh
rebuilds wide and long services from the same single accepted rating load.
Switching modes still performs no scan, pivot, join, or file I/O.

### Performance boundary

Denormalizing Master fields across multiple rating rows intentionally costs
memory. The implementation must keep vectorized pandas joining, avoid Python
row loops, preserve one rating scan, and run a synthetic 100,000-row × 300
numeric-Master-column attach-plus-`TableViewService` time/memory probe on the
documented 16 GiB support envelope. Its gates are `<10 s` and `<4096 MiB` peak
RSS, consistent with the existing table/Formula envelope. The result must be
reported with its limitations; native Qt painting and arbitrary real-world
object/string distributions are not inferred from the Core probe.

## Error handling

- Invalid score-table inputs or movement deltas fail loudly at the helper
  boundary; ordinary boundary/no-selection moves are safe no-ops with disabled
  buttons.
- Malformed tag drafts retain the existing visible validation errors.
- Duplicate/blank Master identities, duplicate columns, and rating identities
  not found in Master raise visible Core errors and prevent context acceptance.
- No silent fallback to the old rating-only long view is allowed.

## Bilingual and accessibility behavior

- Add Chinese and English strings for Move Up and Move Down, including
  accessible names/tooltips.
- Language switching changes only UI chrome; score/tag/master values and
  collision-renamed column names are data and remain byte-for-byte unchanged.
- Score controls remain keyboard reachable. Flow-layout chips retain separate
  focusable edit and remove buttons.

## Verification

### Score rows

- Project and template layouts place all four actions above Tags.
- Up/down moves complete rows, preserves selection, handles first/last/no
  selection, updates button states, remains draft-only, and saves exact order.
- Project/template load, discard, add, remove and bilingual regressions pass.

### Tags

- Narrow width produces more than one chip row and a greater editor height.
- Wider width reduces row count/height without changing tag order.
- No internal scrollbar exists; the outer editor owns vertical overflow.
- Edit/remove/add/blank/duplicate/i18n/accessibility behavior remains green.
- Long single labels elide visually while full text remains stored and exposed
  through tooltip/editing.

### Long results

- All Master columns appear in original order for every actual rating row.
- Collision examples above preserve every column deterministically.
- Multiple module/rater rows duplicate the correct Master facts and retain the
  composite key.
- Unrated Master rows are absent; orphan ratings, duplicate Master identity and
  duplicate columns fail loudly.
- Empty ratings retain the complete schema.
- Filter/sort/columns/export/right-click work with the enriched long service.
- Context preparation still calls rating loading once and mode toggles do no
  additional scan or join.
- Focused 100k performance/memory probe and complete two-lane test matrix pass.

## Expected implementation owners

- `gui_qt/project_config_workspace.py`
- `gui_qt/module_template_editor.py`
- `gui_qt/module_tag_editor.py`
- new `gui_qt/module_score_table.py` for native height, row movement and action
  state shared by both editors
- `gui_qt/i18n.py`
- `core/rating_service.py`
- `core/project_context_service.py`
- focused Core and Qt tests for the owners above

Runtime project data, `projects.json`, module files, rating files, templates,
backups, locks, and user project directories are explicitly out of scope.
