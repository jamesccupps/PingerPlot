"""In-app updates, the window's side: the Settings section, the bar across the
top when a newer version is out, and the tray's Install item. updater.py does
the checking, downloading and installing; this decides when, and shows it.

Checks run on a worker thread and hand their result back through app._post,
so nothing here touches Tk off the main thread.
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from typing import Optional

from . import __version__, updater
from .gui_widgets import section, setting_row

log = logging.getLogger("pingerplot.update")

FIRST_CHECK_MS = 4000                   # after start-up, not during it
RECHECK_MS = 24 * 3600 * 1000           # then once a day while it runs

KINDS = {"installed": "Installed for this Windows user.",
         "portable": "A portable copy: updates replace the program files in this folder "
                     "and keep its settings.",
         "folder": "A copy in a folder of its own: updates replace its program files.",
         "pip": "Installed with pip or pipx: update with pipx reinstall pingerplot.",
         "source": "Running from source: update with git pull."}

# How a copy that can't install updates itself gets one.
UPDATE_BY = {"pip": "pipx reinstall pingerplot", "source": "git pull"}


class Updates:
    def __init__(self, app) -> None:
        self.app = app
        self.available: Optional[updater.Update] = None
        self.status = "Not checked yet."
        self.busy = False
        self.dismissed = False
        self.auto_var = tk.BooleanVar(master=app.root, value=True)
        self._row = None                # the Settings page's widgets, once built
        self._bar: Optional[ttk.Frame] = None
        self._bar_holder: Optional[ttk.Frame] = None
        self._next = None               # the pending automatic check's after() id

    # --- settings ----------------------------------------------------------------
    def load(self, saved: dict) -> None:
        if isinstance(saved.get("check_updates"), bool):
            self.auto_var.set(saved["check_updates"])

    def save(self, data: dict) -> None:
        data["check_updates"] = bool(self.auto_var.get())

    def start(self) -> None:
        """Schedule the first automatic check. Never from source: a developer's
        checkout has git for that."""
        if self.auto_var.get() and updater.install_kind() != "source":
            self._schedule(FIRST_CHECK_MS)

    def _schedule(self, ms: int) -> None:
        """One pending automatic check at most, however often the switch is
        flicked."""
        if self._next is not None:
            self.app.root.after_cancel(self._next)
        self._next = self.app.root.after(ms, self._auto)

    def _auto(self) -> None:
        self._next = None
        if self.auto_var.get():
            self.check(quiet=True)

    # --- checking and installing ---------------------------------------------------
    def check(self, quiet: bool = False) -> None:
        """Ask for latest.json on a worker thread. ``quiet`` is the automatic
        check: it reschedules itself and only speaks up when there's news."""
        if quiet:
            self._schedule(RECHECK_MS)
        if self.busy:
            return
        self.busy = True
        self.status = "Checking…"
        self.refresh()

        def run():
            try:
                u = updater.check()
                status = (f"Version {u.version} is available." if u else
                          f"You're up to date ({__version__}).")
            except Exception as e:      # the network, GitHub, a bad manifest: all just "couldn't"
                log.warning("update check failed: %s", e)
                u, status = None, f"Couldn't check: {e}"
            self.app._post(lambda: self._checked(u, status))
        threading.Thread(target=run, daemon=True, name="pingerplot-update-check").start()

    def _checked(self, u: Optional[updater.Update], status: str) -> None:
        self.busy = False
        self.status = status
        if u and (self.available is None or self.available.version != u.version):
            self.dismissed = False
            tray = self.app.tray
            if tray is not None and not self.app.root.winfo_viewable():
                tray.notify(f"PingerPlot {u.version} is available",
                            "Click to open PingerPlot and install it.",
                            on_click=lambda: self.app._post(self.app._show_window))
        self.available = u
        self.refresh()

    def install(self) -> None:
        u = self.available
        if not u or not u.can_install or self.busy:
            return
        self.busy = True
        self.status = f"Downloading {u.version}…"
        self.refresh()

        def progress(done, total):
            pct = int(done * 100 / total) if total else 0
            self.status = f"Downloading {u.version}… {pct}%"
            self.app._post(self.refresh)

        def run():
            try:
                path = updater.download(u, progress=progress)
                self.status = f"Installing {u.version}…"
                self.app._post(self.refresh)
                updater.apply(u, path)
            except Exception as e:
                log.exception("update failed")
                msg = str(e)        # `e` is unbound once this block ends
                self.app._post(lambda: self._failed(msg))
                return
            # The installer (or the staged copy) waits for this process to
            # exit, then starts the new version with --updated, which
            # resumes the targets saved here.
            self.app._post(self.app._quit)
        threading.Thread(target=run, daemon=True, name="pingerplot-update").start()

    def _failed(self, msg: str) -> None:
        self.busy = False
        self.status = f"The update didn't install: {msg}"
        self.refresh()
        messagebox.showerror("PingerPlot", f"The update didn't install:\n\n{msg}",
                             parent=self.app.root)

    def whats_new(self) -> None:
        if self.available:
            webbrowser.open(f"{updater.RELEASES_URL}/tag/v{self.available.version}")

    def tray_items(self, M) -> list:
        """The tray menu's Install item, when there's something to install.
        Runs on the tray thread: reads state only."""
        u = self.available
        if u and u.can_install and not self.busy:
            return [M(), M(f"Install version {u.version}", lambda: self.app._post(self.install))]
        return []

    # --- what it looks like ------------------------------------------------------------
    def build_bar_holder(self, parent) -> None:
        """An empty frame across the window, under the navigation; the bar
        appears in it when a version is available."""
        self._bar_holder = ttk.Frame(parent)
        self._bar_holder.pack(side="top", fill="x")

    def build_section(self, body) -> None:
        """The Settings page's Updates card."""
        look = self.app.look
        S = look.S
        card = section(body, look, "Updates")

        def buttons(row):
            box = ttk.Frame(row, style="Card.TFrame")
            self._check_btn = ttk.Button(box, text="Check now", style="Card.TButton",
                                         command=self.check)
            self._check_btn.pack(side="right")
            self._install_btn = ttk.Button(box, text="Install and restart", style="Card.Accent.TButton",
                                           command=self.install)
            return box
        row = setting_row(card, look, f"This is version {__version__}", "", buttons, first=True)
        kind = ttk.Label(row, text=KINDS[updater.install_kind()], style="Card.Muted.TLabel",
                         wraplength=S(460), justify="left")
        kind.grid(row=1, column=0, sticky="w", pady=(S(2), 0))
        self._status_lbl = ttk.Label(row, text="", style="Card.TLabel", wraplength=S(460),
                                     justify="left")
        self._status_lbl.grid(row=2, column=0, sticky="w", pady=(S(6), 0))
        self._row = row

        def switch(parent):
            return ttk.Checkbutton(parent, variable=self.auto_var, style="Card.Switch.TCheckbutton",
                                   command=self._auto_changed)
        setting_row(card, look, "Check for updates automatically",
                    "Once a day, one small request to GitHub. Nothing is downloaded until you "
                    "click Install.", switch)
        self.refresh()

    def _auto_changed(self) -> None:
        self.app._save_settings()
        if not self.auto_var.get():
            if self._next is not None:
                self.app.root.after_cancel(self._next)
                self._next = None
        elif updater.install_kind() != "source":
            self._schedule(FIRST_CHECK_MS)

    def refresh(self) -> None:
        self._refresh_bar()
        if self._row is None:
            return
        try:
            if not self._row.winfo_exists():
                return
        except tk.TclError:
            return
        u = self.available
        self._status_lbl.configure(text=self.status)
        self._check_btn.configure(state="disabled" if self.busy else "normal")
        if u and u.can_install:
            self._install_btn.configure(text=f"Install {u.version} and restart",
                                        state="disabled" if self.busy else "normal")
            self._install_btn.pack(side="right", padx=(0, self.app.look.S(8)))
        else:
            self._install_btn.pack_forget()

    def _refresh_bar(self) -> None:
        holder = self._bar_holder
        if holder is None:
            return
        for w in holder.winfo_children():
            w.destroy()
        u = self.available
        if not u or self.dismissed:
            holder.configure(padding=0)
            return
        S = self.app.look.S
        holder.configure(padding=(S(20), S(10), S(20), 0))
        bar = ttk.Frame(holder, style="Tile.TFrame", padding=(S(14), S(8)))
        bar.pack(fill="x")
        text = (self.status if self.busy else
                f"PingerPlot {u.version} is available." if u.can_install else
                f"PingerPlot {u.version} is out: update with "
                f"{UPDATE_BY.get(u.kind, 'git pull')}.")
        ttk.Label(bar, text=text, style="Card.Strong.TLabel").pack(side="left")
        ttk.Button(bar, text=self.app.look.icon("close"), style="Card.Icon.Subtle.TButton",
                   command=self.dismiss, takefocus=False).pack(side="right")
        ttk.Button(bar, text="What's new", style="Card.Subtle.TButton", command=self.whats_new,
                   takefocus=False).pack(side="right", padx=(S(8), S(4)))
        if u.can_install:
            ttk.Button(bar, text="Install and restart", style="Card.Accent.TButton",
                       command=self.install, takefocus=False,
                       state="disabled" if self.busy else "normal").pack(side="right")

    def dismiss(self) -> None:
        self.dismissed = True
        self._refresh_bar()
