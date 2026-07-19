"""PySide6/Qt presentation adapter for EasyQC.

The package may depend on Core and Models. Core and Models must never import it.
"""

from gui_qt.application import (
    build_preview_window,
    get_or_create_qapplication,
    launch_qt_preview,
)

__all__ = [
    "build_preview_window",
    "get_or_create_qapplication",
    "launch_qt_preview",
]
