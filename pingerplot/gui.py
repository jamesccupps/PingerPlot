"""The PingerPlot window: a header with the overall state, the targets on the
left as cards, the selected target on the right (its hops and their graph, a
timeline of every hop, a map, its events), and a Settings page.

Pure standard library. The window never touches monitor internals: it polls
the snapshot accessors on a timer and renders immutable copies, so there are no
cross-thread Tk calls. Canvases are redrawn only while visible and only when
their data changed. Drawing lives in gui_charts, small widgets in gui_widgets,
the option forms in gui_settings.
"""
from __future__ import annotations

import csv
import json
import os
import queue
import sys
import time
import tkinter as tk
import urllib.parse
import webbrowser
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from . import __version__, appicon, compare as cmpmod, geoip, icmp, settings
from . import theme as themes, tkthreads, tray as traymod, winplatform
from .gui_charts import BAD_MS, WARN_MS, Ink, draw_graph, draw_map, draw_timeline, fmt_ms, row_tag
from .gui_settings import REPO_URL, EditTargetDialog, OptionVars, SettingsPage
from .gui_widgets import Nav, ScrollFrame, TargetCard
from .model import HopView, csv_safe, draw_version, mos, mos_label
from . import monitor
from .monitor import Monitor

__all__ = ["App", "main", "BAD_MS", "WARN_MS", "COLORS"]

REFRESH_MS = 700

# The drawing code's colours, under the keys it has always read. They come
# from the theme engine (theme.legacy_colours) and are replaced in place on
# every theme change, so draw code that reads COLORS at call time follows.
COLORS: Dict[str, str] = {}

# Only Hostname stretches (and shrinks): the numbers keep their width, so all
# ten columns fit the detail pane at the window's minimum size.
COLUMNS = [
    ("ttl", "Hop", 44, "center"),
    ("address", "Address", 118, "w"),
    ("hostname", "Hostname", 170, "w"),
    ("loss", "Loss", 52, "e"),
    ("sent", "Sent", 50, "e"),
    ("cur", "Last", 50, "e"),
    ("avg", "Avg", 50, "e"),
    ("best", "Best", 50, "e"),
    ("worst", "Worst", 56, "e"),
    ("jitter", "Jitter", 56, "e"),
]

_ms = fmt_ms


def _csv_num(v: Optional[float]) -> str:
    return "" if v is None else f"{v:.1f}"


class App:
    def __init__(self, root: tk.Tk, use_tray: bool = False, start_minimized: bool = False) -> None:
        tkthreads.install()     # before the first Variable or image: see tkthreads
        self.root = root
        self.tray: Optional[traymod.TrayIcon] = None
        self._tray_state = None
        self._ui_jobs: "queue.SimpleQueue" = queue.SimpleQueue()
        self._notify_seq: Dict[str, int] = {}   # per target: newest event already notified
        self._monitors: "dict[str, Monitor]" = {}   # target name -> its Monitor
        self._active: Optional[str] = None           # which one the detail shows
        self._empty = Monitor()                      # placeholder when nothing selected
        self._loaded: "set[str]" = set()             # offline sessions, not live targets
        self.geo = geoip.GeoResolver()               # for the Map view (lazy/opt-in)
        self._geo_started = False
        self._selected_ttl: Optional[int] = None    # hop currently graphed
        self._last_ttl: Optional[int] = None         # destination hop, per refresh
        self._pinned = False                         # True once the user picks a row
        self._last_event_seq = -1
        self._draw_ver = None                        # what the visible canvas last showed
        self._note_until = 0.0
        self._cards: Dict[str, TargetCard] = {}
        self._settings = settings.load()             # persisted UI/engine/alert state

        self.scale = self._init_scaling()
        root.title("PingerPlot")
        self._set_window_icon()
        root.geometry(f"{self.s(1180)}x{self.s(780)}")
        root.minsize(self.s(900), self.s(560))
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        # "system" (follow Windows), "dark" or "light". Before 1.4 the choice
        # was dark or light and dark was saved by default, so existing users
        # keep the look they had.
        pref = self._settings.get("theme", "system")
        if pref not in themes.THEMES:
            pref = "system"
        self.theme_var = tk.StringVar(value=pref)
        self.resume_var = tk.BooleanVar(value=True)   # resume last targets on launch
        self.keep_tray_var = tk.BooleanVar(value=True)  # closing hides to the tray
        self.notify_var = tk.BooleanVar(value=True)     # a notification per alert
        self.autostart_var = tk.BooleanVar(value=winplatform.autostart_enabled())
        self.target_var = tk.StringVar(value="")
        self._theme_checked = time.monotonic()
        # Before any widget is built: every ttk style comes from here.
        self.look = themes.Theme(root, {"theme": pref})
        self.style = self.look.style
        self._sync_colours()

        # Defaults for new targets (the Settings page edits them).
        self.defaults = OptionVars(root)
        self.interval_var = self.defaults["interval"]
        self.logpath_var = self.defaults["log_path"]
        self.alert_loss_var = self.defaults["alert_loss_pct"]

        self._build_header()
        self._build_pages()
        self._recolor_widgets()
        self._apply_saved_settings()     # restore last defaults and app options

        if not icmp.is_available():
            # Say what to do about it. On Linux this is one sysctl away, and
            # "unsupported platform" would send the user looking for a port
            # that already exists.
            messagebox.showerror(
                "ICMP backend unavailable",
                icmp.unavailable_reason() + "\n\n"
                "TCP and UDP probe modes do not use this backend and may still work.",
            )

        if use_tray:
            self._start_tray()
            winplatform.refresh_autostart()     # the folder moved: point the Run key here
        if start_minimized and self.tray is not None:
            self.root.withdraw()                # Start with Windows: straight to the tray
        self._restore_targets()          # optionally resume the last session's targets
        self._refresh_once()
        self.root.after(REFRESH_MS, self._refresh)

    @property
    def monitor(self) -> Monitor:
        """The monitor the detail views render: the selected target, or an
        empty one when nothing is selected, so the rendering code needs no
        special case."""
        m = self._monitors.get(self._active) if self._active else None
        return m if m is not None else self._empty

    # --- DPI / HiDPI scaling ----------------------------------------------------
    def _init_scaling(self) -> float:
        """Size Tk's fonts to the real display DPI and return the pixel scale
        (1.0 at 96 dpi). Needs the process to be DPI aware already (see
        winplatform.enable_dpi_awareness), or Windows reports a flat 96 dpi and
        bitmap-stretches the window."""
        try:
            dpi = float(self.root.winfo_fpixels("1i"))
        except tk.TclError:
            dpi = 96.0
        if dpi <= 0:
            dpi = 96.0
        try:
            # Tk 'scaling' is points->pixels; 1 pt is 1/72 in.
            self.root.tk.call("tk", "scaling", dpi / 72.0)
        except tk.TclError:
            pass
        return dpi / 96.0

    def s(self, px: float) -> int:
        """Scale a base-96-dpi pixel measurement to the current display."""
        return int(round(px * self.scale))

    def _set_window_icon(self) -> None:
        """The 'signal ripples' mark in the title bar, taskbar and Alt-Tab, kept
        on self so Tk doesn't garbage-collect it."""
        try:
            self._app_icon = tk.PhotoImage(data=appicon.png_base64(64))
            self.root.iconphoto(True, self._app_icon)
        except tk.TclError:
            pass

    # --- theming ----------------------------------------------------------------
    def _apply_theme(self, name: str) -> None:
        """``name`` is the setting (system, dark or light): redraw every
        element in the palette it resolves to, then the canvases."""
        self.theme_var.set(name)
        self.look.apply({"theme": name})
        self._sync_colours()
        self._recolor_widgets()
        self._draw_ver = None
        self._refresh_once()

    def _sync_colours(self) -> None:
        """Point the drawing code's COLORS at the current palette, and give
        every open window a title bar to match."""
        COLORS.clear()
        COLORS.update(themes.legacy_colours(self.look.c))
        self.theme = "dark" if self.look.dark else "light"
        wins = [self.root] + [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]
        for win in wins:
            if win.winfo_exists():
                self.look.style_window(win)

    def _follow_system_theme(self) -> None:
        """With "Match Windows", notice Windows switching light/dark. A
        registry read every few seconds is cheaper than it sounds and needs
        no hidden window to receive the broadcast."""
        if self.theme_var.get() != "system":
            return
        now = time.monotonic()
        if now - self._theme_checked < 3.0:
            return
        self._theme_checked = now
        if themes.resolve_dark("system") != self.look.dark:
            self._apply_theme("system")

    def _recolor_widgets(self) -> None:
        """What doesn't follow ttk styles: canvases, row tags, scroll areas."""
        p = COLORS
        self.root.configure(bg=self.look.c["bg"])
        for c in (self.canvas, self.timeline_canvas, self.map_canvas):
            c.configure(bg=p["graph_bg"])
        for t in ("ok", "warn", "bad", "dest"):
            self.tree.tag_configure(t, background=p[t], foreground=p["fg"])
        for kind in ("alert", "clear", "route", "warn"):
            self.events_tree.tag_configure(kind, background=p[f"ev_{kind}"], foreground=p["fg"])
        for sf in (self.cards_scroll, getattr(self, "settings_page", None)):
            if sf is not None:
                sf.recolor()
        self.header_icon.configure(image=self._header_icon_image())

    def _ink(self) -> Ink:
        return Ink(COLORS, self.look.font, self.s)

    # --- layout -----------------------------------------------------------------
    def _header_icon_image(self) -> tk.PhotoImage:
        self._hdr_img = tk.PhotoImage(data=appicon.png_base64(self.s(30)))
        return self._hdr_img

    def _build_header(self) -> None:
        S = self.s
        head = ttk.Frame(self.root, padding=(S(24), S(16), S(20), S(6)))
        head.pack(side="top", fill="x")
        self.header_icon = ttk.Label(head, image=self._header_icon_image())
        self.header_icon.pack(side="left")
        ttk.Label(head, text="PingerPlot", style="Title.TLabel").pack(side="left", padx=(S(12), 0))
        more = ttk.Button(head, text=self.look.icon("more"), style="Icon.Subtle.TButton",
                          command=lambda: self._header_menu(more))
        more.pack(side="right")
        self.note_label = ttk.Label(head, text="", style="Faint.TLabel")
        self.note_label.pack(side="right", padx=(0, S(14)))
        self.status_text = ttk.Label(head, text="", style="TLabel")
        self.status_text.pack(side="right", padx=(0, S(10)))
        self.status_dot = ttk.Label(head, text="●", style="Dot.TLabel")
        self.status_dot.pack(side="right", padx=(0, S(6)))

        nav_row = ttk.Frame(self.root, padding=(S(24), 0, S(24), 0))
        nav_row.pack(side="top", fill="x")
        self.nav = Nav(nav_row, self.look, [("targets", "Targets"), ("settings", "Settings")],
                       self._show_page)
        self.nav.pack(side="left")
        self.nav.select("targets")
        ttk.Frame(self.root, style="Divider.TFrame", height=1).pack(side="top", fill="x")

    def _build_pages(self) -> None:
        self.pages = ttk.Frame(self.root)
        self.pages.pack(side="top", fill="both", expand=True)
        self.targets_page = ttk.Frame(self.pages)
        self.targets_page.pack(fill="both", expand=True)
        self.settings_page: Optional[SettingsPage] = None
        S = self.s
        split = ttk.Panedwindow(self.targets_page, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ttk.Frame(split, padding=(S(20), S(14), S(8), S(14)))
        right = ttk.Frame(split, padding=(S(8), S(10), S(20), S(14)))
        split.add(left, weight=1)
        split.add(right, weight=4)
        self._build_target_list(left)
        self._build_detail(right)
        self.root.bind_all("<Control-n>", lambda _e: self._focus_add())

    def _show_page(self, key: str) -> None:
        if key == "settings":
            if self.settings_page is None:     # built on first use: it's long
                self.settings_page = SettingsPage(self.pages, self)
            self.targets_page.pack_forget()
            self.settings_page.pack(fill="both", expand=True)
        else:
            if self.settings_page is not None:
                self.settings_page.pack_forget()
            self.targets_page.pack(fill="both", expand=True)
            self._draw_ver = None

    def _build_target_list(self, parent: ttk.Frame) -> None:
        S = self.s
        add = ttk.Frame(parent)
        add.pack(side="top", fill="x")
        self.add_entry = ttk.Entry(add, textvariable=self.target_var)
        self.add_entry.pack(side="left", fill="x", expand=True)
        self.add_entry.bind("<Return>", lambda _e: self._start())
        ttk.Button(add, text="Add", style="Accent.TButton", command=self._start).pack(
            side="left", padx=(S(8), 0))
        ttk.Label(parent, text="A host name or IP address. Ctrl+N comes back here.",
                  style="Faint.TLabel").pack(side="top", anchor="w", pady=(S(4), 0))

        caption = ttk.Frame(parent)
        caption.pack(side="top", fill="x", pady=(S(16), S(6)))
        self.targets_caption = ttk.Label(caption, text="TARGETS", style="Caps.TLabel")
        self.targets_caption.pack(side="left")
        ttk.Button(caption, text="Stop all", style="Subtle.TButton", command=self._stop_all).pack(
            side="right")

        self.cards_scroll = ScrollFrame(parent, self.look)
        self.cards_scroll.pack(side="top", fill="both", expand=True)
        self.cards_empty = ttk.Label(
            self.cards_scroll.inner, style="Muted.TLabel", wraplength=S(230), justify="left",
            text="Nothing monitored yet. Type a host name or IP address above and press Add: "
                 "8.8.8.8, your gateway, a server you care about.")
        self.cards_empty.pack(side="top", anchor="w", pady=(S(4), 0))

    def _build_detail(self, parent: ttk.Frame) -> None:
        S = self.s
        top = ttk.Frame(parent)
        top.pack(side="top", fill="x")
        top.columnconfigure(0, weight=1)
        self.detail_title = ttk.Label(top, text="", style="Heading.TLabel")
        self.detail_title.grid(row=0, column=0, sticky="w")
        self.detail_status = ttk.Label(top, text="", style="Muted.TLabel")
        self.detail_status.grid(row=1, column=0, sticky="w")
        buttons = ttk.Frame(top)
        buttons.grid(row=0, column=1, rowspan=2, sticky="e")
        self.pause_btn = ttk.Button(buttons, text="Pause", command=self._toggle_pause)
        self.pause_btn.pack(side="left")
        self.stop_btn = ttk.Button(buttons, text="Stop", command=self._toggle_run)
        self.stop_btn.pack(side="left", padx=(S(6), 0))
        self.edit_btn = ttk.Button(buttons, text="Edit settings", command=self._edit_active)
        self.edit_btn.pack(side="left", padx=(S(6), 0))

        stats = ttk.Frame(parent)
        stats.pack(side="top", fill="x", pady=(S(6), 0))
        self.mos_label_w = ttk.Label(stats, text="", style="Strong.TLabel")
        self.mos_label_w.pack(side="left")
        self.detail_stats = ttk.Label(stats, text="", style="Muted.TLabel")
        self.detail_stats.pack(side="left", padx=(S(10), 0))

        self.banner = ttk.Frame(parent, style="Banner.TFrame", padding=(S(14), S(8)))
        self.banner_label = ttk.Label(self.banner, text="", style="Banner.TLabel",
                                      wraplength=S(760), justify="left")
        self.banner_label.pack(side="left", fill="x", expand=True)
        self._banner_shown = False

        nav_row = ttk.Frame(parent)
        nav_row.pack(side="top", fill="x", pady=(S(8), 0))
        self._banner_anchor = nav_row
        self.views: Dict[str, ttk.Frame] = {}
        self.detail_nav = Nav(nav_row, self.look, [("hops", "Hops"), ("timeline", "Timeline"),
                                                   ("map", "Map"), ("events", "Events")],
                              self._show_view)
        self.detail_nav.pack(side="left")
        self.view_host = ttk.Frame(parent)
        self.view_host.pack(side="top", fill="both", expand=True, pady=(S(8), 0))

        self.views["hops"] = self._build_hops_view(self.view_host)
        self.views["timeline"] = self._build_timeline_view(self.view_host)
        self.views["map"] = self._build_map_view(self.view_host)
        self.views["events"] = self._build_events_view(self.view_host)
        self.detail_nav.select("hops")
        self._show_view("hops")

    def _card(self, parent) -> ttk.Frame:
        """A rounded surface for a table or a canvas to sit on."""
        S = self.s
        card = ttk.Frame(parent, style="Tile.TFrame", padding=(S(6), S(6)))
        return card

    def _build_hops_view(self, host) -> ttk.Frame:
        S = self.s
        view = ttk.Frame(host)
        split = ttk.Panedwindow(view, orient="vertical")
        split.pack(fill="both", expand=True)
        table_card = self._card(split)
        graph_card = self._card(split)
        split.add(table_card, weight=3)
        split.add(graph_card, weight=2)

        cols = [c[0] for c in COLUMNS]
        self.tree = ttk.Treeview(table_card, columns=cols, show="headings", selectmode="browse")
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title, anchor=anchor if anchor != "center" else "center")
            self.tree.column(key, width=S(width), anchor=anchor, stretch=(key == "hostname"),
                             minwidth=S(80) if key == "hostname" else S(width))
        self.tree.bind("<ButtonRelease-1>", self._on_user_select)
        self.tree.bind("<KeyRelease-Up>", self._on_user_select)
        self.tree.bind("<KeyRelease-Down>", self._on_user_select)
        self.tree.bind("<Button-3>", self._on_hop_menu)
        vsb = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview,
                            style="Card.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(graph_card, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._draw_graph())
        return view

    def _build_timeline_view(self, host) -> ttk.Frame:
        view = ttk.Frame(host)
        self.timeline_header = ttk.Label(view, text="No data yet.", style="Muted.TLabel")
        self.timeline_header.pack(side="top", anchor="w", pady=(0, self.s(6)))
        card = self._card(view)
        card.pack(side="top", fill="both", expand=True)
        self.timeline_canvas = tk.Canvas(card, highlightthickness=0, bd=0)
        tl_vsb = ttk.Scrollbar(card, orient="vertical", command=self.timeline_canvas.yview,
                               style="Card.Vertical.TScrollbar")
        self.timeline_canvas.configure(yscrollcommand=tl_vsb.set)
        tl_vsb.pack(side="right", fill="y")
        self.timeline_canvas.pack(side="left", fill="both", expand=True)
        self.timeline_canvas.bind("<Configure>", lambda _e: self._draw_timeline())
        self.timeline_canvas.bind(
            "<MouseWheel>", lambda e: self.timeline_canvas.yview_scroll(int(-e.delta / 120), "units"))
        return view

    def _build_map_view(self, host) -> ttk.Frame:
        view = ttk.Frame(host)
        self.map_header = ttk.Label(
            view, style="Muted.TLabel",
            text="Opening this view looks up public hop addresses with ipwho.is. "
                 "Private and LAN addresses are never sent.")
        self.map_header.pack(side="top", anchor="w", pady=(0, self.s(6)))
        card = self._card(view)
        card.pack(side="top", fill="both", expand=True)
        self.map_canvas = tk.Canvas(card, highlightthickness=0, bd=0)
        self.map_canvas.pack(fill="both", expand=True)
        self.map_canvas.bind("<Configure>", lambda _e: self._draw_map())
        return view

    def _build_events_view(self, host) -> ttk.Frame:
        S = self.s
        view = ttk.Frame(host)
        card = self._card(view)
        card.pack(fill="both", expand=True)
        cols = ("time", "type", "detail")
        self.events_tree = ttk.Treeview(card, columns=cols, show="headings")
        for key, title, width, stretch in (("time", "Time", 90, False), ("type", "Type", 80, False),
                                           ("detail", "Detail", 600, True)):
            self.events_tree.heading(key, text=title, anchor="w")
            self.events_tree.column(key, width=S(width), stretch=stretch, anchor="w")
        ev_vsb = ttk.Scrollbar(card, orient="vertical", command=self.events_tree.yview,
                               style="Card.Vertical.TScrollbar")
        self.events_tree.configure(yscrollcommand=ev_vsb.set)
        ev_vsb.pack(side="right", fill="y")
        self.events_tree.pack(side="left", fill="both", expand=True)
        return view

    def _show_view(self, key: str) -> None:
        for k, v in self.views.items():
            if k == key:
                v.pack(fill="both", expand=True)
            else:
                v.pack_forget()
        self._draw_ver = None
        self._draw_active_view()

    def build_app_settings(self, card) -> None:
        """The Settings page's rows for running in the background."""
        from .gui_widgets import setting_row

        def switch(var, command=None, enabled=True):
            def make(parent):
                cb = ttk.Checkbutton(parent, variable=var, style="Card.Switch.TCheckbutton",
                                     command=command)
                if not enabled:
                    cb.state(["disabled"])
                return cb
            return make
        has_tray = self.tray is not None
        if winplatform.IS_WINDOWS:
            setting_row(card, self.look, "Start with Windows",
                        "Starts in the notification area when you sign in, and resumes your targets. "
                        "It runs without administrator rights, so ICMP and TCP to the destination "
                        "work but full TCP/UDP traceroute doesn't (launch.ps1 -InstallAutostart "
                        "starts it elevated instead).",
                        switch(self.autostart_var, self._toggle_autostart))
        setting_row(card, self.look, "Keep running in the notification area when closed",
                    "Closing the window keeps every target monitored. Quit from the tray icon's "
                    "menu or the ⋯ menu." if has_tray else
                    "Not available here: there's no notification area to keep running in.",
                    switch(self.keep_tray_var, enabled=has_tray))
        setting_row(card, self.look, "Show a notification for alerts",
                    "When an alert starts or ends. Click it to open that target.",
                    switch(self.notify_var, enabled=has_tray))

    def _toggle_autostart(self) -> None:
        on = bool(self.autostart_var.get())
        if winplatform.set_autostart(on) and on:
            self.resume_var.set(True)       # starting with nothing to resume is pointless
        self.autostart_var.set(winplatform.autostart_enabled())

    # --- the notification area ---------------------------------------------------
    def _start_tray(self) -> None:
        if not traymod.AVAILABLE:
            return
        t = traymod.TrayIcon(
            lambda state, size: appicon.png_bytes(size, state), "PingerPlot", self._tray_menu,
            on_open=lambda: self._post(self._show_window),
            on_show=lambda: self._post(self._show_window),
            on_theme=lambda: self._post(self._on_system_theme))
        if t.start():
            self.tray = t
            self.root.after(50, self._drain_ui)

    def _post(self, fn) -> None:
        """Run ``fn`` on Tk's thread. The tray's callbacks come from its own
        thread, and a Tk call from another thread waits for the main loop."""
        self._ui_jobs.put(fn)

    def _drain_ui(self) -> None:
        try:
            while True:
                try:
                    fn = self._ui_jobs.get_nowait()
                except queue.Empty:
                    break
                fn()
        finally:
            self.root.after(50, self._drain_ui)

    def _tray_menu(self) -> list:
        """Built on the tray thread: reads state, never touches Tk."""
        M = traymod.MenuItem
        running = any(m.running for n, m in self._monitors.items() if n not in self._loaded)
        items = [M("Open PingerPlot", lambda: self._post(self._show_window), default=True), M(),
                 M("Stop all", lambda: self._post(self._stop_all), enabled=running)]
        if winplatform.IS_WINDOWS:
            items.append(M("Start with Windows", lambda: self._post(self._tray_toggle_autostart),
                           checked=winplatform.autostart_enabled()))
        items += [M(), M("Quit", lambda: self._post(self._quit))]
        return items

    def _tray_toggle_autostart(self) -> None:
        self.autostart_var.set(not winplatform.autostart_enabled())
        self._toggle_autostart()

    def _show_window(self) -> None:
        self.root.deiconify()
        if self.root.state() == "iconic":
            self.root.state("normal")
        self.root.lift()
        self.root.focus_force()
        self._draw_ver = None

    def _on_system_theme(self) -> None:
        if self.theme_var.get() == "system":
            self._theme_checked = 0.0
            self._follow_system_theme()

    def _update_tray(self) -> None:
        if self.tray is None:
            return
        live = [(n, m) for n, m in self._monitors.items() if n not in self._loaded]
        running = sum(1 for _n, m in live if m.running and not m.paused)
        alerting = sum(1 for _n, m in live if m.running and m.active_alerts())
        state = "alert" if alerting else ("normal" if running else "idle")
        tip = "PingerPlot" + (f" · {alerting} alerting" if alerting else "") + (
            f" · monitoring {running}" if running else " · not monitoring")
        if (state, tip) != self._tray_state:
            self._tray_state = (state, tip)
            self.tray.set_state(state, tip)
        if not self.notify_var.get():
            return
        for name, mon in live:
            new, last = mon.events_after(self._notify_seq.get(name, -1))
            self._notify_seq[name] = last
            for e in new:
                if e.kind not in ("alert", "clear"):
                    continue
                text = e.text.split(": ", 1)[1] if e.kind == "alert" and ": " in e.text else e.text
                self.tray.notify(f"{name}: {'alert' if e.kind == 'alert' else 'back to normal'}",
                                 text, on_click=lambda n=name: self._post(lambda: self._open_target(n)))

    def _open_target(self, name: str) -> None:
        self._show_window()
        self.nav.select("targets", notify=True)
        if name in self._monitors:
            self._select_card(name)

    def report_url(self) -> str:
        """The bug-report form, with the version filled in (an issue form
        takes a field's value from the query string by the field's id)."""
        return f"{REPO_URL}/issues/new?" + urllib.parse.urlencode(
            {"template": "bug_report.yml", "version": __version__})

    # --- defaults and starting ---------------------------------------------------
    def _default_options(self) -> Optional[dict]:
        """start() options from the Settings page, or None (after saying
        why) when a number field doesn't parse."""
        try:
            return self.defaults.to_options()
        except ValueError as exc:
            messagebox.showwarning("Check the settings", str(exc))
            return None

    def _focus_add(self) -> None:
        self.nav.select("targets", notify=True)
        self.add_entry.focus_set()
        self.add_entry.select_range(0, "end")

    def _start(self, target: Optional[str] = None, options: Optional[dict] = None) -> None:
        """Start (or restart) ``target`` -- the Add box by default.

        Without ``options``: a target already in the list that's running is
        just selected; one that's stopped starts again with its own settings;
        a new one gets the Settings page's defaults. With ``options`` (Edit
        settings, a restored session) it (re)starts with exactly those."""
        from_box = target is None
        target = (self.target_var.get() if target is None else target).strip()
        if not target:
            messagebox.showwarning("No target", "Enter a host name or IP address.")
            return
        mon = self._monitors.get(target)
        if options is None:
            if mon is not None and target not in self._loaded:
                if mon.running:
                    self._activate(target)
                    return
                options = mon.start_options()
            else:
                options = self._default_options()
                if options is None:
                    return
        options = dict(options)
        # One log setting, many targets: each gets its own file, or their rows
        # interleave in a CSV that has no target column.
        options["log_path"] = monitor.per_target_log_path(
            str(options.get("log_path") or "").strip(), target)

        if mon is None:
            mon = Monitor()
            self._monitors[target] = mon
        mon.start(target, **options)
        self._notify_seq[target] = -1       # a restart numbers its events from 0 again
        if from_box:
            self.target_var.set("")
        self._activate(target)

    def _activate(self, name: Optional[str]) -> None:
        """Show ``name`` in the detail views, from a clean slate."""
        self._active = name
        self._selected_ttl = None
        self._last_ttl = None
        self._pinned = False
        self._last_event_seq = -1
        self._draw_ver = None
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for iid in self.events_tree.get_children():
            self.events_tree.delete(iid)
        self._refresh_once()

    def _active_monitor(self) -> Optional[Monitor]:
        return self._monitors.get(self._active) if self._active else None

    def _toggle_pause(self) -> None:
        mon = self._active_monitor()
        if mon is not None and mon.running:
            (mon.resume if mon.paused else mon.pause)()
        self._refresh_once()

    def _toggle_run(self) -> None:
        mon = self._active_monitor()
        if mon is None or self._active in self._loaded:
            return
        if mon.running:
            mon.stop()
            self._refresh_once()
        else:
            self._start(self._active, mon.start_options())

    def _edit_active(self) -> None:
        if self._active and self._active not in self._loaded:
            self._edit_target(self._active)

    def _edit_target(self, name: str) -> None:
        EditTargetDialog(self, name)

    def _stop(self) -> None:
        mon = self._active_monitor()
        if mon is not None:
            mon.stop()

    def _stop_all(self) -> None:
        # Signal every monitor before waiting on any: each stop() can wait up
        # to a reply timeout for a round in flight, and one at a time those
        # waits added up into a frozen window.
        mons = list(self._monitors.values())
        for mon in mons:
            mon.request_stop()
        for mon in mons:
            mon.stop()
        self._refresh_once()

    def _remove_target(self) -> None:
        self._remove_named(self._active)

    def _remove_named(self, name: Optional[str]) -> None:
        if not name or name not in self._monitors:
            return
        self._monitors.pop(name).shutdown()
        self._loaded.discard(name)
        card = self._cards.pop(name, None)
        if card is not None:
            card.destroy()
        if self._active == name:
            self._activate(next(iter(self._monitors), None))
        else:
            self._refresh_once()

    def _select_card(self, name: str) -> None:
        if name != self._active:
            self._activate(name)

    def _on_user_select(self, _event: object) -> None:
        sel = self.tree.selection()
        if sel:
            try:
                self._selected_ttl = int(sel[0])
                self._pinned = True
            except ValueError:
                pass
        self._draw_graph()

    # --- menus --------------------------------------------------------------------
    def _menu(self, parent) -> tk.Menu:
        return tk.Menu(parent, tearoff=0, **self.look.menu_colours())

    def _popup(self, menu: tk.Menu, x: int, y: int) -> None:
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _header_menu(self, button) -> None:
        m = self._menu(self.root)
        m.add_command(label="Export hops as CSV…", command=self._export)
        m.add_command(label="Save session…", command=self._save_session)
        m.add_command(label="Open a saved session…", command=self._load_session)
        m.add_command(label="Compare with a saved session…", command=self._compare_with_baseline)
        m.add_separator()
        m.add_command(label="Report a problem…", command=lambda: webbrowser.open(self.report_url()))
        m.add_command(label="Project page", command=lambda: webbrowser.open(REPO_URL))
        m.add_separator()
        m.add_command(label="Quit", command=self._quit)
        self._popup(m, button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())

    def _target_menu(self, name: str, x: int, y: int) -> None:
        if name not in self._monitors:
            return
        self._select_card(name)
        mon = self._monitors[name]
        loaded = name in self._loaded
        m = self._menu(self.root)
        if not loaded:
            m.add_command(label="Edit settings…", command=lambda: self._edit_target(name))
            m.add_separator()
            if mon.running:
                m.add_command(label="Resume" if mon.paused else "Pause",
                              command=lambda: ((mon.resume if mon.paused else mon.pause)(),
                                               self._refresh_once()))
                m.add_command(label="Stop", command=lambda: (mon.stop(), self._refresh_once()))
            else:
                m.add_command(label="Start", command=lambda: self._start(name, mon.start_options()))
            m.add_separator()
        m.add_command(label="Remove" if not loaded else "Close", command=lambda: self._remove_named(name))
        self._popup(m, x, y)

    def _on_hop_menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self._on_user_select(event)
        vals = self.tree.item(iid, "values")
        ip = str(vals[1]) if len(vals) > 1 else ""
        host = str(vals[2]) if len(vals) > 2 else ""
        m = self._menu(self.tree)
        if ip and ip != "*":
            m.add_command(label=f"Copy address   {ip}", command=lambda: self._clip(ip))
        if host and host not in ("", "(resolving…)"):
            m.add_command(label=f"Copy hostname   {host}", command=lambda: self._clip(host))
        m.add_command(label="Copy row", command=lambda: self._clip("\t".join(str(v) for v in vals)))
        self._popup(m, event.x_root, event.y_root)

    def _clip(self, text: str) -> None:
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._note("Copied")

    def _note(self, text: str, seconds: float = 5.0) -> None:
        """A passing confirmation in the header (copied, exported, saved)."""
        self.note_label.configure(text=text)
        self._note_until = time.monotonic() + seconds

    def _on_close(self) -> None:
        """The window's close button: to the tray if it's there and wanted,
        otherwise quit."""
        if self.tray is not None and self.keep_tray_var.get():
            self._save_settings()           # a crash later shouldn't cost the target list
            self.root.withdraw()
            if not self._settings.get("told_hidden"):
                self._settings["told_hidden"] = True
                self.tray.notify("PingerPlot is still running",
                                 "Your targets are still being monitored. Quit from this icon's menu.",
                                 on_click=lambda: self._post(self._show_window))
            return
        self._quit()

    def _quit(self) -> None:
        self._save_settings()
        if self.tray is not None:
            self.tray.stop()
            self.tray = None
        self.geo.stop()
        mons = list(self._monitors.values())
        for mon in mons:
            mon.request_stop()      # all at once, as in _stop_all
        for mon in mons:
            mon.shutdown()
        self._empty.shutdown()
        tkthreads.drain()
        self.root.destroy()

    # --- settings persistence ---------------------------------------------------
    def _apply_saved_settings(self) -> None:
        """The saved defaults and app options, into their controls."""
        s = self._settings
        self.defaults.load_settings(s)
        for var, key in ((self.resume_var, "resume_on_launch"), (self.keep_tray_var, "keep_in_tray"),
                         (self.notify_var, "notify")):
            if isinstance(s.get(key), bool):
                var.set(s[key])

    def _save_settings(self) -> None:
        """Persist the defaults, app options and the target list."""
        data = {"theme": self.theme_var.get(),
                "resume_on_launch": bool(self.resume_var.get()),
                "keep_in_tray": bool(self.keep_tray_var.get()),
                "notify": bool(self.notify_var.get()),
                "told_hidden": bool(self._settings.get("told_hidden", False))}
        self.defaults.save_settings(data)
        # Live targets only. A loaded session lives in the same dict so the
        # detail views can render it, but its key is "<name> (loaded)" -- not
        # a hostname. Saving it meant _restore_targets called _start() on it
        # next launch, leaving a permanently dead "Cannot resolve" target.
        data["targets"] = [n for n in self._monitors if n not in self._loaded]
        # Each target's own configuration: restoring from the defaults alone
        # gave every target the same values.
        data["target_options"] = {n: m.start_options() for n, m in self._monitors.items()
                                  if n not in self._loaded}
        settings.save(data)

    def _restore_targets(self) -> None:
        """If enabled, re-add and start the last session's targets, each with
        the options it was running with."""
        if not self.resume_var.get():
            return
        saved = self._settings.get("target_options")
        saved = saved if isinstance(saved, dict) else {}
        for name in self._settings.get("targets", []) or []:
            if not (isinstance(name, str) and name.strip()):
                continue
            opts = saved.get(name)
            if isinstance(opts, dict):
                opts = {k: v for k, v in opts.items() if k in monitor.START_OPTION_KEYS}
            else:
                opts = None              # 1.3.x settings: names only
            try:
                self._start(name, opts)
            except (ValueError, TypeError):
                # A hand-edited or corrupt entry. This runs inside __init__, so
                # raising would stop the app opening at all; the defaults are
                # the 1.3.x behaviour.
                self._start(name, self._default_options())
        self.target_var.set("")

    # --- export, sessions, comparison ------------------------------------------
    def _export(self) -> None:
        views, _status, target_ip, target_input = self.monitor.snapshot()
        if not views:
            messagebox.showinfo("Nothing to export", "No hop data yet.")
            return
        path = filedialog.asksaveasfilename(
            title="Export hop statistics", defaultextension=".csv",
            initialfile=f"pingerplot_{target_input or 'route'}.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        info = self.monitor.run_summary()
        mode = info["packet_type"].upper()
        if info["packet_type"] != "icmp":
            mode += f":{info['port']}"

        def _iso(t):
            return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) if t else "?"

        ws, we = info["window_start"], info["window_end"]
        window = f"{we - ws:.0f}s ({_iso(ws)} to {_iso(we)})" if ws and we else "?"
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                # Self-documenting header: records exactly how this run was made,
                # so two exports can actually be compared after the fact.
                fh.write(f"# PingerPlot {info['version']} export - {_iso(time.time())}\n")
                fh.write(f"# target={info['target_input']} [{info['target_ip']}]  mode={mode}\n")
                fh.write(f"# interval={info['interval']}s  timeout={info['timeout_ms']}ms  "
                         f"packet_size={info['packet_size']}B  send_delay={info['send_delay_ms']}ms  "
                         f"max_hops={info['max_hops']}  "
                         f"final_hop_only={'yes' if info['final_hop_only'] else 'no'}\n")
                fh.write(f"# samples={info['samples']}  window={window}\n")
                w = csv.writer(fh)
                w.writerow(["hop", "ip", "hostname", "sent", "received", "loss_pct",
                            "current_ms", "avg_ms", "min_ms", "max_ms", "jitter_ms", "status", "mos"])
                last_ttl = views[-1].ttl
                for v in views:
                    m = mos(v.avg, v.jitter, v.loss_pct) if v.ttl == last_ttl else None
                    w.writerow([
                        v.ttl, csv_safe(v.address or ""), csv_safe(v.hostname or ""),
                        v.sent, v.received,
                        f"{v.loss_pct:.1f}", _csv_num(v.current), _csv_num(v.avg),
                        _csv_num(v.best), _csv_num(v.worst), _csv_num(v.jitter),
                        icmp.status_text(v.last_status) if v.last_status is not None else "",
                        f"{m:.2f}" if m is not None else "",
                    ])
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        self._note(f"Exported {len(views)} hops")

    def _save_session(self) -> None:
        data = self.monitor.to_dict()
        if not data.get("hops"):
            messagebox.showinfo("Nothing to save", "No hop data yet.")
            return
        path = filedialog.asksaveasfilename(
            title="Save session", defaultextension=".json",
            initialfile=f"pingerplot_{data.get('target_input') or 'session'}.json",
            filetypes=[("PingerPlot session", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, path)   # atomic: never leave a truncated session file
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self._note(f"Saved session ({len(data['hops'])} hops)")

    def _load_session(self) -> None:
        path = filedialog.askopenfilename(
            title="Open a saved session",
            filetypes=[("PingerPlot session", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Load failed", str(exc))
            return
        if not isinstance(data, dict) or "hops" not in data:
            messagebox.showerror("Load failed", "Not a PingerPlot session file.")
            return
        name = f"{data.get('target_input') or 'loaded session'} (loaded)"
        # Loaded into a fresh Monitor and only then registered: registering
        # first left an empty "(loaded)" row behind whenever the load raised.
        mon = Monitor()
        try:
            mon.load_dict(data)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Load failed", f"Could not load that session: {exc}")
            return
        old = self._monitors.get(name)
        if old is not None:
            old.shutdown()
        self._monitors[name] = mon
        self._loaded.add(name)
        self.nav.select("targets", notify=True)
        self._activate(name)

    def _compare_with_baseline(self) -> None:
        """Diff the selected target against a session saved earlier: what says
        whether today's path differs from last Tuesday's."""
        views, _status, _ip, target_input = self.monitor.snapshot()
        if not views:
            messagebox.showinfo("Nothing to compare",
                                "Select a target with data first: there is nothing to compare yet.")
            return
        path = filedialog.askopenfilename(
            title="Choose a saved session to compare against",
            filetypes=[("PingerPlot session", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Compare failed", str(exc))
            return
        if not isinstance(data, dict):
            messagebox.showerror("Compare failed", "Not a PingerPlot session file.")
            return
        base = cmpmod.stats_from_session(data)
        if not base:
            messagebox.showerror("Compare failed", "That session has no hop data to compare against.")
            return
        base_target = str(data.get("target_input") or os.path.basename(path))
        if base_target and target_input and base_target != target_input:
            # Comparing two different destinations is almost always a mis-click,
            # and the per-hop numbers would be meaningless. Warn, but allow it:
            # the same host is legitimately reachable under two names.
            if not messagebox.askyesno(
                    "Different target",
                    f"The saved session is for '{base_target}' but this target is "
                    f"'{target_input}'.\n\nCompare anyway?"):
                return
        now = cmpmod.stats_from_views(views)
        self._show_comparison(cmpmod.compare(base, now, target=target_input), base_target)

    def _show_comparison(self, cmp_, baseline_label: str) -> None:
        S = self.s
        win = tk.Toplevel(self.root)
        win.title(f"Compare: now and {baseline_label}")
        win.transient(self.root)
        win.geometry(f"{S(920)}x{S(480)}")
        self.look.style_window(win)
        body = ttk.Frame(win, padding=(S(20), S(16)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=cmp_.summary(), style="Strong.TLabel").pack(side="top", anchor="w")
        foot = ttk.Frame(body, padding=(0, S(12), 0, 0))
        foot.pack(side="bottom", fill="x")
        ttk.Button(foot, text="Copy as text", command=lambda: self._clip(
            "\n".join(cmpmod.format_comparison(cmp_, baseline_label)))).pack(side="left")
        ttk.Button(foot, text="Close", command=win.destroy).pack(side="right")
        card = self._card(body)
        card.pack(side="top", fill="both", expand=True, pady=(S(10), 0))
        cols = ("ttl", "address", "loss", "d_loss", "avg", "d_avg", "verdict")
        tree = ttk.Treeview(card, columns=cols, show="headings", selectmode="browse")
        for key, title, width, anchor in (
            ("ttl", "Hop", 44, "center"), ("address", "Address", 150, "w"),
            ("loss", "Loss", 62, "e"), ("d_loss", "Δ Loss", 74, "e"),
            ("avg", "Avg ms", 70, "e"), ("d_avg", "Δ Avg", 74, "e"),
            ("verdict", "Change", 260, "w"),
        ):
            tree.heading(key, text=title)
            tree.column(key, width=S(width), anchor=anchor, stretch=(key == "verdict"))
        for tag in ("bad", "warn", "ok", "dest"):
            tree.tag_configure(tag, background=COLORS[tag], foreground=COLORS["fg"])
        for r in cmp_.rows:
            side = r.now or r.base
            note = r.verdict
            if r.verdict == cmpmod.REROUTED and r.base and r.now:
                note = f"rerouted  {r.base.address} → {r.now.address}"
            tree.insert("", "end", values=(
                r.ttl, r.address or "*",
                "—" if side is None else f"{side.loss_pct:.0f}%",
                "" if r.d_loss is None else f"{r.d_loss:+.0f}%",
                "—" if side is None or side.avg is None else _ms(side.avg),
                "" if r.d_avg is None else f"{r.d_avg:+.1f}",
                note,
            ), tags=(self._verdict_tag(r.verdict),))
        vsb = ttk.Scrollbar(card, orient="vertical", command=tree.yview, style="Card.Vertical.TScrollbar")
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)

    @staticmethod
    def _verdict_tag(verdict: str) -> str:
        """Red for a regression, amber for anything that moved, plain for the
        rest - so the eye lands on the hops that got worse."""
        if verdict == cmpmod.WORSE:
            return "bad"
        if verdict in (cmpmod.REROUTED, cmpmod.NEW, cmpmod.GONE):
            return "warn"
        if verdict == cmpmod.BETTER:
            return "dest"
        return "ok"

    # --- refresh loop -------------------------------------------------------------
    def _refresh(self) -> None:
        # Re-arm first-and-always: an exception in one redraw is reported by
        # Tk and must not also end the timer, which froze every target's
        # display while the monitors carried on probing behind it.
        try:
            tkthreads.drain()
            self._refresh_once()
        finally:
            self.root.after(REFRESH_MS, self._refresh)

    def _refresh_once(self) -> None:
        self._follow_system_theme()
        self._update_header()
        self._update_tray()
        if self.root.state() == "withdrawn":
            return              # in the tray: nothing on screen to update
        self._update_cards()
        views, status, target_ip, _target_input = self.monitor.snapshot()
        self._update_detail(views, status, target_ip)
        self._update_table(views)
        self._update_events()
        # Redraw the visible canvas only when its data changed since the last
        # tick (probes arrive every `interval`, but we tick faster). Resize and
        # view changes redraw through their own bindings.
        ver = draw_version(self._active, self._active_monitor(), self._selected_ttl,
                           self.theme, self.geo.count())
        if ver != self._draw_ver:
            self._draw_ver = ver
            self._draw_active_view()

    def _state_of(self, name: str, mon: Monitor) -> str:
        if name in self._loaded or not mon.running:
            return "idle"
        if mon.paused:
            return "paused"
        if mon.active_alerts():
            return "alert"
        n, loss, avg, _j, _reached = mon.summary()
        if n and loss is not None and (loss > 0 or (avg is not None and avg >= BAD_MS)):
            return "warn"
        return "ok"

    def _update_header(self) -> None:
        live = [(n, m) for n, m in self._monitors.items() if n not in self._loaded]
        running = sum(1 for _n, m in live if m.running and not m.paused)
        alerting = sum(1 for _n, m in live if m.running and m.active_alerts())
        c = self.look.c
        if alerting:
            dot, text = c["red"], f"{alerting} alerting  ·  monitoring {running}"
        elif running:
            dot, text = c["green"], f"Monitoring {running} target{'s' if running != 1 else ''}"
        elif live:
            dot, text = c["text3"], "Stopped"
        else:
            dot, text = c["text3"], "Not monitoring"
        self.status_dot.configure(foreground=dot)
        self.status_text.configure(text=text)
        title = "PingerPlot" + (f" · {alerting} alerting" if alerting else "")
        if self.root.title() != title:
            self.root.title(title)
        if self._note_until and time.monotonic() > self._note_until:
            self.note_label.configure(text="")
            self._note_until = 0.0

    def _update_cards(self) -> None:
        for name in list(self._cards):
            if name not in self._monitors:
                self._cards.pop(name).destroy()
        S = self.s
        for name, mon in self._monitors.items():
            card = self._cards.get(name)
            if card is None:
                card = TargetCard(self.cards_scroll.inner, self.look, name,
                                  self._select_card, self._target_menu)
                card.pack(side="top", fill="x", pady=(0, S(8)))
                self._cards[name] = card
            state = self._state_of(name, mon)
            n, loss, avg, jitter, reached = mon.summary()
            if name in self._loaded:
                stats = "Saved session" + (f"  ·  {n} hops" if n else "")
            elif not mon.running:
                stats = "Stopped"
            elif mon.paused:
                stats = "Paused"
            elif not n:
                stats = "Tracing the route…"
            else:
                score = mos(avg, jitter, loss) if reached else None
                parts = [f"{n} hop{'s' if n != 1 else ''}",
                         f"{loss:.0f}% loss" if loss is not None else "",
                         f"{fmt_ms(avg)} ms" if avg is not None else "no reply",
                         f"MOS {score:.1f}" if score is not None else ""]
                stats = "  ·  ".join(p for p in parts if p)
            card.update_card(state, name, stats, selected=(name == self._active))
        live = sum(1 for n in self._monitors if n not in self._loaded)
        self.targets_caption.configure(text=f"TARGETS  ·  {live}" if live else "TARGETS")
        if self._monitors:
            self.cards_empty.pack_forget()
        elif not self.cards_empty.winfo_ismapped():
            self.cards_empty.pack(side="top", anchor="w")

    def _update_detail(self, views: List[HopView], status: str, target_ip: Optional[str]) -> None:
        mon = self._active_monitor()
        name = self._active
        loaded = name in self._loaded if name else False
        if mon is None:
            self.detail_title.configure(text="No target selected")
            self.detail_status.configure(text="Add one on the left, or choose one from the list.")
            for b in (self.pause_btn, self.stop_btn, self.edit_btn):
                b.state(["disabled"])
            self.mos_label_w.configure(text="")
            self.detail_stats.configure(text="")
            self._set_banner([])
            return
        shown = name if not target_ip or target_ip == name else f"{name}  ({target_ip})"
        self.detail_title.configure(text=shown)
        self.detail_status.configure(text=self._describe(mon, status))
        self.pause_btn.configure(text="Resume" if mon.paused else "Pause")
        self.pause_btn.state(["!disabled"] if (mon.running and not loaded) else ["disabled"])
        self.stop_btn.configure(text="Stop" if mon.running else "Start")
        self.stop_btn.state(["disabled"] if loaded else ["!disabled"])
        self.edit_btn.state(["disabled"] if loaded else ["!disabled"])

        c = self.look.c
        dest = views[-1] if views else None
        score = mos(dest.avg, dest.jitter, dest.loss_pct) if (dest and mon.reached_target) else None
        if score is None:
            self.mos_label_w.configure(text="")
        else:
            colour = c["green"] if score >= 4.0 else c["amber"] if score >= 3.1 else c["red"]
            self.mos_label_w.configure(text=f"MOS {score:.1f} · {mos_label(score)}", foreground=colour)
        if dest is not None:
            jit = f"  ·  jitter {fmt_ms(dest.jitter)} ms" if dest.jitter is not None else ""
            avg = f"{fmt_ms(dest.avg)} ms average" if dest.avg is not None else "no reply yet"
            self.detail_stats.configure(
                text=f"Destination: {dest.loss_pct:.0f}% loss  ·  {avg}{jit}  ·  {dest.sent} probes")
        else:
            self.detail_stats.configure(text="")
        self._set_banner(mon.active_alerts())

    @staticmethod
    def _describe(mon: Monitor, status: str) -> str:
        """How a monitoring target runs, in one line; the engine's own status
        for everything else (resolving, tracing, paused, stopped and why).
        The engine's monitoring line repeats the name and address the title
        already shows, and the alert count the banner already shows."""
        if not mon.running or mon.paused or not status.startswith("Monitoring"):
            return status
        proto = mon.packet_type.upper() + (f" port {mon.port}" if mon.packet_type != "icmp" else "")
        parts = [proto, f"every {mon.interval:g} s",
                 "destination only" if mon.final_hop_only
                 else f"{mon.route_len} hop{'s' if mon.route_len != 1 else ''}"]
        if mon.dscp:
            parts.append(f"DSCP {mon.dscp}")
        if mon.source_ip:
            parts.append(f"from {mon.source_ip}")
        if not mon.reached_target:
            parts.append("the destination isn't answering")
        return "  ·  ".join(parts)

    def _set_banner(self, alerts: List[str]) -> None:
        if alerts:
            self.banner_label.configure(text="\n".join(alerts))
            if not self._banner_shown:
                self.banner.pack(side="top", fill="x", pady=(self.s(10), 0), before=self._banner_anchor)
                self._banner_shown = True
        elif self._banner_shown:
            self.banner.pack_forget()
            self._banner_shown = False

    _row_tag = staticmethod(row_tag)

    def _update_table(self, views: List[HopView]) -> None:
        desired = {str(v.ttl) for v in views}
        for iid in self.tree.get_children():
            if iid not in desired:
                self.tree.delete(iid)
        last_ttl = views[-1].ttl if views else None
        for v in views:
            iid = str(v.ttl)
            hostname = v.hostname if v.hostname else ("(resolving…)" if v.address and v.hostname is None else "")
            values = (v.ttl, v.address or "*", hostname, f"{v.loss_pct:.0f}%", v.sent,
                      _ms(v.current), _ms(v.avg), _ms(v.best), _ms(v.worst), _ms(v.jitter))
            tag = row_tag(v, is_dest=(v.ttl == last_ttl))
            if self.tree.exists(iid):
                self.tree.item(iid, values=values, tags=(tag,))
            else:
                self.tree.insert("", "end", iid=iid, values=values, tags=(tag,))
        self._last_ttl = last_ttl
        if not self._pinned and last_ttl is not None:
            self._selected_ttl = last_ttl
            if self.tree.exists(str(last_ttl)):
                self.tree.selection_set(str(last_ttl))

    def _update_events(self) -> None:
        new, last = self.monitor.events_after(self._last_event_seq)
        for e in new:
            ts = time.strftime("%H:%M:%S", time.localtime(e.t))
            tag = e.kind if e.kind in ("alert", "clear", "route", "warn") else "info"
            self.events_tree.insert("", "end", values=(ts, e.kind.title(), e.text), tags=(tag,))
        if new:
            self.events_tree.yview_moveto(1.0)
        self._last_event_seq = last

    # --- drawing (see gui_charts) ------------------------------------------------
    def _draw_active_view(self) -> None:
        if not self.targets_page.winfo_ismapped() and self.settings_page is not None:
            return
        view = self.detail_nav.current
        if view == "hops":
            self._draw_graph()
        elif view == "timeline":
            self._draw_timeline()
        elif view == "map":
            self._draw_map()

    def _draw_graph(self) -> None:
        ttl = self._selected_ttl
        samples = self.monitor.samples_for(ttl) if ttl is not None else []
        draw_graph(self.canvas, self._ink(), ttl, samples)

    def _draw_timeline(self) -> None:
        views, _s, _ip, _in = self.monitor.snapshot()
        caption = draw_timeline(self.timeline_canvas, self._ink(), views, self.monitor.all_samples())
        if caption:
            self.timeline_header.configure(text=caption)

    def _draw_map(self) -> None:
        if self.detail_nav.current != "map":
            return
        if not self._geo_started:      # opt-in: only talk to ipwho.is once the
            self.geo.start()           # Map view is actually opened
            self._geo_started = True
        views, _s, _ip, _in = self.monitor.snapshot()
        caption = draw_map(self.map_canvas, self._ink(), views, self.geo)
        if caption:
            self.map_header.configure(text=caption)


def _enable_windows_dpi_awareness() -> None:
    """Kept for callers of the 1.3 name; see winplatform.enable_dpi_awareness."""
    winplatform.enable_dpi_awareness()


def main() -> None:
    if "--version" in sys.argv[1:]:
        # Exits before Tk is touched, which makes this the one way to run the
        # windowed build without a display -- and therefore the way CI proves a
        # frozen binary's import graph is intact. gui imports geoip imports
        # urllib.request imports email, so a bad PyInstaller exclude fails here
        # rather than in a message box on a user's desktop. That is not
        # hypothetical: it is exactly how the first build of this was broken.
        print(f"PingerPlot {__version__}")
        return
    winplatform.enable_dpi_awareness()
    winplatform.set_app_id()
    # One copy at a time: two would each restore and save the same target list
    # and probe every target twice. A second launch shows the first instead.
    instance = winplatform.SingleInstance()
    if instance.already_running and instance.signal_existing():
        return
    root = tk.Tk()
    App(root, use_tray=True, start_minimized="--minimized" in sys.argv[1:])
    root.mainloop()
    instance.release()


if __name__ == "__main__":
    main()
