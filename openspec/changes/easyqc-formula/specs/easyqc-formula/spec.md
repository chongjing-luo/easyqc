# EasyQC Formula specification

## Formula contract

The system SHALL accept exactly one bounded EasyQC expression with exact
bracketed column references, locale-stable operators, typed literals and a
closed function catalog.

The system SHALL reject statements, assignments, loops, attributes, objects,
unknown functions, user callables, Python, SQL, regex, file/network/process
access and formulas exceeding documented resource limits.

## Evaluation contract

The system SHALL normalize every expression into an immutable EasyQC AST before
evaluation. It SHALL use vectorized pandas Series operations and SHALL preserve
the source length and index.

Row-level failures SHALL remain explicit until selected away by `IF`, consumed
by `IFERROR`, or rejected by strict materialization. No failure SHALL silently
become blank.

## UI contract

The new-column dialog SHALL expose quick templates and advanced formula editing
over one shared formula text and evaluator. It SHALL provide column/function
insertion, a bounded preview and visible diagnostics.

Language switching SHALL preserve draft state and SHALL NOT translate formula
tokens or user column names.

## Persistence contract

The system SHALL persist only the generated ordinary column through existing
context-owned persistence. Formula text, AST, template state and intermediates
SHALL remain ephemeral.

Existing columns SHALL NOT be overwritten. Only a current import draft without
`ezqcid` MAY create that identity column; authoritative tables SHALL retain
existing identity validation.

## Performance contract

Full evaluation SHALL run outside the Qt thread. A representative 100,000-row
multi-column formula SHALL complete within 10 seconds and peak RSS SHALL remain
within 4 GiB on the existing benchmark host.
