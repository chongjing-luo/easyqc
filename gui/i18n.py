"""GUI internationalization — global language switch (zh/en).

Usage in each GUI file:
    from gui.i18n import LANG, tr
    ttk.Button(..., text=tr(_T, "新建项目"))

Each file defines its own ``_T`` dict mapping the canonical key to
{ "zh": "...", "en": "..." }. ``tr`` picks the current language.

To switch language globally, change ``LANG`` here (or set via menu/env later).
"""

LANG = "en"  # "zh" = 中文, "en" = English


def tr(table: dict, key: str) -> str:
    """Look up ``key`` in ``table`` and return the current-language string.

    Falls back to the key itself if not found (so missing translations are
    visible but never crash).
    """
    entry = table.get(key)
    if entry is None:
        return key
    return entry.get(LANG, entry.get("zh", key))


__all__ = ["LANG", "tr"]
