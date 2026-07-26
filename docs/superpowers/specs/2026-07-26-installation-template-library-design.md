# Installation-scoped template library design

Date: 2026-07-26  
Status: pending written-spec review  
Task index: `../../../../dev/cross-project-settings/PROJECT_INDEX.md`

## Goal

Add reusable constant and QC-module templates without adding live
cross-project coupling. Templates belong to one EasyQC installation. Copying a
template creates normal, editable project-owned data; later template edits do
not affect the copy.

The same feature adds a prominent “跨项目设置” page, moves the viewer
`shell=False/True` choice into it, stores each module in its own JSON file, and
keeps the collapsed navigation button fixed at the top.

## User-approved rules

- The current EasyQC installation is the isolation boundary.
- `projects.json` remains in that installation directory.
- Constant and QC-module system content is a template only.
- There is no enable/reference/synchronization mode.
- Project users copy a template and may then edit every copied field.
- Templates never participate in View-command variables.
- Templates never participate in project import/name conflicts until copied.
- Conflicts are first-come-first-served: validate additions and renames before
  writing; existing destination data wins.
- Module names are unique within their own template catalog or project.
- Module labels may repeat.
- Every template module and project module has one JSON file.

## Storage

```text
easyqc/
├── projects.json
├── constant_templates.json
├── app_settings.json
└── modules/
    └── <module-uuid>.json

<project>/
├── settings_<project>.json
└── modules/
    └── <module-uuid>.json
```

`constant_templates.json`:

```json
{
  "schema_version": 1,
  "constants": {
    "DATA_ROOT": "/data/example"
  }
}
```

`app_settings.json`:

```json
{
  "schema_version": 1,
  "viewer_execution": {
    "shell": false
  }
}
```

Each module file contains:

```json
{
  "schema_version": 1,
  "module_id": "<canonical UUID>",
  "scope": "template",
  "display_order": 10,
  "module": "<complete QCModule legacy-compatible object>"
}
```

Project module files use `scope: project` and a new UUID. The nested `module`
value is produced and parsed through the existing `QCModule` model.

All writes use a same-directory temporary file and `os.replace`.

## Runtime boundary

```text
View variables = project constants + current list row
Runtime modules = project module files
```

Template files are read only by template-management and copy operations. No
runtime resolver, command renderer, filter, QC queue, or rating operation reads
templates.

Ratings continue to save the complete legacy module snapshot used at rating
time, so later project/template changes do not alter history.

## Copy behavior

### Constant

1. Select “常量设置 → 从模板添加”.
2. Load a detached name/value candidate.
3. Permit edits before confirmation.
4. Check the candidate name against existing project constants and the current
   master-list header.
5. On conflict, show the exact existing owner and do not write.
6. On success, add a normal editable project constant.

### QC module

1. Select “质控模块 → 从模板添加”.
2. Load a detached full-module candidate.
3. Permit edits before confirmation.
4. Check the candidate name against project module names.
5. On success, create a new project module UUID and file.
6. The project copy has no live pointer to the template.

Using an unchanged template name is valid if the destination project does not
already contain that name. A template and a project may therefore independently
have equal names.

## Legacy project migration

For a project without a `modules/` directory:

1. Read and validate every legacy `settings["qcmodule"]` entry.
2. Write all converted files into a same-filesystem staging directory.
3. Read back and compare every complete legacy payload.
4. Atomically rename the staged directory into place.
5. Treat module files as authoritative only after the complete migration.

The settings `qcmodule` field remains a generated compatibility snapshot during
the transition. One Core mutation boundary updates module files and the
snapshot, rolling back on failure. A failed migration leaves the original
settings unchanged and reports the failing path/module.

## GUI

- The navigation header has fixed height and top alignment.
- Collapsing hides optional text/navigation content, not the header or toggle.
- The previous bottom execution-settings button becomes “跨项目设置” and opens
  a real page in the workspace stack.
- Page tabs: “常量模板”, “质控模块模板”, “命令执行”.
- Project constant and module pages each add “从模板添加”.
- New presentation text supports immediate Chinese/English switching.
- Template/project user-defined names, values, labels, commands, scores, tags,
  and notes are never translated.

## Core boundaries

- `TemplateService`: installation-scoped template/settings CRUD.
- `ModuleRepository`: one-file module persistence, ordering, and migration.
- `NamespaceValidator`: destination-only first-come conflict checks.
- `ProjectTemplateService`: copy detached candidates through project mutation
  services.
- `EffectiveProjectResolver`: project-only runtime constants/modules.
- Qt pages call these Core services; they never read/write JSON directly.

Existing `QCModule`, `ConfigurationService`, `ProjectContextService`,
`QcWorkflowService`, `CodeExecutor`, EventBus, and atomic file utilities are
extended or reused rather than duplicated.

## Failure and performance rules

- A malformed module file identifies its exact path and cannot silently turn a
  catalog into an empty list.
- Valid sibling modules remain inspectable while the catalog exposes its error
  state.
- Project-list conflicts read CSV headers only, never the full table.
- No file watcher, database, cross-install discovery, or background sync.
- Multiple simultaneous writers are not supported; atomic replacement protects
  interruption, not semantic multi-writer merging.
- Tests use only temporary installations/projects and never real runtime data.

## Verification

- Two temporary installations retain independent templates and execution modes.
- Template copy is editable and detached.
- Template names do not affect list import or View variables.
- Project destination conflicts are blocked before writing.
- Legacy module migration preserves every legacy field.
- Ratings still contain complete module snapshots.
- Navigation toggle stays top-aligned when collapsed.
- New page and dialogs work in Chinese and English.
- Focused Core/GUI tests, layer/isolation tests, and the full suite pass.

## Implementation order

1. Storage records, repository, and atomic tests.
2. Template service and installation settings.
3. Destination namespace validation and project-copy operations.
4. Legacy module migration and compatibility transaction.
5. Project-only runtime resolution.
6. Cross-project settings page and execution-mode integration.
7. Project copy actions, navigation alignment, and bilingual strings.
8. Integration tests and documentation updates.

