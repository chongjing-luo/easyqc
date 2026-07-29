# Stable rating JSON filename design

Date: 2026-07-28
Status: user-approved; implemented and verified with synthetic data
Architecture:
`../../../../dev/easyqc/03_architecture_design/architecture_design_rating_filename_20260728_100848.md`

## Goal

Store one latest atomic rating snapshot for each exact
`(module_name, rater, ezqcid)` while retaining enough identity in the directory,
filename and JSON body to detect misplaced files and recover them safely.

## Identifier policy

The three values are ASCII internal IDs:

```regex
module_name = ^[A-Za-z0-9_]+$
rater       = ^[A-Za-z0-9_]+$
ezqcid      = ^[A-Za-z0-9_.-]+$
```

- Module and rater IDs permit underscore and forbid hyphen.
- `ezqcid` permits underscore, period and hyphen.
- Module/rater are at most 32 ASCII characters; `ezqcid` is at most 128.
- Blank, path-separator, case-colliding and non-portable values fail before
  filesystem mutation; `ezqcid` values `.` and `..` are rejected.
- Module/rater directory names must not equal Windows reserved device names.
- The internal rater sentinel `__observation_no_rater__` is reserved.
- Module names are case-insensitively unique within a project, `ezqcid` values
  within the master list, and raters within a module. Original case is
  preserved, but case-only alternatives cannot coexist.
- Validation checks final-component, unique atomic-temporary-component and
  complete-path budgets before writing.
- Human-facing localized names belong in labels, not internal IDs.

## Canonical path

```text
RatingFiles/<module_name>/<rater>/
  <module_name>-<rater>-<ezqcid>.json
```

Example:

```text
RatingFiles/AnatAll/lcj/
  AnatAll-lcj-CCNPPEK0001_01-anat.json
```

The parser removes exactly one final `.json` and uses `split("-", 2)`.
Additional hyphens therefore remain part of `ezqcid`.

Repeating module and rater in the filename is intentional safety redundancy.
If two module/rater directories are accidentally mixed, their files remain
distinct.

## Current-snapshot and identity lifecycle

- One project contains at most one current file for an exact
  `(module_name, rater, ezqcid)`.
- Changing scores, tags, notes, time or commands overwrites the same final path.
- Changing rater creates another rating identity.
- A module internal name with existing ratings is not ordinarily renameable;
  its display label remains editable.
- A removed module name remains reserved while its ratings exist.
- Existing module, rater and `ezqcid` IDs cannot be reassigned to a different
  logical module/person/record without explicit migration.
- Save-event history is out of scope.

## Save protocol

1. Validate all identity values.
2. Construct the exact target path without a glob.
3. If it exists, load it and compare JSON `name/rater/ezqcid` with the requested
   identity.
4. Refuse to overwrite any identity mismatch.
5. Serialize the complete legacy-compatible module snapshot with
   `schema_version: 2`.
6. Write a same-directory exclusive unique temporary file, flush, `fsync`, and
   publish with `os.replace`.

There is no score/tag token, random suffix, hash, encoding layer, prefix glob or
post-write old-file deletion in normal new-format saves.

EasyQC remains a single-writer project application. The save boundary
serializes same-process writes per identity. A second process attempting to
write the same project fails on a project write lock instead of racing through
“read existing identity → replace”. PID-only temporary names are insufficient.

## Authority, validation and recovery

The JSON body is authoritative for scores, tags, notes, time and its declared
identity. Directory and filename identity are redundant validation evidence.

A scan returns valid records and path-specific errors. It does not silently
skip malformed JSON or repair a mismatch. An explicit recovery operation may
recalculate the canonical path from validated JSON and present conflicts before
moving anything.

Every identity-producing entry point uses the same policy: module
create/import/template copy/project load, GUI/CLI rater input, QC-list
import/load, rating save and recovery. CSV ingestion reads `ezqcid` as text from
the start; it cannot let pandas infer `001` as numeric `1` and then stringify
the already-lost value.

## Compatibility

Legacy filenames remain readable:

```text
<module>._.<ezqcid>._.<rater>._.<score1>._.<tag1>.json
```

New saves write only the canonical form. Migration is scan/plan/apply:

- validate every source payload and identifier;
- report case collisions and duplicate logical identities;
- reject occupied destinations with non-identical payloads;
- never choose a winner using mtime, payload time or scan order;
- stage, read back and verify canonical copies;
- retain legacy sources until a separate backup/removal decision is approved.

Permanent dual-write is prohibited. Compatibility is one-way: the new program
reads legacy filenames, but old EasyQC glob logic does not discover new
canonical names. A legacy-compatible JSON payload alone does not make the new
path readable by the old program, so cutover retains a verified rollback backup.

## Core boundaries

- `Rating` continues to model a complete current snapshot.
- `rating_identity` owns pure validation, filename parsing/construction and
  portable path budgets.
- `RatingService` owns dual-format reads, path/body collision guards, canonical
  saves and aggregation input.
- `rating_write_lock` owns project-scoped same-process and OS writer
  coordination.
- `rating_migration` owns the read-only conflict plan and explicit construction
  of a verified, inactive sibling candidate. It never activates the candidate
  or deletes/moves the active source tree.
- `FileUtils` owns atomic publication.
- Qt/tkinter adapters display typed errors and never parse paths or JSON.
- Aggregation reads validated JSON values; filenames never supply scores/tags.

## Verification

Synthetic tests cover:

- accepted/rejected character sets, lengths, reserved names and case scope;
- hyphenated `ezqcid` round-trip and leading-zero preservation from CSV;
- two modules/two raters mixed into one directory without filename collision;
- exact-identity re-save to one path;
- body-identity conflict refusing overwrite;
- injected atomic failure retaining the complete old snapshot;
- same-process serialization, unique temporary names and second-writer lock
  rejection;
- valid siblings remaining visible beside path-scoped scan errors;
- legacy/new dual reads and conflict-first migration planning;
- equivalent wide-table results before and after synthetic migration.

Tests do not read, modify or delete real runtime rating data.

The redundant filename protects files mixed across module/rater directories
inside one project. It does not distinguish two projects with an identical
three-field identity, and it intentionally does not preserve multiple versions
of one exact identity.
