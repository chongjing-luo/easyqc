# tests/ — pointer

For this project the generic scaffold's `tests/` is realized by **`easyqc/tests/`**:

```text
tests/  →  easyqc/tests/
           ├── conftest.py
           ├── fixtures/                 desensitized data snapshots
           ├── test_characterization/    legacy-behavior pins (CCNPPEKI qctable rebuild, etc.)
           ├── test_core/                services + table_transform + code_executor
           ├── test_models/              dataclasses + legacy adapters
           ├── test_gui/                 GUI static + adapter layer
           ├── test_integration/         end-to-end paths
           ├── test_scripts/             CLI / setup
           ├── test_utils/               logger, file_utils, validators
           └── test_smoke.py
```

Run: `cd easyqc && .venv/bin/python -m pytest` (234 passing). See `docs/PROJECT_DEVELOPMENT_SYSTEM.md` §25.2.
