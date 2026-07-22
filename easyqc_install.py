#!/usr/bin/env python3
"""Stable command-line entry point for the EasyQC managed runtime."""

import contextlib
import io
import os


# Existing FileUtils imports initialize the application logger. Installer
# commands own their transaction logs, while `status` must remain read-only and
# stdout is a protocol. Disable that legacy file sink only during the fresh
# installer import and isolate its known console chatter; exceptions still
# propagate to stderr.
_log_override = "EASYQC_LOG_DIR"
_previous_log_override = os.environ.get(_log_override)
os.environ[_log_override] = os.devnull
try:
    with contextlib.redirect_stdout(io.StringIO()):
        from core.managed_runtime_cli import main
finally:
    if _previous_log_override is None:
        os.environ.pop(_log_override, None)
    else:
        os.environ[_log_override] = _previous_log_override


if __name__ == "__main__":
    raise SystemExit(main())
