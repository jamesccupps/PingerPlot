"""The Settings page's Recording section: whether the history is kept, what
goes into it and for how long, where it is and how big, and clearing it.

Recording is on by default and needs no setup; everything here is for
changing what it keeps. The "what to record" switches sit behind a
"Choose…" button, as advanced settings: the defaults record everything.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict

from . import history
from .gui_widgets import section, setting_row

_SIZE_REFRESH_MS = 10_000


class RecordingVars:
    """Tk variables for the ``recording`` section of settings.json."""

    def __init__(self, master, saved) -> None:
        self.vars: Dict[str, tk.Variable] = {}
        for key, value in history.normalize(saved).items():
            if isinstance(value, bool):
                self.vars[key] = tk.BooleanVar(master=master, value=value)
            else:
                self.vars[key] = tk.StringVar(master=master, value=str(value))

    def __getitem__(self, key: str) -> tk.Variable:
        return self.vars[key]

    def options(self) -> dict:
        """The options as the recorder takes them. A number field holding
        something else ("7.5", "", a typo) falls back to its default rather
        than raising: the field is validated by being normalised."""
        raw = {}
        for key, var in self.vars.items():
            value = var.get()
            if isinstance(value, str):
                try:
                    value = int(float(value.strip()))
                except (ValueError, OverflowError):
                    pass
            raw[key] = value
        return history.normalize(raw)


def open_folder(path) -> None:
    """Show ``path`` in the system's file manager, creating it if need be."""
    path = os.fspath(path)
    try:
        os.makedirs(path, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)                        # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
    except OSError as exc:
        messagebox.showerror("Can't open the folder", f"{path}\n\n{exc}")


def _fmt_size(n: int) -> str:
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.0f} MB"
    return f"{n / 1024 ** 3:.1f} GB"


def build_section(parent, look, app) -> None:
    """The Recording card. ``app`` provides ``recording`` (RecordingVars),
    ``recorder`` and ``clear_history()``."""
    S = look.S
    rv: RecordingVars = app.recording

    def switch(var):
        return lambda row: ttk.Checkbutton(row, variable=var, style="Card.Switch.TCheckbutton")

    def spin(var, lo, hi, step=1):
        return lambda row: ttk.Spinbox(row, from_=lo, to=hi, increment=step, width=7,
                                       textvariable=var, style="Card.TSpinbox")

    card = section(parent, look, "Recording")
    setting_row(card, look, "Record history",
                "Everything each target measures goes into a database on this computer: every "
                "probe and the router that answered it, per-minute summaries for each hop, alerts, "
                "reroutes and DNS moves. Nothing is sent anywhere.",
                switch(rv["enabled"]), first=True)
    setting_row(card, look, "Keep every probe for (days)",
                "The full detail. About 25 MB a day for a 15-hop target probed every 2.5 s.",
                spin(rv["probe_days"], 1, 365))
    setting_row(card, look, "Keep minute summaries for (days)",
                "Loss, latency and jitter for each hop and router, minute by minute: small "
                "enough to keep for years.",
                spin(rv["summary_days"], 1, 3650))
    setting_row(card, look, "Keep events and routes for (days)", "",
                spin(rv["event_days"], 1, 3650))
    setting_row(card, look, "Size limit (MB)",
                "Past it, the oldest probes go first, then summaries, then events.",
                spin(rv["max_mb"], 50, 100_000, 50))

    # Advanced: what to record, behind a button so the defaults (everything)
    # need no reading.
    advanced = ttk.Frame(card, style="Card.TFrame")
    shown = {"open": False}

    def toggle(button):
        shown["open"] = not shown["open"]
        if shown["open"]:
            advanced.pack(side="top", fill="x", after=choose_row)
            button.configure(text="Hide")
        else:
            advanced.pack_forget()
            button.configure(text="Choose…")

    def choose(row):
        b = ttk.Button(row, text="Choose…", style="Card.TButton")
        b.configure(command=lambda: toggle(b))
        return b
    choose_row = setting_row(card, look, "What to record",
                             "Everything, unless you choose otherwise.", choose)
    setting_row(advanced, look, "Every probe",
                "Off keeps only the minute summaries: far less space, the same picture at "
                "one-minute resolution.", switch(rv["probes"]))
    setting_row(advanced, look, "Minute summaries", "", switch(rv["summaries"]))
    setting_row(advanced, look, "Events", "Alerts, reroutes, DNS moves, warnings and notes.",
                switch(rv["events"]))
    setting_row(advanced, look, "Routes", "Which route each target was on, and when.",
                switch(rv["routes"]))

    folder = history.default_dir()

    def storage(row):
        box = ttk.Frame(row, style="Card.TFrame")
        ttk.Button(box, text="Open folder", style="Card.TButton",
                   command=lambda: open_folder(folder)).pack(side="left")
        ttk.Button(box, text="Clear history…", style="Card.TButton",
                   command=app.clear_history).pack(side="left", padx=(S(6), 0))
        return box
    where = setting_row(card, look, "Stored in", str(folder), storage)
    size = ttk.Label(where, text="", style="Card.Muted.TLabel")
    size.grid(row=2, column=0, sticky="w", pady=(S(2), 0))

    def refresh():
        try:
            if not size.winfo_exists():
                return
        except tk.TclError:
            return
        used = history.disk_bytes(folder)
        size.configure(text=f"Using {_fmt_size(used)}" if used else "Nothing recorded yet")
        size.after(_SIZE_REFRESH_MS, refresh)
    refresh()


def clear_history(app) -> None:
    """Ask, then delete everything recorded. Running targets carry on."""
    if not messagebox.askyesno(
            "Clear history",
            "Delete everything PingerPlot has recorded, for every target?\n\n"
            "This can't be undone. Targets that are running carry on recording.",
            icon="warning", default="no", parent=app.root):
        return
    rec = app.recorder
    if rec is not None:
        rec.clear()
        return
    # Recording is off, but the user still wants the old history gone.
    temp = history.Recorder(program="gui").start()
    temp.clear()
    threading.Thread(target=temp.close, name="history-clear", daemon=True).start()
