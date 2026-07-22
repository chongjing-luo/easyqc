# EasyQC native UI checklist v1

This checklist defines the stable native-human verification items used by
`record_native_ui_checklist.py`. A real run is canonical JSON tied to one exact
source, release, runtime, platform, display and hardware request. This document
is guidance only and is never itself a PASS claim.

Every item must appear exactly once as `PASS`, `FAIL` or `NOT_RUN`. `FAIL` and
`NOT_RUN` require a non-empty note. `NOT_RUN` also requires one controlled
reason code. Screenshots may support an observation but cannot replace it.
`UI-REMOTE-01` is conditional: a request recorded as a non-remote display must
mark it `NOT_RUN` and may still pass the required local observations; a request
with `display.remote: true` must actually pass it.

| Item ID | Required observation |
|---|---|
| UI-LAYOUT-01 | Critical controls do not overlap or disappear at required viewports. |
| UI-TEXT-01 | Long and Chinese labels and paths remain accessible. |
| UI-PALETTE-01 | Standard light/dark contrast is usable and errors are not color-only. |
| UI-KEYBOARD-01 | Project → Table → Filter/Sort/Columns → selection → QC is keyboard reachable without a trap. |
| UI-TABLE-01 | Pinned identity and the data view remain simultaneously usable. |
| UI-DIALOG-01 | Apply, Cancel, error focus and the button box are reachable. |
| UI-QC-01 | Viewer, rating, watch state and critical actions remain visible. |
| UI-CONFIG-01 | Forms scroll/resize and save/cancel remain reachable. |
| UI-DPI-01 | The required scale and logical viewport combination is usable. |
| UI-REMOTE-01 | Remote-session behavior is recorded when remote use is claimed. |
| UI-A11Y-01 | Accessible names and a basic platform assistive-technology smoke are recorded. |

The operator records an accountable identifier, start/completion UTC timestamps,
and notes for every non-PASS item. Native execution that did not occur remains
`NOT_RUN`; an automated CI runner cannot close any item in this checklist.
