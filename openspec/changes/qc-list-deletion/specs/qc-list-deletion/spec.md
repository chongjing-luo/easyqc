# QC list deletion specification

## Import-draft contract

The system SHALL delete every selected import-preview row from its exact
underlying draft position and SHALL delete the current selected column on
request. It SHALL NOT write a project table or rating file until the user
separately applies a valid draft.

## Pre-QC contract

The system SHALL delete exact selected identities or exact selected ordinary
columns from the authoritative Pre-QC list through validated atomic
persistence. It SHALL reject deletion of `easyqcid`, missing targets and stale
targets before publishing success.

## Rating-retention contract

No list-deletion operation SHALL delete, rename or modify any rating record.
This applies to success, validation failure and persistence failure. Removing
an identity MAY make its rating unjoined in current result views; restoring the
same identity SHALL leave the retained record available for normal joining.

## UI contract

Deletion actions SHALL be discoverable, confirm the exact scope, state that
ratings are retained, support Chinese/English switching, and be disabled while
the source view is not safe to mutate.
