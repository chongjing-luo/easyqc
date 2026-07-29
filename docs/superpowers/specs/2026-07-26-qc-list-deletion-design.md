# QC list row and column deletion design

Status: approved by the user's direct request on 2026-07-26.

## Behavior

QC List Import and Pre-QC List each expose bordered `删除行` and `删除列`
actions beside the existing table actions. Row deletion supports the current
multi-selection. Column deletion targets the current cell's column. The UI
shows a confirmation containing the exact row count or column name and states
that existing rating records are retained.

The import page edits only its detached draft. It may remove any column,
including `easyqcid`; the existing merge validation then prevents writing until a
valid identity column is restored.

The Pre-QC page removes exact current `easyqcid` identities or exact ordinary
column names from the authoritative list. It never permits deletion of
`easyqcid`. A successful operation uses existing validation and atomic CSV
replacement, then publishes the existing subject-change event so all views
reload. Missing/stale targets fail without a partial update.

## Safety and data flow

```text
Import selection -> draft-position mapping -> detached replacement -> preview

Pre-QC selection -> exact identities/column -> ConfigurationService
 -> reload authoritative easyqc_all.csv -> validate candidate
 -> atomic TableService replacement -> SUBJECTS_CHANGED -> context refresh

RatingFiles ----------------------------------------------------> untouched
```

No deletion path imports or calls the rating service, scans `RatingFiles`, or
removes a file. A rating whose identity is removed from the master list remains
recoverable but may no longer join into the current result view; restoring the
same identity restores the association.

## Error and concurrency behavior

Empty selections do nothing and show a clear error. Duplicate, unknown or
stale Core targets fail loudly. Pre-QC actions are disabled while project
context changes, formula writes or unsaved QC edits make the view unsafe.
Import actions are disabled while its revisioned worker or formula worker is
busy. A failed save emits no change event and leaves both the list and ratings
unchanged.

## Verification

Tests cover filtered/sorted multi-row mapping, column deletion, protected
identity, draft-only behavior, atomic save failure, stale/busy controls,
Chinese/English text, and byte-for-byte rating retention. All fixtures use
temporary synthetic projects; real runtime data is excluded.
