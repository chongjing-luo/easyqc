# EasyQC Formula design

Status: approved for implementation by the user's “执行这个方案”.

## Outcome

The current Power Query-style step editor is replaced by one safe
Excel/VBA-familiar expression system. The dialog exposes two entry paths:

- quick templates for ordinary users;
- an advanced multi-line formula editor.

Templates generate visible formula text. Both paths use the same parser, AST,
function catalog, preview and vectorized evaluator.

```text
quick template ─┐
                ├─> formula -> safe AST -> pandas Series -> preview -> column
advanced text ──┘
```

The product calls this language EasyQC Formula, not VBA. It supports one
expression and exact `[column name]` references:

```text
IF([site] = "A", UPPER(TEXTBEFORE([filename], "_")), "OTHER")
[parent path] & "/" & [filename]
ROUND(([age] - [baseline_age]) / 12, 1)
"fixed value"
```

## Boundaries

- Lark parses a small EasyQC grammar; it does not execute anything.
- A closed immutable AST is interpreted through pandas-vectorized, whitelisted
  operators and functions.
- No VBA statements, Python, SQL, regex, attributes, objects, user functions,
  filesystem, network, Shell, `eval` or `exec`.
- Formula text and intermediate state are not saved; only the final ordinary
  column is materialized through existing persistence.
- Existing columns remain immutable. Only an import draft lacking `ezqcid` may
  create that identity column.

## Language and functions

The syntax is locale-stable: English case-insensitive function names, comma
arguments, decimal point, double-quoted strings, optional leading `=`, and
exact case-sensitive bracketed columns. The initial catalog is:

- `IF`, `IFERROR`, `ISBLANK`, `COALESCE`, `BLANK`;
- `TRIM`, `UPPER`, `LOWER`, `LEN`, `LEFT`, `RIGHT`, `MID`, `FIND`,
  `TEXTBEFORE`, `TEXTAFTER`, `SUBSTITUTE`;
- `VALUE`, `ABS`, `ROUND`;
- `PATHNAME`, `PARENTPATH`, `EXTENSION`, `STEM`;
- `OR`, `AND`, `NOT`, comparisons, arithmetic and `&`.

Errors are row-aligned values during evaluation. `IF` keeps errors only from
the selected branch and `IFERROR` may consume them. Any unresolved error blocks
generation and reports a count plus bounded examples.

## Components

- `models/derived_formula.py`: immutable `DerivedColumnFormula`.
- `core/formula_parser.py`: grammar, diagnostics and closed AST.
- `core/formula_engine.py`: metadata, validation and vector execution.
- `core/table_transform.py`: strict detached one-column materialization.
- `gui_qt/formula_editor.py`: quick templates, insertion and formula text.
- `gui_qt/derived_column_dialog.py`: reuse preview/worker/stale-result shell.
- existing service/page callbacks: migrate request type without changing their
  data authority.

## GUI

The dialog contains a target name, quick/advanced tabs, a multi-line editor,
column insertion, a searchable function/template panel, syntax status, bounded
preview, visible errors and Generate/Cancel buttons. Bilingual switching
preserves all draft state; only UI descriptions translate.

## Verification

- grammar/precedence/escaping/resource/security rejection tests;
- operator/function/null/type/error tests;
- quick-template equivalence tests;
- import/Pre-QC/results source and `ezqcid` tests;
- stale worker and bilingual Qt tests;
- 100,000-row ≤10-second and ≤4-GiB gate;
- no-formula-persistence and full-suite/layering tests.

## Spec self-review

- Placeholder scan: none.
- Consistency: one formula core owns both UI paths; no second recipe semantics.
- Scope: row-preserving derived columns only; no regex, dates, aggregates or
  formula persistence.
- Ambiguities resolved: exact column matching, fixed locale-independent syntax,
  one-based `MID`/`FIND`, scalar-only non-vectorizable parameters, explicit
  row-error semantics and import-only missing-identity creation.
