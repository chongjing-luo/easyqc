# Proposal: EasyQC Formula

## Why

The current ordered step chain is safe but becomes cumbersome for nested
conditions, multi-column text composition and general format extraction.
Complete VBA would restore arbitrary-code and platform-host risks.

## What changes

- Introduce a restricted Excel/VBA-style single-expression language.
- Add a Lark parser that normalizes into an EasyQC-owned immutable AST.
- Add a pandas-vectorized whitelist evaluator with explicit row errors.
- Replace the step-chain GUI with quick templates plus an advanced formula
  editor over the same core.
- Migrate import draft, Pre-QC and results new-column callbacks.
- Persist only final values; do not persist formulas.

## Impact

- New small runtime dependency: `lark==1.3.1`.
- New Models/Core/Qt modules and tests.
- No project/settings/rating/CSV schema migration.
- ADR-012 changes from “no formula language” to “only EasyQC Formula”.

## Approval

Approved by the user's “执行这个方案” after review of the Level B research and
recommended design.
