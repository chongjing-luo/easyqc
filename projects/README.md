# tests/ — pointer

For this project the generic scaffold's `tests/` is realized by **`easyqc/tests/`**:

```text
tests/  →  easyqc/tests/
           ├── conftest.py
           ├── fixtures/                 desensitized data snapshots
           ├── test_core/                services + table_transform + code_executor
           ├── test_models/              dataclasses + legacy adapters
           ├── test_gui_qt/              sole Qt GUI and workflow tests
           ├── test_integration/         end-to-end paths
           ├── test_scripts/             CLI / setup
           ├── test_utils/               logger, file_utils, validators
           └── test_smoke.py
```

Run: `cd easyqc && .venv/bin/python scripts/run_test_matrix.py`. See `docs/PROJECT_DEVELOPMENT_SYSTEM.md` §25.2.
