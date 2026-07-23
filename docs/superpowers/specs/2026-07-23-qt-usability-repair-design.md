# Qt usability repair design

Status: approved by the user's 2026-07-23 “执行” instruction.

## Scope

This repair covers six connected usability defects in the Qt product shell:

1. remove repeated module title/name/label text;
2. make every QC launch produce visible success or failure feedback;
3. increase primary-navigation font size and row separation;
4. render toolbar actions as recognisable native bordered controls;
5. keep the frozen `ezqcid` surface aligned with the remaining columns;
6. add a one-time derived-column operation that persists the generated values.

The repair does not alter the tkinter rollback adapter, rating JSON, module
settings schema, or existing project data until a user explicitly invokes the
new derived-column action at runtime.

## Considered approaches

### A. One table without a frozen identity column

This gives one native horizontal and vertical scrollbar and is the smallest
implementation. It removes the approved always-visible `ezqcid` behavior, so it
is rejected.

### B. A custom frozen-table subclass with an overlaid identity viewport

This can imitate spreadsheet controls closely, but introduces custom event,
geometry, focus, selection and accessibility handling. It has the highest
cross-platform maintenance cost and is rejected for this repair.

### C. Existing twin views with shared external scrollbars

Keep the bounded shared model and frozen identity view, hide both views'
internal scrollbars, and place one horizontal scrollbar below the complete
surface plus one vertical scrollbar beside both views. The external bars mirror
the main view's ranges and drive both views. This preserves identity pinning,
gives both viewports exactly the same height and removes the source of
bottom-row drift. This is the selected approach.

## Interaction design

### Module rows and launch feedback

Each module row shows the display label once as its title and the technical
module name plus rater/read-only state once as secondary text. Score and tag
counts are omitted because they repeat information visible in the selected
module editor. The hidden `QListWidgetItem` text is not used as a second visual
label.

Selecting “启动质控” immediately selects the row. On success the button reports
“已启动”, the module page shows a short status message, and the controller
window is shown, raised and activated. On failure the module-page message shows
the concrete error instead of writing only to the project page, which may be
hidden.

### Navigation and buttons

The primary navigation uses the host font enlarged by one point, metric-derived
row heights and four pixels of inter-item spacing. The active project remains a
secondary line under “项目选择”, with a row height calculated for two lines.

Toolbar actions remain standard `QToolButton` controls so Qt overflow,
shortcuts and accessibility continue to work. Auto-raise is disabled for
ordinary actions, causing the active platform style to draw a normal button
frame. No global theme, forced Qt style or broad stylesheet is introduced.

### Frozen table scrolling

The frozen and main views retain one shared model, selection model, row heights
and vertical scroll mode. Their internal scrollbars are hidden. One external
horizontal bar spans the full table width and controls only the horizontally
scrollable main columns while `ezqcid` stays fixed. One external vertical bar
controls both views. Range, page step, single step and value are synchronised
after model resets, column changes, resizes and style/font changes.

### One-time derived column

The pre-QC-list toolbar gains “新增列” next to filter/sort/column controls. A
focused dialog contains:

- a new-column-name field;
- a multiline expression field;
- the existing columns, which can be inserted into the expression;
- a preview of the first ten calculated rows;
- “取消” and “生成列” actions.

Expressions use the existing restricted `ExpressionParser` through
`TableTransformEngine`; `eval` and SQL remain forbidden. The target name must be
new, non-empty and different from `ezqcid` and existing constants. Preview does
not write data. Confirmation recalculates against the current full
`ezqc_all.csv`, validates the complete result, atomically replaces that CSV via
`ConfigurationService`, publishes `SUBJECTS_CHANGED`, and refreshes both the
pre-QC list and results context.

The generated values are ordinary CSV values. The expression is not stored and
future source-column edits do not recalculate the new column.

The QC-results table does not expose this write action because
`ezqc_qctable.csv` is derived from ratings and can be rebuilt; writing an
untracked column there would be misleading and non-durable.

## Failure behavior

- Invalid expression, unknown columns, invalid target name, duplicate target
  column or type/length mismatch leaves all project files unchanged and shows
  the exact error in the dialog.
- A launch failure remains on the module page until the next launch or an
  explicit successful action.
- Scroll synchronization never suppresses exceptions silently; signal feedback
  is prevented with explicit signal blocking where required.

## Verification

Tests must cover:

- one visual occurrence of each module label and exact module launch routing;
- visible module-local success and failure messages;
- navigation font/spacing and metric-derived one-line/two-line heights;
- non-auto-raised toolbar buttons;
- one full-width external horizontal scrollbar and one shared vertical
  scrollbar, including last-row alignment after scrolling to the maximum;
- derived-column preview without writes;
- confirmation writing a new CSV column atomically through Core;
- rejection of duplicate names and unsafe expressions without writes;
- refresh of the live table after the subjects-changed event;
- existing Qt unit suite and full repository suite.
