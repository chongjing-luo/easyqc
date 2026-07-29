# Proposal: QC list row and column deletion

## Why

Users need to correct imported drafts and the authoritative Pre-QC list without
re-importing an entire table. Rating history must remain an independent audit
record.

## What changes

- Add multi-row and selected-column deletion to QC List Import.
- Add multi-row and selected-column deletion to Pre-QC List.
- Protect authoritative `easyqcid`.
- Confirm that rating records are retained.
- Reuse validated atomic `easyqc_all.csv` persistence and context refresh.

## Impact

No database, schema, rating-format or project-format migration. Historical
ratings can become temporarily unjoined when their identity leaves the master
list, but the files remain recoverable.

## Approval

Approved by the user's direct request on 2026-07-26.
