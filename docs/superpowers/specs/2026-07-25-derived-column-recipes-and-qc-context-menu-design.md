# Derived-column recipes and QC row context menus

Status: approved for implementation on 2026-07-25.

## Outcome

EasyQC replaces the derived-column expression editor with a simplified Power
Query-style, ordered transformation chain. Users select a source, add safe
typed steps, inspect a before/after preview, and materialize one new ordinary
column. Python, SQL, shell commands, `eval`, user callables, and arbitrary regex
are not accepted.

The Pre-QC list, QC results, and active QC queue gain one shared row context
menu. It shows all configured modules (disabled when the identity is outside a
module's independent list) and existing exact module/rater rating records.
Module actions open the existing workflow. Record actions open a forced
read-only workflow from the saved full legacy module payload.

## Recipe interaction

1. Enter a non-colliding new column name.
2. Choose one primary source column.
3. Add, delete, or reorder operation cards.
4. Each card consumes the previous result and may reference another existing
   column or literal.
5. Preview shows `ezqcid`, original source value, and final value.
6. Generate executes the complete table in a worker and atomically writes only
   the final column.

Operation families include text cleanup/case/length, literal replace,
prefix/suffix removal, slicing, delimiter and boundary extraction, path-text
conveniences, prepend/append, numeric conversion/arithmetic/rounding,
conditional generation, and explicit null filling.

Errors fail loud by default and identify the step. Explicit blank/keep-input
policies may be selected per step. Recipes and intermediate values are not
persisted.

## Security and performance

- Closed operation IDs and typed scalar/column/current references only.
- No filesystem access for path-like values; paths are text.
- No per-row user code and no new dependency.
- Bounded deterministic preview.
- Vectorized pandas complete-table execution in the existing background worker.
- Failed transform or validation performs no write; existing atomic save
  behavior remains authoritative.

## Architecture seams

- Models: immutable recipe and row-context contracts.
- Core: `TableTransformEngine` executes recipes;
  `ProjectContextService` resolves menu facts and normal/read-only workflows.
- GUI: focused recipe editor/dialog and shared `QMenu` renderer.
- Persistence: unchanged CSV/JSON authority.

The detailed requirements and architecture are maintained in
`dev/table-derived-columns-and-qc-context-menu/`.
