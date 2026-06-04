from __future__ import annotations

import platform
import tkinter as tk
from tkinter import ttk
from typing import Callable


class ScrolledTreeview(ttk.Frame):
    def __init__(self, parent, **tree_kwargs):
        super().__init__(parent)
        self.tree = ttk.Treeview(self, **tree_kwargs)
        self.v_scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.tree.yview)
        self.h_scrollbar = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=self.v_scrollbar.set, xscrollcommand=self.h_scrollbar.set)

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.v_scrollbar.grid(row=0, column=1, sticky="ns")
        self.h_scrollbar.grid(row=1, column=0, sticky="ew")


class LabelledEntry(ttk.Frame):
    def __init__(self, parent, label: str, textvariable=None, width: int = 30):
        super().__init__(parent)
        self.variable = textvariable or tk.StringVar()
        self.label = ttk.Label(self, text=label)
        self.entry = ttk.Entry(self, textvariable=self.variable, width=width)
        self.label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.entry.grid(row=0, column=1, sticky="ew")
        self.grid_columnconfigure(1, weight=1)

    def get(self) -> str:
        return self.variable.get()

    def set(self, value: str) -> None:
        self.variable.set(value)


class DialogBase:
    def __init__(self, parent, title: str, geometry: str | None = None):
        self.parent = parent
        self.window = tk.Toplevel(parent)
        self.window.title(title)
        if geometry:
            self.window.geometry(geometry)
        self.window.transient(parent)
        self.window.grab_set()

    def close(self) -> None:
        self.window.destroy()


class CollapsibleCard(ttk.Frame):
    def __init__(self, parent, title: str, expanded: bool = True, on_toggle: Callable[[bool], None] | None = None):
        super().__init__(parent)
        self.expanded = tk.BooleanVar(value=expanded)
        self.on_toggle = on_toggle

        self.header = ttk.Frame(self)
        self.header.pack(fill=tk.X)
        self.toggle_button = ttk.Button(self.header, text="-" if expanded else "+", width=3, command=self.toggle)
        self.toggle_button.pack(side=tk.LEFT)
        self.title_label = ttk.Label(self.header, text=title)
        self.title_label.pack(side=tk.LEFT, padx=4)
        self.content = ttk.Frame(self)
        if expanded:
            self.content.pack(fill=tk.BOTH, expand=True)

    def toggle(self) -> None:
        new_value = not self.expanded.get()
        self.expanded.set(new_value)
        self.toggle_button.configure(text="-" if new_value else "+")
        if new_value:
            self.content.pack(fill=tk.BOTH, expand=True)
        else:
            self.content.pack_forget()
        if self.on_toggle:
            self.on_toggle(new_value)


def bind_context_menu(widget, callback, system_name: str | None = None) -> list[str]:
    system = system_name or platform.system()
    events = ["<Button-2>", "<Control-Button-1>"] if system == "Darwin" else ["<Button-3>"]
    for event in events:
        widget.bind(event, callback)
    return events


__all__ = ["ScrolledTreeview", "LabelledEntry", "DialogBase", "CollapsibleCard", "bind_context_menu"]
