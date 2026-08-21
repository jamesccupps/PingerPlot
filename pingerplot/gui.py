"""Tkinter UI: hop table, single-hop graph, stacked all-hops timeline, and an
event log, plus a live alert banner.

Pure standard library. The GUI never touches monitor internals; it polls the
snapshot accessors on a timer and renders immutable copies, so there are no
cross-thread Tk calls. Heavy canvases are redrawn only while their tab is
visible; the table, banner, and event log update every tick (they are cheap).
"""
from __future__ import annotations

import csv
import ctypes
import json
import os
import sys
import time
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
from typing import Dict, List, Optional

from . import __version__, appicon, compare as cmpmod, geoip, icmp, settings, tcpudp, worldmap
from .model import HopView, Sample, csv_safe, draw_version, mos, mos_label
from . import monitor
from .monitor import Monitor

REFRESH_MS = 700

WARN_MS = 120.0   # row/label goes amber at/above this average latency
BAD_MS = 250.0    # ...and at/above this it is treated as a problem

# Two palettes with identical keys. COLORS is the *active* one (mutated in place
# on theme switch so the draw code, which reads COLORS at call time, follows).
LIGHT = {
    "ok": "#ffffff", "warn": "#fff3cd", "bad": "#f8d7da", "dest": "#e7f1ff",
    "graph_bg": "#ffffff", "graph_border": "#c9ced6", "grid": "#eef0f3",
    "line": "#1f77b4", "fill": "#d6e6f4", "loss": "#d62728",
    "axis_text": "#666666", "title": "#222222",
    "strip_sep": "#e7e9ee", "strip_border": "#e3e6ea",
    "banner_bg": "#c0392b", "banner_fg": "#ffffff",
    "fg_bad": "#c0392b", "fg_warn": "#b9770e", "fg_dest": "#1f5fbf", "fg_ok": "#333333",
    "ev_alert": "#fdecea", "ev_clear": "#eafaf1", "ev_route": "#eaf2fb",
    "mos_good": "#2e7d32", "mos_mid": "#b9770e", "mos_bad": "#c0392b",
    # ttk widget styling
    "ttk_theme": "vista", "win_bg": "#f0f0f0", "panel": "#f0f0f0", "field": "#ffffff",
    "fg": "#000000", "sel_bg": "#cce4ff", "sel_fg": "#000000", "active": "#e5f1fb",
    "border": "#c9ced6", "hint": "#888888", "status_bg": "#f0f0f0",
}
DARK = {
    "ok": "#1c1e22", "warn": "#4a3f1e", "bad": "#4a2329", "dest": "#21384f",
    "graph_bg": "#1c1e22", "graph_border": "#3a3d44", "grid": "#33363c",
    "line": "#4aa3df", "fill": "#163049", "loss": "#ff5a67",
    "axis_text": "#9aa0a8", "title": "#e4e6eb",
    "strip_sep": "#2a2d33", "strip_border": "#33363c",
    "banner_bg": "#c0392b", "banner_fg": "#ffffff",
    "fg_bad": "#ff6b6b", "fg_warn": "#e0a93b", "fg_dest": "#5aa9ff", "fg_ok": "#c8ccd2",
    "ev_alert": "#3d2528", "ev_clear": "#233a2a", "ev_route": "#21344a",
    "mos_good": "#5fd07a", "mos_mid": "#e0a93b", "mos_bad": "#ff6b6b",
    "ttk_theme": "clam", "win_bg": "#23252a", "panel": "#2d3036", "field": "#1c1e22",
    "fg": "#e4e6eb", "sel_bg": "#2f5d8a", "sel_fg": "#ffffff", "active": "#34373d",
    "border": "#3a3d44", "hint": "#9aa0a8", "status_bg": "#23252a",
}
PALETTES = {"light": LIGHT, "dark": DARK}
COLORS = dict(DARK)  # default to dark

COLUMNS = [
    ("ttl", "Hop", 44, "center"),
    ("address", "IP Address", 130, "w"),
    ("hostname", "Hostname", 240, "w"),
    ("loss", "Loss", 60, "e"),
    ("sent", "Sent", 55, "e"),
    ("cur", "Cur", 60, "e"),
    ("avg", "Avg", 60, "e"),
    ("best", "Min", 60, "e"),
    ("worst", "Max", 60, "e"),
    ("jitter", "Jitter", 60, "e"),
]


def _ms(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if v < 1:
        return "<1"
    if v < 10:
        return f"{v:.1f}"
    return f"{v:.0f}"


def _csv_num(v: Optional[float]) -> str:
    return "" if v is None else f"{v:.1f}"



class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._monitors: "dict[str, Monitor]" = {}   # target name -> its Monitor
        self._active: Optional[str] = None           # which one the detail tabs show
        self._empty = Monitor()                      # placeholder when nothing selected
        self.geo = geoip.GeoResolver()               # for the Map tab (lazy/opt-in)
        self._geo_started = False
        self._selected_ttl: Optional[int] = None   # hop currently graphed
        self._last_ttl: Optional[int] = None        # destination hop, per refresh
        self._pinned = False                        # True once the user picks a row
        self._last_event_seq = -1
        self._banner_shown = False
        self._draw_ver = None                        # last-drawn (target, round, hop, theme)
        self._settings = settings.load()             # persisted UI/engine/alert state

        self.scale = self._init_scaling()
        root.title(f"PingerPlot {__version__}")
        self._set_window_icon()
        root.geometry(f"{self.s(1120)}x{self.s(740)}")
        root.minsize(self.s(820), self.s(520))
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        saved_theme = self._settings.get("theme", "dark")
        if saved_theme not in PALETTES:
            saved_theme = "dark"
        self.theme = saved_theme
        COLORS.clear()
        COLORS.update(PALETTES[saved_theme])
        self.theme_var = tk.StringVar(value=saved_theme)
        self.resume_var = tk.BooleanVar(value=True)   # resume last targets on launch
        self.style = ttk.Style(root)
        self._apply_ttk_style()          # theme ttk widgets before they are built
        root.configure(bg=COLORS["win_bg"])

        self._init_engine_vars()
        self._build_menubar()
        self._build_controls()
        self._build_alert_controls()
        self._build_banner()
        self._build_statusbar()
        self._build_main()
        self._recolor_widgets()          # tk-widget colors + tree tags for the theme
        self._apply_saved_settings()     # restore last engine/alert/interval values

        if not icmp.is_available():
            # Say what to do about it. On Linux this is one sysctl away, and
            # "unsupported platform" would send the user looking for a port
            # that already exists.
            messagebox.showerror(
                "ICMP backend unavailable",
                icmp.unavailable_reason() + "\n\n"
                "TCP and UDP probe modes do not use this backend and may still work.",
            )

        self._restore_targets()          # optionally resume the last session's targets
        self.root.after(REFRESH_MS, self._refresh)

    @property
    def monitor(self) -> Monitor:
        """The monitor whose data the detail tabs (table/graph/timeline/events)
        currently render. Falls back to an empty monitor when nothing is selected
        so all the existing single-target rendering code Just Works."""
        m = self._monitors.get(self._active) if self._active else None
        return m if m is not None else self._empty

    # --- DPI / HiDPI scaling ----------------------------------------------
    def _init_scaling(self) -> float:
        """Size Tk's fonts to the real display DPI and return a pixel-scale
        factor (1.0 at 96 dpi/100%, 1.5 at 150%, 2.0 at 200%) for the explicit
        pixel dimensions in the layout. Requires the process to already be
        DPI-aware (see :func:`_enable_windows_dpi_awareness`), otherwise Windows
        reports a flat 96 dpi and bitmap-stretches the window — the blur."""
        try:
            dpi = float(self.root.winfo_fpixels("1i"))  # pixels per inch
        except tk.TclError:
            dpi = 96.0
        if dpi <= 0:
            dpi = 96.0
        try:
            # Tk 'scaling' is points->pixels; 1 pt is 1/72 in, so dpi/72 makes
            # point-sized fonts render at their true physical size and crisp.
            self.root.tk.call("tk", "scaling", dpi / 72.0)
        except tk.TclError:
            pass
        return dpi / 96.0

    def s(self, px: float) -> int:
        """Scale a base-96-dpi pixel measurement to the current display."""
        return int(round(px * self.scale))

    def _set_window_icon(self) -> None:
        """Replace Tk's default feather with the themed 'signal ripples' mark
        (title bar, taskbar, Alt-Tab). Kept as an instance attribute so Tk does
        not garbage-collect it. Silently leaves the default if Tk/the platform
        can't set it (e.g. headless)."""
        try:
            self._app_icon = tk.PhotoImage(data=appicon.png_base64(64))
            self.root.iconphoto(True, self._app_icon)
        except Exception:
            pass

    # --- theming -----------------------------------------------------------
    def _apply_theme(self, name: str) -> None:
        self.theme = name
        COLORS.clear()
        COLORS.update(PALETTES[name])
        self._apply_ttk_style()
        self._recolor_widgets()
        self.theme_var.set(name)          # keep the View-menu radio in sync
        self._refresh_once()  # redraw canvases with the new palette

    def _apply_ttk_style(self) -> None:
        """Native (vista) for light; fully restyled 'clam' for dark, since the
        Windows native themes ignore background colours."""
        p = COLORS
        try:
            self.style.theme_use(p["ttk_theme"])
        except tk.TclError:
            self.style.theme_use("clam")

        # DPI-scaled geometry ttk won't derive on its own — needed in BOTH themes.
        # Tie the Treeview row height to the *actual* rendered font height so the
        # rows never clip the text, at any display scaling.
        line = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        self.style.configure("Treeview", rowheight=max(self.s(22), line + self.s(8)))
        self.style.configure("Treeview.Heading", padding=(self.s(6), self.s(5)))

        if self.style.theme_use() != "clam":
            return  # native light theme: leave widget colours system-default
        self.style.configure(".", background=p["win_bg"], foreground=p["fg"],
                             fieldbackground=p["field"], bordercolor=p["border"],
                             lightcolor=p["panel"], darkcolor=p["panel"], troughcolor=p["win_bg"])
        self.style.configure("TFrame", background=p["win_bg"])
        self.style.configure("TLabel", background=p["win_bg"], foreground=p["fg"])
        self.style.configure("TLabelframe", background=p["win_bg"], foreground=p["fg"])
        self.style.configure("TLabelframe.Label", background=p["win_bg"], foreground=p["fg"])
        self.style.configure("TButton", background=p["panel"], foreground=p["fg"], bordercolor=p["border"])
        self.style.map("TButton", background=[("active", p["active"]), ("disabled", p["win_bg"])],
                       foreground=[("disabled", p["hint"])])
        self.style.configure("TCheckbutton", background=p["win_bg"], foreground=p["fg"])
        self.style.map("TCheckbutton", background=[("active", p["win_bg"])])
        self.style.configure("TEntry", fieldbackground=p["field"], foreground=p["fg"],
                             insertcolor=p["fg"], bordercolor=p["border"])
        self.style.configure("TSpinbox", fieldbackground=p["field"], foreground=p["fg"],
                             background=p["panel"], bordercolor=p["border"], arrowcolor=p["fg"])
        self.style.configure("TCombobox", fieldbackground=p["field"], foreground=p["fg"],
                             background=p["panel"], bordercolor=p["border"], arrowcolor=p["fg"])
        self.style.map("TCombobox", fieldbackground=[("readonly", p["field"])],
                       foreground=[("readonly", p["fg"])], selectbackground=[("readonly", p["field"])])
        self.style.configure("Treeview", background=p["field"], foreground=p["fg"],
                             fieldbackground=p["field"], bordercolor=p["border"])
        self.style.map("Treeview", background=[("selected", p["sel_bg"])],
                       foreground=[("selected", p["sel_fg"])])
        self.style.configure("Treeview.Heading", background=p["panel"], foreground=p["fg"],
                             bordercolor=p["border"])
        self.style.map("Treeview.Heading", background=[("active", p["active"])])
        self.style.configure("TNotebook", background=p["win_bg"], bordercolor=p["border"])
        self.style.configure("TNotebook.Tab", background=p["panel"], foreground=p["fg"])
        self.style.map("TNotebook.Tab", background=[("selected", p["field"])],
                       foreground=[("selected", p["fg"])])
        self.style.configure("TPanedwindow", background=p["win_bg"])
        self.style.configure("TScrollbar", background=p["panel"], troughcolor=p["win_bg"],
                             bordercolor=p["border"], arrowcolor=p["fg"])
        self.style.map("TScrollbar", background=[("active", p["active"])])

        # Comfortable, DPI-scaled padding (clam uses none by default, so widgets
        # otherwise look cramped and uneven as the display scaling grows).
        self.style.configure("TButton", padding=(self.s(11), self.s(5)))
        self.style.configure("TEntry", padding=(self.s(3), self.s(3)))
        self.style.configure("TSpinbox", padding=(self.s(2), self.s(3)))
        self.style.configure("TCombobox", padding=(self.s(3), self.s(3)))
        self.style.configure("TCheckbutton", padding=(self.s(2), self.s(3)))
        self.style.configure("TNotebook.Tab", padding=(self.s(12), self.s(6)))

    def _recolor_widgets(self) -> None:
        """Recolour the non-ttk (classic tk) widgets and Treeview row tags, which
        don't follow ttk styles."""
        p = COLORS
        self.root.configure(bg=p["win_bg"])
        self.banner.configure(bg=p["banner_bg"])
        self.banner_label.configure(bg=p["banner_bg"], fg=p["banner_fg"])
        self.mos_label_w.configure(bg=p["status_bg"])
        self.canvas.configure(bg=p["graph_bg"])
        self.timeline_canvas.configure(bg=p["graph_bg"])
        self.map_canvas.configure(bg=p["graph_bg"])
        for t in ("ok", "warn", "bad", "dest"):
            self.tree.tag_configure(t, background=p[t], foreground=p["fg"])
            self.summary_tree.tag_configure(t, background=p[t], foreground=p["fg"])
        self.events_tree.tag_configure("alert", background=p["ev_alert"], foreground=p["fg"])
        self.events_tree.tag_configure("clear", background=p["ev_clear"], foreground=p["fg"])
        self.events_tree.tag_configure("route", background=p["ev_route"], foreground=p["fg"])

    # --- layout ------------------------------------------------------------
    def _build_controls(self) -> None:
        bar = ttk.Frame(self.root, padding=(self.s(8), self.s(8), self.s(8), self.s(3)))
        bar.pack(side="top", fill="x")
        gap, group = self.s(4), self.s(16)   # within-group vs between-group spacing

        ttk.Label(bar, text="Target:").pack(side="left", padx=(0, gap))
        self.target_var = tk.StringVar(value="8.8.8.8")
        self.target_entry = ttk.Entry(bar, textvariable=self.target_var, width=20)
        self.target_entry.pack(side="left", padx=(0, group))
        self.target_entry.bind("<Return>", lambda _e: self._start())
        self.target_entry.focus_set()

        ttk.Label(bar, text="Interval (s):").pack(side="left", padx=(0, gap))
        self.interval_var = tk.StringVar(value="2.5")
        ttk.Spinbox(bar, from_=0.5, to=60, increment=0.5, width=5,
                    textvariable=self.interval_var).pack(side="left", padx=(0, group))

        ttk.Button(bar, text="Engine…", command=self._open_engine_dialog).pack(side="left", padx=(0, group))

        self.start_btn = ttk.Button(bar, text="Add / Start", command=self._start)
        self.start_btn.pack(side="left", padx=(0, gap))
        self.stop_btn = ttk.Button(bar, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(0, gap))
        ttk.Button(bar, text="Remove", command=self._remove_target).pack(side="left")
        # File actions (Export / Save / Load) and the theme toggle live in the
        # menu bar — keeps the toolbar to the core controls so it never overflows.

    def _build_menubar(self) -> None:
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Export CSV…", command=self._export)
        file_menu.add_command(label="Save session…", command=self._save_session)
        file_menu.add_command(label="Load session…", command=self._load_session)
        file_menu.add_separator()
        file_menu.add_command(label="Compare with saved session…",
                              command=self._compare_with_baseline)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_radiobutton(label="Dark theme", variable=self.theme_var,
                                  value="dark", command=lambda: self._apply_theme("dark"))
        view_menu.add_radiobutton(label="Light theme", variable=self.theme_var,
                                  value="light", command=lambda: self._apply_theme("light"))
        view_menu.add_separator()
        view_menu.add_checkbutton(label="Resume targets on launch", variable=self.resume_var)
        menubar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About PingerPlot", command=self._about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def _about(self) -> None:
        messagebox.showinfo(
            "About PingerPlot",
            f"PingerPlot {__version__}\n\n"
            "A continuous-traceroute network path monitor for Windows (MTR-style).\n"
            "Pure Python standard library — no third-party dependencies.\n\n"
            "MIT licensed.",
        )

    def _build_alert_controls(self) -> None:
        bar = ttk.Frame(self.root, padding=(self.s(8), 0, self.s(8), self.s(5)))
        bar.pack(side="top", fill="x")
        gap, group = self.s(4), self.s(14)

        self.alerts_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Alerts", variable=self.alerts_var).pack(side="left", padx=(0, group))

        ttk.Label(bar, text="loss ≥").pack(side="left", padx=(0, gap))
        self.alert_loss_var = tk.StringVar(value="20")
        ttk.Spinbox(bar, from_=0, to=100, increment=5, width=4, textvariable=self.alert_loss_var).pack(side="left", padx=(0, gap))
        ttk.Label(bar, text="%").pack(side="left", padx=(0, group))

        ttk.Label(bar, text="latency ≥").pack(side="left", padx=(0, gap))
        self.alert_lat_var = tk.StringVar(value="250")
        ttk.Spinbox(bar, from_=0, to=5000, increment=25, width=6, textvariable=self.alert_lat_var).pack(side="left", padx=(0, gap))
        ttk.Label(bar, text="ms").pack(side="left", padx=(0, group))

        ttk.Label(bar, text="over").pack(side="left", padx=(0, gap))
        self.alert_win_var = tk.StringVar(value="20")
        ttk.Spinbox(bar, from_=2, to=300, increment=1, width=4, textvariable=self.alert_win_var).pack(side="left", padx=(0, gap))
        ttk.Label(bar, text="probes").pack(side="left", padx=(0, group))

        # MOS folds latency, jitter and loss into one score, so it catches the
        # combination that ruins a call while each part sits under its own
        # threshold. Lower is worse, hence "<=" rather than the ">=" above.
        ttk.Label(bar, text="MOS ≤").pack(side="left", padx=(0, gap))
        self.alert_mos_var = tk.StringVar(value="0")
        ttk.Spinbox(bar, from_=0, to=5, increment=0.1, format="%.1f", width=4,
                    textvariable=self.alert_mos_var).pack(side="left", padx=(0, group))

        self.alert_sound_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Sound", variable=self.alert_sound_var).pack(side="left", padx=(0, group))

        ttk.Label(bar, text="(0 disables a threshold; alerts use the destination hop)",
                  foreground="#8a9099").pack(side="left")

    def _build_banner(self) -> None:
        self.banner = tk.Frame(self.root, bg=COLORS["banner_bg"])
        self.banner_label = tk.Label(
            self.banner, bg=COLORS["banner_bg"], fg=COLORS["banner_fg"],
            anchor="w", font=("Segoe UI", 9, "bold"), padx=self.s(10), pady=self.s(5),
        )
        self.banner_label.pack(side="left", fill="x", expand=True)

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(self.s(8), self.s(3), self.s(8), self.s(6)))
        bar.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar(value="Idle — enter a target and press Start.")
        ttk.Label(bar, textvariable=self.status_var, anchor="w").pack(side="left", fill="x", expand=True)
        self.mos_var = tk.StringVar(value="")
        self.mos_label_w = tk.Label(bar, textvariable=self.mos_var, font=("Segoe UI", 9, "bold"))
        self.mos_label_w.pack(side="right")

    def _build_main(self) -> None:
        paned = ttk.Panedwindow(self.root, orient="horizontal")
        paned.pack(side="top", fill="both", expand=True)
        sidebar = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(sidebar, weight=1)
        paned.add(right, weight=4)
        self._build_summary(sidebar)
        self._build_notebook(right)

    def _build_summary(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Targets", padding=(self.s(6), self.s(5)),
                  font=("Segoe UI", 9, "bold")).pack(side="top", anchor="w")
        footer = ttk.Frame(parent)
        footer.pack(side="bottom", fill="x")
        ttk.Button(footer, text="Stop all", command=self._stop_all).pack(
            side="left", padx=self.s(5), pady=self.s(5))

        cols = ("target", "hops", "loss", "avg", "mos")
        self.summary_tree = ttk.Treeview(parent, columns=cols, show="headings", selectmode="browse")
        for key, title, width, anchor in (
            ("target", "Target", 150, "w"), ("hops", "Hops", 42, "e"),
            ("loss", "Loss", 48, "e"), ("avg", "Avg", 52, "e"), ("mos", "MOS", 42, "e"),
        ):
            self.summary_tree.heading(key, text=title)
            self.summary_tree.column(key, width=self.s(width), anchor=anchor, stretch=(key == "target"))
        for tag in ("ok", "warn", "bad", "dest"):
            self.summary_tree.tag_configure(tag, background=COLORS[tag])
        sb = ttk.Scrollbar(parent, orient="vertical", command=self.summary_tree.yview)
        self.summary_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.summary_tree.pack(side="left", fill="both", expand=True)
        self.summary_tree.bind("<<TreeviewSelect>>", self._on_summary_select)
        self.summary_tree.bind("<Button-3>", self._on_target_menu)

    def _build_notebook(self, parent: ttk.Frame) -> None:
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(side="top", fill="both", expand=True)

        # --- Overview tab: hop table over a single-hop graph ---------------
        overview = ttk.Frame(self.notebook)
        self.notebook.add(overview, text="Overview")
        paned = ttk.Panedwindow(overview, orient="vertical")
        paned.pack(fill="both", expand=True)
        table_frame = ttk.Frame(paned)
        graph_frame = ttk.Frame(paned)
        paned.add(table_frame, weight=3)
        paned.add(graph_frame, weight=2)
        self._build_table(table_frame)
        self._build_graph(graph_frame)

        # --- Timeline tab: all hops, shared axis ---------------------------
        timeline = ttk.Frame(self.notebook)
        self.notebook.add(timeline, text="Timeline")
        self.timeline_header = ttk.Label(timeline, text="No data yet.", padding=(8, 4))
        self.timeline_header.pack(side="top", fill="x")
        self.timeline_canvas = tk.Canvas(timeline, bg=COLORS["graph_bg"], highlightthickness=0)
        tl_vsb = ttk.Scrollbar(timeline, orient="vertical", command=self.timeline_canvas.yview)
        self.timeline_canvas.configure(yscrollcommand=tl_vsb.set)
        tl_vsb.pack(side="right", fill="y")
        self.timeline_canvas.pack(side="left", fill="both", expand=True)
        self.timeline_canvas.bind("<Configure>", lambda _e: self._draw_timeline())
        self.timeline_canvas.bind(
            "<MouseWheel>",
            lambda e: self.timeline_canvas.yview_scroll(int(-e.delta / 120), "units"),
        )

        # --- Events tab: route changes + alerts ----------------------------
        events = ttk.Frame(self.notebook)
        self.notebook.add(events, text="Events")
        cols = ("time", "type", "detail")
        self.events_tree = ttk.Treeview(events, columns=cols, show="headings")
        for key, title, width, stretch in (
            ("time", "Time", 90, False), ("type", "Type", 80, False), ("detail", "Detail", 600, True)
        ):
            self.events_tree.heading(key, text=title)
            self.events_tree.column(key, width=width, stretch=stretch, anchor="w")
        self.events_tree.tag_configure("alert", background=COLORS["ev_alert"])
        self.events_tree.tag_configure("clear", background=COLORS["ev_clear"])
        self.events_tree.tag_configure("route", background=COLORS["ev_route"])
        ev_vsb = ttk.Scrollbar(events, orient="vertical", command=self.events_tree.yview)
        self.events_tree.configure(yscrollcommand=ev_vsb.set)
        ev_vsb.pack(side="right", fill="y")
        self.events_tree.pack(side="left", fill="both", expand=True)

        # --- Map tab: geo-located hops on a world map ----------------------
        map_tab = ttk.Frame(self.notebook)
        self.notebook.add(map_tab, text="Map")
        self.map_header = ttk.Label(map_tab, text="Open this tab to geo-locate hops.", padding=(8, 4))
        self.map_header.pack(side="top", fill="x")
        self.map_canvas = tk.Canvas(map_tab, bg=COLORS["graph_bg"], highlightthickness=0)
        self.map_canvas.pack(side="top", fill="both", expand=True)
        self.map_canvas.bind("<Configure>", lambda _e: self._draw_map())

        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._draw_active_tab())

    def _build_table(self, parent: ttk.Frame) -> None:
        cols = [c[0] for c in COLUMNS]
        self.tree = ttk.Treeview(parent, columns=cols, show="headings", selectmode="browse")
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=self.s(width), anchor=anchor, stretch=(key == "hostname"))
        for tag in ("ok", "warn", "bad", "dest"):
            self.tree.tag_configure(tag, background=COLORS[tag])
        self.tree.bind("<ButtonRelease-1>", self._on_user_select)
        self.tree.bind("<KeyRelease-Up>", self._on_user_select)
        self.tree.bind("<KeyRelease-Down>", self._on_user_select)
        self.tree.bind("<Button-3>", self._on_hop_menu)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def _build_graph(self, parent: ttk.Frame) -> None:
        self.canvas = tk.Canvas(parent, background=COLORS["graph_bg"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._draw_graph())

    # --- engine options ----------------------------------------------------
    def _init_engine_vars(self) -> None:
        self.maxhops_var = tk.StringVar(value="30")
        self.resolve_var = tk.BooleanVar(value=True)
        self.timeout_var = tk.StringVar(value="1000")
        self.psize_var = tk.StringVar(value="32")
        self.senddelay_var = tk.StringVar(value="0")
        self.finalhop_var = tk.BooleanVar(value=False)
        self.packettype_var = tk.StringVar(value="ICMP")
        self.port_var = tk.StringVar(value="443")
        self.dscp_var = tk.StringVar(value="0")
        self.sourceip_var = tk.StringVar(value="")
        self.logpath_var = tk.StringVar(value="")
        self.webhook_var = tk.StringVar(value="")
        self._engine_win: Optional[tk.Toplevel] = None

    def _open_engine_dialog(self) -> None:
        if self._engine_win is not None and self._engine_win.winfo_exists():
            self._engine_win.lift()
            return
        win = tk.Toplevel(self.root)
        win.title("Engine options")
        win.transient(self.root)
        win.resizable(False, False)
        win.configure(bg=COLORS["win_bg"])
        self._engine_win = win
        frm = ttk.Frame(win, padding=self.s(12))
        frm.pack(fill="both", expand=True)

        ptype = ttk.Combobox(frm, width=7, textvariable=self.packettype_var, state="readonly",
                             values=["ICMP", "TCP", "UDP"])
        ptype.bind("<<ComboboxSelected>>", self._on_packettype_change)
        rows = [
            ("Packet type:", ptype),
            ("Port (TCP/UDP):", ttk.Spinbox(frm, from_=1, to=65535, increment=1, width=9, textvariable=self.port_var)),
            ("Max hops:", ttk.Spinbox(frm, from_=1, to=64, increment=1, width=9, textvariable=self.maxhops_var)),
            ("Reply timeout (ms):", ttk.Spinbox(frm, from_=100, to=10000, increment=100, width=9, textvariable=self.timeout_var)),
            ("Payload size (bytes):", ttk.Spinbox(frm, from_=0, to=1472, increment=8, width=9, textvariable=self.psize_var)),
            ("Send delay (ms):", ttk.Spinbox(frm, from_=0, to=1000, increment=5, width=9, textvariable=self.senddelay_var)),
            # DSCP marks the probe with the traffic class you actually care
            # about, so a QoS-marked path can be measured as itself instead of
            # as best-effort.
            ("DSCP (0-63):", ttk.Spinbox(frm, from_=0, to=63, increment=1, width=9, textvariable=self.dscp_var)),
            # Pins the outgoing interface on a multi-homed box, so you can ask
            # what a path looks like from a particular VLAN.
            ("Source IP (blank = auto):", ttk.Entry(frm, width=18, textvariable=self.sourceip_var)),
        ]
        for i, (label, widget) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", pady=3, padx=(0, 10))
            widget.grid(row=i, column=1, sticky="w")
        base = len(rows)
        ttk.Checkbutton(frm, text="Resolve hostnames (reverse DNS)", variable=self.resolve_var).grid(
            row=base, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Checkbutton(frm, text="Final hop only (ping destination, skip route mapping)", variable=self.finalhop_var).grid(
            row=base + 1, column=0, columnspan=2, sticky="w", pady=3)

        ttk.Label(frm, text="Log probes to CSV:").grid(row=base + 2, column=0, sticky="w", pady=3, padx=(0, 10))
        logrow = ttk.Frame(frm)
        logrow.grid(row=base + 2, column=1, sticky="w")
        ttk.Entry(logrow, textvariable=self.logpath_var, width=22).pack(side="left")
        ttk.Button(logrow, text="Browse…", command=self._browse_log).pack(side="left", padx=(4, 0))

        ttk.Label(frm, text="Webhook URL (alerts):").grid(row=base + 3, column=0, sticky="w", pady=3, padx=(0, 10))
        ttk.Entry(frm, textvariable=self.webhook_var, width=30).grid(row=base + 3, column=1, sticky="w")

        ttk.Label(frm, text="DSCP marks the probe (46 = EF/voice, 34 = AF41/video, 0 = best effort); ICMP mode marks "
                            "via the IP Helper API, but Windows silently ignores it on TCP/UDP sockets unless "
                            "DisableUserTOSSetting is cleared - confirm with a capture before trusting a TCP/UDP QoS "
                            "result. A source IP that this machine does not hold is rejected outright rather than "
                            "falling back. "
                            "TCP/UDP modes need Administrator (raw socket); ICMP does not. Send delay throttles the "
                            "probe rate. Logging is crash-safe (flushed every probe). A webhook URL (http/https) gets "
                            "a JSON POST when the destination alert raises or clears. TCP mode raises the reply timeout "
                            f"to at least {monitor.TCP_REFUSAL_FLOOR_MS} ms, because Windows takes about that long to "
                            "report a closed port and a shorter wait cannot tell one from an unreachable host. "
                            "Changes apply on next Start.",
                  foreground="#888", wraplength=self.s(380)).grid(
            row=base + 4, column=0, columnspan=2, sticky="w", pady=(8, 2))
        if not tcpudp.is_admin():
            ttk.Label(frm, text="⚠ Not running as Administrator — TCP/UDP will be refused.",
                      foreground=COLORS["fg_bad"], wraplength=self.s(380)).grid(
                row=base + 5, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Button(frm, text="Close", command=win.destroy).grid(row=base + 6, column=1, sticky="e", pady=(4, 0))

    def _on_packettype_change(self, _event: object = None) -> None:
        ptype = self.packettype_var.get().lower()
        if ptype in tcpudp.DEFAULT_PORTS:
            self.port_var.set(str(tcpudp.DEFAULT_PORTS[ptype]))

    def _browse_log(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Log probes to CSV", defaultextension=".csv",
            initialfile="pingerplot_log.csv", confirmoverwrite=False,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self.logpath_var.set(path)

    # --- actions -----------------------------------------------------------
    def _start(self) -> None:
        target = self.target_var.get().strip()
        if not target:
            messagebox.showwarning("No target", "Enter a hostname or IP address.")
            return
        try:
            interval = float(self.interval_var.get())
            max_hops = int(self.maxhops_var.get())
            timeout_ms = int(self.timeout_var.get())
            psize = int(self.psize_var.get())
            send_delay = int(self.senddelay_var.get())
            port = int(self.port_var.get())
            dscp = int(self.dscp_var.get())
            a_loss = float(self.alert_loss_var.get())
            a_lat = float(self.alert_lat_var.get())
            a_win = int(self.alert_win_var.get())
            a_mos = float(self.alert_mos_var.get())
        except ValueError:
            messagebox.showwarning("Invalid input", "Interval, engine and alert fields must be numbers.")
            return

        mon = self._monitors.get(target)
        if mon is None:
            mon = Monitor()
            self._monitors[target] = mon
        mon.start(
            target,
            interval=interval,
            timeout_ms=timeout_ms,
            max_hops=max_hops,
            resolve_names=self.resolve_var.get(),
            packet_size=psize,
            send_delay_ms=send_delay,
            final_hop_only=self.finalhop_var.get(),
            packet_type=self.packettype_var.get().lower(),
            port=port,
            dscp=dscp,
            source_ip=self.sourceip_var.get(),
            log_path=self.logpath_var.get(),
            alert_enabled=self.alerts_var.get(),
            alert_loss_pct=a_loss,
            alert_latency_ms=a_lat,
            alert_window=a_win,
            alert_sound=self.alert_sound_var.get(),
            alert_mos=a_mos,
            webhook_url=self.webhook_var.get(),
        )
        self._activate(target)

    def _activate(self, name: Optional[str]) -> None:
        """Make ``name`` the target shown in the detail tabs; reset detail state."""
        self._active = name
        self._selected_ttl = None
        self._last_ttl = None
        self._pinned = False
        self._last_event_seq = -1
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for iid in self.events_tree.get_children():
            self.events_tree.delete(iid)
        self._refresh_once()
        if name and self.summary_tree.exists(name) and self.summary_tree.selection() != (name,):
            self.summary_tree.selection_set(name)

    def _stop(self) -> None:
        mon = self._monitors.get(self._active) if self._active else None
        if mon is not None:
            mon.stop()

    def _stop_all(self) -> None:
        for mon in self._monitors.values():
            mon.stop()

    def _remove_target(self) -> None:
        self._remove_named(self._active)

    def _remove_named(self, name: Optional[str]) -> None:
        if not name or name not in self._monitors:
            return
        self._monitors.pop(name).shutdown()
        if self.summary_tree.exists(name):
            self.summary_tree.delete(name)
        if self._active == name:
            self._activate(next(iter(self._monitors), None))

    def _on_summary_select(self, _event: object) -> None:
        sel = self.summary_tree.selection()
        if sel and sel[0] != self._active:
            self._activate(sel[0])

    def _on_user_select(self, _event: object) -> None:
        sel = self.tree.selection()
        if sel:
            try:
                self._selected_ttl = int(sel[0])
                self._pinned = True
            except ValueError:
                pass
        self._draw_graph()

    # --- right-click menus (copy a hop; pause/stop a target) --------------
    def _on_hop_menu(self, event: object) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self._on_user_select(event)
        vals = self.tree.item(iid, "values")
        ip = str(vals[1]) if len(vals) > 1 else ""
        host = str(vals[2]) if len(vals) > 2 else ""
        menu = tk.Menu(self.tree, tearoff=0)
        if ip and ip != "*":
            menu.add_command(label=f"Copy IP   {ip}", command=lambda: self._clip(ip))
        if host and host not in ("", "(resolving…)"):
            menu.add_command(label=f"Copy hostname   {host}", command=lambda: self._clip(host))
        menu.add_command(label="Copy row", command=lambda: self._clip("\t".join(str(v) for v in vals)))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _clip(self, text: str) -> None:
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set(f"Copied: {text}")

    def _on_target_menu(self, event: object) -> None:
        iid = self.summary_tree.identify_row(event.y)
        if not iid or iid not in self._monitors:
            return
        self.summary_tree.selection_set(iid)
        self._activate(iid)
        mon = self._monitors[iid]
        menu = tk.Menu(self.summary_tree, tearoff=0)
        menu.add_command(label="Edit settings…", command=lambda: self._edit_target(iid))
        menu.add_separator()
        if mon.running and not mon.paused:
            menu.add_command(label="Pause", command=lambda: self._pause_target(iid))
        elif mon.running and mon.paused:
            menu.add_command(label="Resume", command=lambda: self._resume_target(iid))
        if mon.running:
            menu.add_command(label="Stop", command=mon.stop)
        menu.add_separator()
        menu.add_command(label="Remove", command=lambda: self._remove_named(iid))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _pause_target(self, name: str) -> None:
        mon = self._monitors.get(name)
        if mon is not None:
            mon.pause()
        self._refresh_once()

    def _resume_target(self, name: str) -> None:
        mon = self._monitors.get(name)
        if mon is not None:
            mon.resume()
        self._refresh_once()

    def _load_target_into_controls(self, name: str) -> None:
        """Load a target's *own* engine/alert settings back into the toolbar so
        you can see and re-apply them — settings are stored per-target (set when
        you Add / Start), not shared, so this is how you inspect/change one."""
        mon = self._monitors.get(name)
        if mon is None:
            return
        self.target_var.set(name)
        self.interval_var.set(f"{mon.interval:g}")
        self.maxhops_var.set(str(mon.max_hops))
        self.timeout_var.set(str(mon.timeout_ms))
        self.psize_var.set(str(mon.packet_size))
        self.senddelay_var.set(str(mon.send_delay_ms))
        self.port_var.set(str(mon.port))
        self.packettype_var.set(mon.packet_type.upper())
        self.dscp_var.set(str(mon.dscp))
        self.sourceip_var.set(mon.source_ip)
        self.logpath_var.set(mon.log_path)
        self.webhook_var.set(mon.webhook_url)
        self.resolve_var.set(mon.resolve_names)
        self.finalhop_var.set(mon.final_hop_only)
        self.alerts_var.set(mon.alert_enabled)
        self.alert_loss_var.set(f"{mon.alert_loss_pct:g}")
        self.alert_lat_var.set(f"{mon.alert_latency_ms:g}")
        self.alert_win_var.set(str(mon.alert_window))
        self.alert_mos_var.set(f"{mon.alert_mos:g}")
        self.alert_sound_var.set(mon.alert_sound)

    def _edit_target(self, name: str) -> None:
        """Inspect/change one target's settings: load them into the controls and
        open the engine dialog. Press Add / Start to re-apply (restarts it)."""
        self._load_target_into_controls(name)
        self._open_engine_dialog()

    def _on_close(self) -> None:
        self._save_settings()
        self.geo.stop()
        for mon in self._monitors.values():
            mon.shutdown()
        self._empty.shutdown()
        self.root.destroy()

    # --- settings persistence ---------------------------------------------
    def _apply_saved_settings(self) -> None:
        """Push the loaded settings into the toolbar/engine/alert variables so
        the user's last choices are in effect on launch."""
        s = self._settings
        for var, key in (
            (self.interval_var, "interval"), (self.target_var, "target"),
            (self.maxhops_var, "max_hops"), (self.timeout_var, "timeout_ms"),
            (self.psize_var, "packet_size"), (self.senddelay_var, "send_delay_ms"),
            (self.port_var, "port"), (self.logpath_var, "log_path"),
            (self.dscp_var, "dscp"), (self.sourceip_var, "source_ip"),
            (self.webhook_var, "webhook_url"), (self.packettype_var, "packet_type"),
            (self.alert_loss_var, "alert_loss"), (self.alert_lat_var, "alert_latency"),
            (self.alert_win_var, "alert_window"), (self.alert_mos_var, "alert_mos"),
        ):
            if s.get(key) is not None:
                var.set(str(s[key]))
        for var, key in (
            (self.resolve_var, "resolve_names"), (self.finalhop_var, "final_hop_only"),
            (self.alerts_var, "alerts_enabled"), (self.alert_sound_var, "alert_sound"),
            (self.resume_var, "resume_on_launch"),
        ):
            if isinstance(s.get(key), bool):
                var.set(s[key])

    def _save_settings(self) -> None:
        """Persist the current UI/engine/alert state and the target list."""
        settings.save({
            "theme": self.theme,
            "interval": self.interval_var.get(),
            "target": self.target_var.get(),
            "max_hops": self.maxhops_var.get(),
            "timeout_ms": self.timeout_var.get(),
            "packet_size": self.psize_var.get(),
            "send_delay_ms": self.senddelay_var.get(),
            "port": self.port_var.get(),
            "dscp": self.dscp_var.get(),
            "source_ip": self.sourceip_var.get(),
            "log_path": self.logpath_var.get(),
            "webhook_url": self.webhook_var.get(),
            "packet_type": self.packettype_var.get(),
            "resolve_names": bool(self.resolve_var.get()),
            "final_hop_only": bool(self.finalhop_var.get()),
            "alerts_enabled": bool(self.alerts_var.get()),
            "alert_loss": self.alert_loss_var.get(),
            "alert_latency": self.alert_lat_var.get(),
            "alert_window": self.alert_win_var.get(),
            "alert_mos": self.alert_mos_var.get(),
            "alert_sound": bool(self.alert_sound_var.get()),
            "resume_on_launch": bool(self.resume_var.get()),
            "targets": list(self._monitors.keys()),
        })

    def _restore_targets(self) -> None:
        """If enabled, re-add and start the targets from the last session."""
        if not self.resume_var.get():
            return
        entry = self.target_var.get()
        for name in self._settings.get("targets", []) or []:
            if isinstance(name, str) and name.strip():
                self.target_var.set(name)
                self._start()
        self.target_var.set(entry)   # leave the box showing the saved entry text

    def _export(self) -> None:
        views, _status, target_ip, target_input = self.monitor.snapshot()
        if not views:
            messagebox.showinfo("Nothing to export", "No hop data yet.")
            return
        path = filedialog.asksaveasfilename(
            title="Export hop statistics",
            defaultextension=".csv",
            initialfile=f"pingerplot_{target_input or 'route'}.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
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
        self.status_var.set(f"Exported {len(views)} hops to {path}")

    def _save_session(self) -> None:
        data = self.monitor.to_dict()
        if not data.get("hops"):
            messagebox.showinfo("Nothing to save", "No hop data yet.")
            return
        path = filedialog.asksaveasfilename(
            title="Save session",
            defaultextension=".json",
            initialfile=f"pingerplot_{data.get('target_input') or 'session'}.json",
            filetypes=[("PingerPlot session", "*.json"), ("All files", "*.*")],
        )
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
        self.status_var.set(f"Saved session ({len(data['hops'])} hops) to {path}")

    def _load_session(self) -> None:
        path = filedialog.askopenfilename(
            title="Load session",
            filetypes=[("PingerPlot session", "*.json"), ("All files", "*.*")],
        )
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
        name = (data.get("target_input") or "loaded session") + " (loaded)"
        mon = self._monitors.get(name)
        if mon is None:
            mon = Monitor()
            self._monitors[name] = mon
        mon.load_dict(data)
        self._activate(name)

    # --- baseline comparison ----------------------------------------------
    def _compare_with_baseline(self) -> None:
        """Diff the active target against a session saved earlier.

        The tool could always say what a path looks like now; this is what says
        whether that differs from last Tuesday, which is the question people
        actually turn up with.
        """
        views, _status, _ip, target_input = self.monitor.snapshot()
        if not views:
            messagebox.showinfo("Nothing to compare",
                                "Start a target first - there is no current data.")
            return
        path = filedialog.askopenfilename(
            title="Choose a saved session to compare against",
            filetypes=[("PingerPlot session", "*.json"), ("All files", "*.*")],
        )
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
            messagebox.showerror("Compare failed",
                                 "That session has no hop data to compare against.")
            return
        base_target = str(data.get("target_input") or os.path.basename(path))
        if base_target and target_input and base_target != target_input:
            # Comparing two different destinations is almost always a mis-click,
            # and the per-hop numbers would be meaningless. Warn, but allow it:
            # the same host is legitimately reachable under two names.
            if not messagebox.askyesno(
                    "Different target",
                    f"The saved session is for '{base_target}' but the current "
                    f"target is '{target_input}'.\n\nCompare anyway?"):
                return

        now = [cmpmod.HopStats(v.ttl, v.address, v.hostname, v.sent,
                               v.loss_pct, v.avg, v.jitter) for v in views]
        self._show_comparison(cmpmod.compare(base, now, target=target_input),
                              base_target)

    def _show_comparison(self, cmp_, baseline_label: str) -> None:
        win = tk.Toplevel(self.root)
        win.title(f"Compare - now vs {baseline_label}")
        win.transient(self.root)
        win.geometry(f"{self.s(900)}x{self.s(460)}")
        win.configure(bg=COLORS["win_bg"])

        ttk.Label(win, text=cmp_.summary(), padding=(self.s(10), self.s(8)),
                  font=("Segoe UI", 9, "bold")).pack(side="top", anchor="w")

        cols = ("ttl", "address", "loss", "d_loss", "avg", "d_avg", "verdict")
        tree = ttk.Treeview(win, columns=cols, show="headings", selectmode="browse")
        for key, title, width, anchor in (
            ("ttl", "Hop", 44, "center"), ("address", "Address", 150, "w"),
            ("loss", "Loss", 62, "e"), ("d_loss", "Δ Loss", 74, "e"),
            ("avg", "Avg ms", 70, "e"), ("d_avg", "Δ Avg", 74, "e"),
            ("verdict", "Change", 260, "w"),
        ):
            tree.heading(key, text=title)
            tree.column(key, width=self.s(width), anchor=anchor,
                        stretch=(key == "verdict"))
        for tag, key in (("bad", "bad"), ("warn", "warn"),
                         ("ok", "ok"), ("dest", "dest")):
            tree.tag_configure(tag, background=COLORS[key], foreground=COLORS["fg"])

        for r in cmp_.rows:
            side = r.now or r.base
            note = r.verdict
            if r.verdict == cmpmod.REROUTED and r.base and r.now:
                note = f"rerouted  {r.base.address} → {r.now.address}"
            tree.insert("", "end", values=(
                r.ttl,
                r.address or "*",
                "—" if side is None else f"{side.loss_pct:.0f}%",
                "" if r.d_loss is None else f"{r.d_loss:+.0f}%",
                "—" if side is None or side.avg is None else _ms(side.avg),
                "" if r.d_avg is None else f"{r.d_avg:+.1f}",
                note,
            ), tags=(self._verdict_tag(r.verdict),))

        vsb = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        footer = ttk.Frame(win, padding=(self.s(8), self.s(6)))
        footer.pack(side="bottom", fill="x")
        ttk.Button(footer, text="Copy",
                   command=lambda: self._clip("\n".join(
                       cmpmod.format_comparison(cmp_, baseline_label)))).pack(side="left")
        ttk.Button(footer, text="Close", command=win.destroy).pack(side="right")
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

    # --- refresh loop ------------------------------------------------------
    def _refresh(self) -> None:
        self._refresh_once()
        self.root.after(REFRESH_MS, self._refresh)

    def _refresh_once(self) -> None:
        self._update_summary()
        views, status, _target_ip, _target_input = self.monitor.snapshot()
        if self._active:
            self.status_var.set(status)
        else:
            self.status_var.set("Idle — enter a target and press Add / Start.")
        active = self._monitors.get(self._active) if self._active else None
        self.stop_btn.config(state=("normal" if active and active.running else "disabled"))
        self._update_table(views)
        self._update_events()
        self._update_banner()
        self._update_mos(views)
        # Redraw the active canvas only when its data actually changed since the
        # last tick (probes arrive every `interval`, but we tick faster). Resize
        # and tab-change redraw via their own bindings, so this can't blank them.
        ver = draw_version(self._active, active, self._selected_ttl, self.theme,
                           self.geo.count())
        if ver != self._draw_ver:
            self._draw_ver = ver
            self._draw_active_tab()

    def _update_summary(self) -> None:
        for iid in self.summary_tree.get_children():
            if iid not in self._monitors:
                self.summary_tree.delete(iid)
        for name, mon in self._monitors.items():
            n_hops, d_loss, d_avg, d_jitter, reached = mon.summary()
            mscore = mos(d_avg, d_jitter, d_loss) if (n_hops and reached) else None
            label = name + ("  ⏸" if (mon.running and mon.paused) else ("  ▶" if mon.running else ""))
            values = (
                label,
                n_hops,
                f"{d_loss:.0f}%" if d_loss is not None else "—",
                _ms(d_avg) if n_hops else "—",
                f"{mscore:.1f}" if mscore is not None else "—",
            )
            tag = self._summary_tag(d_loss, d_avg, mon, name)
            if self.summary_tree.exists(name):
                self.summary_tree.item(name, values=values, tags=(tag,))
            else:
                self.summary_tree.insert("", "end", iid=name, values=values, tags=(tag,))

    def _summary_tag(self, loss: Optional[float], avg: Optional[float], mon: Monitor, name: str) -> str:
        if mon.active_alerts() or (loss is not None and loss > 25):
            return "bad"
        if loss is not None and (loss > 0 or (avg is not None and avg >= BAD_MS)):
            return "warn"
        return "dest" if name == self._active else "ok"

    def _update_mos(self, views: List[HopView]) -> None:
        if not views or not self.monitor.reached_target:
            self.mos_var.set("")
            return
        dest = views[-1]
        m = mos(dest.avg, dest.jitter, dest.loss_pct)
        if m is None:
            self.mos_var.set("")
            return
        self.mos_var.set(f"Dest MOS {m:.1f} · {mos_label(m)}")
        self.mos_label_w.config(fg=(COLORS["mos_good"] if m >= 4.0 else COLORS["mos_mid"] if m >= 3.1 else COLORS["mos_bad"]))

    def _draw_active_tab(self) -> None:
        try:
            current = self.notebook.index(self.notebook.select())
        except tk.TclError:
            return
        if current == 0:
            self._draw_graph()
        elif current == 1:
            self._draw_timeline()
        elif current == 3:
            self._draw_map()

    def _draw_map(self) -> None:
        if not self._geo_started:      # opt-in: only talk to ipwho.is once the
            self.geo.start()           # Map tab is actually opened
            self._geo_started = True
        c = self.map_canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 80 or h < 80:
            return
        p = COLORS

        def proj(lon, lat):
            return ((lon + 180.0) / 360.0 * w, (90.0 - lat) / 180.0 * h)

        for poly in worldmap.COASTLINES:
            pts = []
            for lon, lat in poly:
                x, y = proj(lon, lat)
                pts.extend((x, y))
            if len(pts) >= 4:
                c.create_line(pts, fill=p["grid"], width=1)

        views, _status, _ip, _in = self.monitor.snapshot()
        public = 0
        located = []
        for v in views:
            if v.address and geoip._is_public(v.address):
                public += 1
                self.geo.request(v.address)
                g = self.geo.get(v.address)
                if g:
                    x, y = proj(g.lon, g.lat)
                    located.append((v, g, x, y))

        for i in range(1, len(located)):
            c.create_line(located[i - 1][2], located[i - 1][3], located[i][2], located[i][3],
                          fill=p["line"], width=max(1, self.s(2)))
        last_ttl = views[-1].ttl if views else None
        rad = max(2, self.s(4))
        for v, g, x, y in located:
            c.create_oval(x - rad, y - rad, x + rad, y + rad,
                          fill=self._hop_fg(v, v.ttl == last_ttl), outline=p["graph_bg"])
            label = f"{v.ttl}. {g.city or g.country}".strip()
            c.create_text(x + rad + self.s(2), y, anchor="w", text=label,
                          fill=p["title"], font=("Segoe UI", 8))

        if not views:
            self.map_header.config(text="No data yet — start a target to map its hops.")
        else:
            self.map_header.config(
                text=f"Geo-located {len(located)} of {public} public hops via ipwho.is  "
                     f"(private/LAN hops have no location)")

    def _update_table(self, views: List[HopView]) -> None:
        desired = {str(v.ttl) for v in views}
        for iid in self.tree.get_children():
            if iid not in desired:
                self.tree.delete(iid)

        last_ttl = views[-1].ttl if views else None
        for v in views:
            iid = str(v.ttl)
            hostname = v.hostname if v.hostname else ("(resolving…)" if v.address and v.hostname is None else "")
            values = (
                v.ttl, v.address or "*", hostname, f"{v.loss_pct:.0f}%", v.sent,
                _ms(v.current), _ms(v.avg), _ms(v.best), _ms(v.worst), _ms(v.jitter),
            )
            tag = self._row_tag(v, is_dest=(v.ttl == last_ttl))
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
            tag = e.kind if e.kind in ("alert", "clear", "route") else "info"
            self.events_tree.insert("", "end", values=(ts, e.kind.upper(), e.text), tags=(tag,))
        if new:
            self.events_tree.yview_moveto(1.0)
        self._last_event_seq = last

    def _update_banner(self) -> None:
        alerts = []
        for name, mon in self._monitors.items():
            prefix = "" if len(self._monitors) == 1 else f"[{name}] "
            alerts.extend(prefix + a for a in mon.active_alerts())
        if alerts:
            self.banner_label.config(text="⚠  " + "      ".join(alerts))
            if not self._banner_shown:
                self.banner.pack(side="top", fill="x", before=self.notebook)
                self._banner_shown = True
        elif self._banner_shown:
            self.banner.pack_forget()
            self._banner_shown = False

    @staticmethod
    def _row_tag(v: HopView, is_dest: bool) -> str:
        """Row colour: red for a problem, amber for a warning.

        BAD_MS used to be tested one branch too late — `avg >= BAD_MS` returned
        "warn", and any average clearing 250 ms also clears WARN_MS's 120 ms and
        would have hit the next branch for the same answer. The condition could
        never change the outcome, so latency alone never turned a row red no
        matter how bad it got, contradicting BAD_MS's own comment.
        """
        if v.loss_pct > 25 or (v.avg is not None and v.avg >= BAD_MS):
            return "bad"
        if v.loss_pct > 0 or (v.avg is not None and v.avg >= WARN_MS):
            return "warn"
        return "dest" if is_dest else "ok"

    def _hop_fg(self, v: HopView, is_dest: bool) -> str:
        return {
            "bad": COLORS["fg_bad"], "warn": COLORS["fg_warn"],
            "dest": COLORS["fg_dest"], "ok": COLORS["fg_ok"],
        }[self._row_tag(v, is_dest)]

    # --- plotting ----------------------------------------------------------
    def _plot_series(self, c, samples, x0, x1, y_base, y_top, y_max, t_min, span, tick=8):
        """Draw one latency series (filled line + red loss ticks) inside a rect.
        ``y_base`` is the bottom (value 0), ``y_top`` the top (value y_max)."""
        if not samples:
            return

        def X(t):
            return x0 + (x1 - x0) * ((t - t_min) / span) if span else (x0 + x1) / 2

        def Y(v):
            return y_base + (y_top - y_base) * (min(v, y_max) / y_max)

        segment: List[float] = []
        loss_x: List[float] = []
        for s in samples:
            if s.rtt is None:
                loss_x.append(X(s.t))
                self._flush_segment(c, segment, y_base)
                segment = []
            else:
                segment.extend((X(s.t), Y(s.rtt)))
        self._flush_segment(c, segment, y_base)

        tick_h = min(tick, max(2, y_base - y_top))
        for x in loss_x:
            c.create_line(x, y_base, x, y_base - tick_h, fill=COLORS["loss"], width=1)

    def _flush_segment(self, c, segment: List[float], baseline: float) -> None:
        if len(segment) >= 4:  # at least two points
            poly = [segment[0], baseline] + segment + [segment[-2], baseline]
            c.create_polygon(poly, fill=COLORS["fill"], outline="")
            c.create_line(segment, fill=COLORS["line"], width=max(1, self.s(2)), joinstyle="round")
        elif len(segment) == 2:
            r = max(1, self.s(2))
            c.create_oval(segment[0] - r, segment[1] - r, segment[0] + r, segment[1] + r,
                          fill=COLORS["line"], outline="")

    def _draw_graph(self) -> None:
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 40 or h < 40:
            return
        pad_l, pad_r, pad_t, pad_b = self.s(52), self.s(14), self.s(26), self.s(26)
        x0, x1 = pad_l, w - pad_r
        y_base, y_top = h - pad_b, pad_t
        c.create_rectangle(x0, y_top, x1, y_base, fill=COLORS["graph_bg"], outline=COLORS["graph_border"])

        ttl = self._selected_ttl
        samples: List[Sample] = self.monitor.samples_for(ttl) if ttl is not None else []
        rtts = [s.rtt for s in samples if s.rtt is not None]
        y_max = max(10.0, (max(rtts) if rtts else 50.0) * 1.15)

        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = y_base + (y_top - y_base) * frac
            c.create_line(x0, y, x1, y, fill=COLORS["grid"])
            c.create_text(x0 - self.s(6), y, text=f"{y_max * frac:.0f}", anchor="e",
                          fill=COLORS["axis_text"], font=("Segoe UI", 8))

        title = "Select a hop to graph" if ttl is None else f"Hop {ttl} — latency (ms)"
        c.create_text(x0, y_top - self.s(13), text=title, anchor="w", fill=COLORS["title"], font=("Segoe UI", 9, "bold"))
        if not samples:
            return

        t0, t1 = samples[0].t, samples[-1].t
        span = max(1e-6, t1 - t0)
        self._plot_series(c, samples, x0, x1, y_base, y_top, y_max, t0, span, tick=self.s(10))

        last = samples[-1]
        if last.rtt is not None:
            lx = x0 + (x1 - x0) * ((last.t - t0) / span)
            ly = y_base + (y_top - y_base) * (min(last.rtt, y_max) / y_max)
            r = self.s(3)
            c.create_oval(lx - r, ly - r, lx + r, ly + r, fill=COLORS["line"], outline="")
            c.create_text(lx - self.s(6), ly - self.s(8), text=_ms(last.rtt), anchor="se",
                          fill=COLORS["line"], font=("Segoe UI", 8, "bold"))
        c.create_text(x1, y_base + self.s(13), text=f"← {span:.0f}s window  ·  now →",
                      anchor="e", fill=COLORS["axis_text"], font=("Segoe UI", 8))

    def _draw_timeline(self) -> None:
        c = self.timeline_canvas
        c.delete("all")
        w = c.winfo_width()
        if w < 60:
            return
        data: Dict[int, List[Sample]] = self.monitor.all_samples()
        views, _status, _ip, _in = self.monitor.snapshot()
        if not views:
            self.timeline_header.config(text="No data yet.")
            c.configure(scrollregion=(0, 0, w, 1))
            return

        all_s = [s for lst in data.values() for s in lst]
        rtts = [s.rtt for s in all_s if s.rtt is not None]
        g_max = max(10.0, (max(rtts) if rtts else 50.0) * 1.1)
        times = [s.t for s in all_s]
        t_min = min(times) if times else 0.0
        span = max(1e-6, (max(times) if times else 1.0) - t_min)

        n = len(views)
        strip_h, gutter, pad = self.s(44), self.s(184), self.s(6)
        c.configure(scrollregion=(0, 0, w, n * strip_h))
        last_ttl = views[-1].ttl

        for i, v in enumerate(views):
            y0 = i * strip_h
            y_top, y_base = y0 + pad, y0 + strip_h - pad
            gx0, gx1 = gutter, w - self.s(12)
            c.create_line(0, y0, w, y0, fill=COLORS["strip_sep"])
            c.create_rectangle(gx0, y_top, gx1, y_base, fill=COLORS["graph_bg"], outline=COLORS["strip_border"])

            label = f"{v.ttl}.  {v.hostname or v.address or '*'}"
            c.create_text(self.s(8), (y_top + y_base) / 2, anchor="w", text=label[:34],
                          fill=self._hop_fg(v, v.ttl == last_ttl), font=("Segoe UI", 8))
            c.create_text(gutter - self.s(8), (y_top + y_base) / 2, anchor="e",
                          text=f"{v.loss_pct:.0f}% loss", fill="#999", font=("Segoe UI", 7))

            self._plot_series(c, data.get(v.ttl, []), gx0, gx1, y_base, y_top, g_max, t_min, span, tick=self.s(6))
            c.create_text(gx1 - self.s(2), y_top + self.s(1), anchor="ne", text=_ms(v.current),
                          fill=COLORS["line"], font=("Segoe UI", 7, "bold"))

        self.timeline_header.config(
            text=f"All hops, shared scale 0–{g_max:.0f} ms  ·  {span:.0f}s window  ·  {n} hops"
        )


def _enable_windows_dpi_awareness() -> None:
    """Tell Windows we'll handle DPI ourselves, so it renders the window at
    native resolution instead of bitmap-stretching a 96-dpi surface (the blur).
    Must be called before the first Tk window is created."""
    if sys.platform != "win32":
        return
    try:  # Windows 8.1+: PROCESS_SYSTEM_DPI_AWARE = 1 (crisp on the main monitor)
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except (AttributeError, OSError):
        pass
    try:  # Vista/7/8 fallback
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


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
    _enable_windows_dpi_awareness()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
