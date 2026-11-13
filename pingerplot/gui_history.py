"""The History page: what a target did over a span of time, from the history.

Pick a target and a range (the last hour to the last year, stepped back and
forth), and the page shows:

* a chart of the destination's latency and loss (or any hop's, picked from
  the Hops table), with the route in use drawn as a coloured band above it
  and reroutes and alerts marked; hovering reads out the moment under the
  pointer;
* Routes: every route taken, how long, and how the destination did on each,
  so the bad path stands out;
* Hops: every router seen at each hop and how it did, so "which router" has
  an answer;
* Events: alerts, reroutes, DNS moves and warnings, filtered and searched.

Queries run on a worker thread (a year of minutes is a lot of rows) and the
results come back through the app's UI queue. Everything can be exported.
"""
from __future__ import annotations

import csv
import math
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from . import history, investigate
from .gui_charts import fmt_ms
from .gui_widgets import Nav
from .model import csv_safe

RANGES = (("Last hour", 3600), ("Last 6 hours", 6 * 3600), ("Last 24 hours", 86400),
          ("Last 7 days", 7 * 86400), ("Last 30 days", 30 * 86400), ("Last year", 365 * 86400))
DEFAULT_RANGE = "Last 24 hours"

# Routes are identities, so a categorical palette in a fixed order: the
# reference order without its green and red (PingerPlot's latency line and
# loss). Checked with the dataviz validator against the app's own surfaces
# (#ffffff, #2b2b2b): adjacent pairs pass in both modes, the first three pass
# all-pairs. Three light-mode colours are under 3:1 on white, which is why the
# band carries route numbers and the Routes table repeats them. A seventh
# route and beyond are grey, numbered.
ROUTE_COLOURS = {
    False: ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#6250d6"),
    True: ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9"),
}
OTHER_ROUTE = {False: "#9a9a9a", True: "#6e6e6e"}
EVENT_KINDS = (("alert", "Alerts"), ("route", "Reroutes and moves"), ("warn", "Warnings"),
               ("info", "Notes"), ("clear", "Cleared"))
MAX_EVENT_ROWS = 2000
LIVE_REFRESH_MS = 30_000


def _when(t: float, span: float) -> str:
    fmt = "%H:%M" if span <= 86400 else "%a %d %b %H:%M"
    return time.strftime(fmt, time.localtime(t))


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"
    return f"{seconds // 86400} d {seconds % 86400 // 3600} h"


def nice_max(v: float) -> float:
    """The axis top: ``v`` rounded up to a round step (1, 1.5, 2, 3, 4, 5, 6
    or 8 times a power of ten), so the gridlines read 0 / 40 / 80 rather
    than 0 / 31 / 62."""
    if v <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * exp >= v - 1e-9:
            return m * exp
    return 10 * exp


def route_colour(index: int, dark: bool) -> str:
    """Colour for the ``index``-th route ever recorded to a target (stable,
    so a route keeps its colour whatever range is shown)."""
    palette = ROUTE_COLOURS[dark]
    return palette[index] if 0 <= index < len(palette) else OTHER_ROUTE[dark]


def draw_chart(c, ink, data: dict, dark: bool, hop: Optional[int]) -> None:
    """The latency/loss chart with the route band. ``data`` is what
    HistoryPage._load produced."""
    S = ink.s
    c.delete("all")
    w, h = c.winfo_width(), c.winfo_height()
    if w < 80 or h < 80:
        return
    since, until = data["since"], data["until"]
    span = max(1.0, until - since)
    pad_l, pad_r, pad_t, pad_b = S(52), S(16), S(46), S(24)
    band_top, band_h = S(22), S(16)
    x0, x1 = pad_l, w - pad_r
    y_base, y_top = h - pad_b, pad_t

    def X(t):
        return x0 + (x1 - x0) * ((t - since) / span)

    points = data["series"]
    avgs = [p.avg for p in points if p.avg is not None]
    y_max = nice_max(max(10.0, (max(avgs) if avgs else 50.0) * 1.1))

    def Y(v):
        return y_base + (y_top - y_base) * (min(v, y_max) / y_max)

    for frac in (0.0, 0.5, 1.0):
        y = y_base + (y_top - y_base) * frac
        c.create_line(x0, y, x1, y, fill=ink.col["grid"])
        c.create_text(x0 - S(8), y, text=f"{y_max * frac:g}", anchor="e",
                      fill=ink.col["axis_text"], font=ink.font(-2))
    what = "Destination" if hop is None else f"Hop {hop}"
    c.create_text(x0, S(10), text=f"{what} · latency (ms) and loss", anchor="w",
                  fill=ink.col["title"], font=ink.font(-1, "semibold"))
    for i in range(5):
        t = since + span * i / 4
        c.create_text(X(t), y_base + S(12), text=_when(t, span),
                      anchor="w" if i == 0 else ("e" if i == 4 else "center"),
                      fill=ink.col["axis_text"], font=ink.font(-2))

    # The route band: which route was in use, numbered for colour-blind
    # readers and for the three light colours that are faint on white.
    c.create_rectangle(x0, band_top, x1, band_top + band_h, fill=ink.col["grid"], outline="")
    for route in data["routes"]:
        colour = route_colour(data["route_index"].get(route.id, -1), dark)
        for a, b in route.spans:
            xa, xb = X(a), X(b)
            if xb - xa < 1:
                xb = xa + 1
            # A 2 px gap of surface between neighbouring segments.
            c.create_rectangle(xa + 1, band_top, xb - 1, band_top + band_h, fill=colour, outline="")
            if xb - xa > S(18):
                c.create_text((xa + xb) / 2, band_top + band_h / 2, text=str(route.id),
                              fill="#ffffff", font=ink.font(-2, "semibold"))

    # Loss: bars up from the baseline, height = share of probes lost.
    bucket_w = max(1.0, (x1 - x0) / max(1, len(points)))
    for p in points:
        if p.sent and p.recv < p.sent:
            xb = X(p.t)
            top = y_base - (y_base - y_top) * 0.35 * (p.loss_pct / 100.0)
            c.create_rectangle(xb, top, xb + max(1.0, bucket_w - 1), y_base,
                               fill=ink.col["loss"], outline="")
    # Latency: a line through each bucket's average, broken where a bucket
    # had no replies at all.
    seg: List[float] = []
    for p in points:
        if p.avg is None:
            if len(seg) >= 4:
                c.create_line(seg, fill=ink.col["line"], width=max(1, S(2)), joinstyle="round")
            seg = []
            continue
        seg.extend((X(p.t + 30), Y(p.avg)))
    if len(seg) >= 4:
        c.create_line(seg, fill=ink.col["line"], width=max(1, S(2)), joinstyle="round")
    elif len(seg) == 2:
        r = max(2, S(3))
        c.create_oval(seg[0] - r, seg[1] - r, seg[0] + r, seg[1] + r, fill=ink.col["line"], outline="")

    # Reroutes and alerts, marked where they happened.
    for e in data["events"]:
        if e["kind"] not in ("route", "alert") or not since <= e["t"] <= until:
            continue
        x = X(e["t"])
        colour = ink.col["loss"] if e["kind"] == "alert" else ink.col["axis_text"]
        c.create_line(x, band_top + band_h + S(2), x, y_base, fill=colour, dash=(2, 3))
    if not points:
        c.create_text((x0 + x1) / 2, (y_top + y_base) / 2, fill=ink.col["axis_text"],
                      font=ink.font(0), text="Nothing recorded for this target in this range.")


class HistoryPage(ttk.Frame):
    def __init__(self, parent, app) -> None:
        super().__init__(parent, padding=(app.s(24), app.s(10), app.s(24), app.s(14)))
        self.app = app
        S = app.s
        self.target_var = tk.StringVar(master=self)
        self.range_var = tk.StringVar(master=self, value=DEFAULT_RANGE)
        self.search_var = tk.StringVar(master=self)
        self.kind_vars = {k: tk.BooleanVar(master=self, value=True) for k, _ in EVENT_KINDS}
        self.until: Optional[float] = None       # None: up to now, refreshed while shown
        self.hop: Optional[int] = None           # None: the destination
        self.data: Optional[dict] = None
        self._load_seq = 0
        self._refresh_job = None
        self._swatches: Dict[str, tk.PhotoImage] = {}

        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x")
        ttk.Label(bar, text="Target").pack(side="left")
        self.target_box = ttk.Combobox(bar, textvariable=self.target_var, state="readonly", width=30)
        self.target_box.pack(side="left", padx=(S(6), S(16)))
        self.target_box.bind("<<ComboboxSelected>>", lambda _e: self._pick_target())
        ttk.Combobox(bar, textvariable=self.range_var, values=[r for r, _ in RANGES],
                     state="readonly", width=14).pack(side="left")
        self.range_var.trace_add("write", lambda *_a: self._set_until(None))
        ttk.Button(bar, text="◀ Earlier", command=lambda: self._step(-1)).pack(side="left", padx=(S(8), 0))
        self.later_btn = ttk.Button(bar, text="Later ▶", command=lambda: self._step(1))
        self.later_btn.pack(side="left", padx=(S(4), 0))
        ttk.Button(bar, text="Now", command=lambda: self._set_until(None)).pack(side="left", padx=(S(4), 0))
        ttk.Button(bar, text="Export…", command=self._export_menu).pack(side="right")

        self.summary = ttk.Label(self, text="", style="Strong.TLabel")
        self.summary.pack(side="top", anchor="w", pady=(S(10), 0))
        self.detail = ttk.Label(self, text="", style="Muted.TLabel")
        self.detail.pack(side="top", anchor="w")

        card = ttk.Frame(self, style="Tile.TFrame", padding=(S(6), S(6)))
        card.pack(side="top", fill="x", pady=(S(8), 0))
        self.canvas = tk.Canvas(card, height=S(240), highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._draw())
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", lambda _e: self.canvas.delete("hover"))

        nav_row = ttk.Frame(self)
        nav_row.pack(side="top", fill="x", pady=(S(10), 0))
        self.tabs: Dict[str, ttk.Frame] = {}
        self.nav = Nav(nav_row, app.look, [("routes", "Routes"), ("hops", "Hops"), ("events", "Events")],
                       self._show_tab)
        self.nav.pack(side="left")
        self.chart_note = ttk.Label(nav_row, text="", style="Muted.TLabel")
        self.chart_note.pack(side="right")
        host = ttk.Frame(self)
        host.pack(side="top", fill="both", expand=True, pady=(S(6), 0))
        self.tabs["routes"] = self._build_routes(host)
        self.tabs["hops"] = self._build_hops(host)
        self.tabs["events"] = self._build_events(host)
        self.nav.select("routes")
        self._show_tab("routes")
        self.recolor()

    # --- building -------------------------------------------------------------
    def _table(self, host, cols, tree_col=None):
        S = self.app.s
        card = ttk.Frame(host, style="Tile.TFrame", padding=(S(6), S(6)))
        tree = ttk.Treeview(card, columns=[c[0] for c in cols],
                            show="tree headings" if tree_col else "headings", selectmode="browse")
        if tree_col:
            title, width = tree_col
            tree.heading("#0", text=title, anchor="w")
            tree.column("#0", width=S(width), stretch=False, anchor="w")
        for key, title, width, stretch, anchor in cols:
            tree.heading(key, text=title, anchor=anchor)
            tree.column(key, width=S(width), stretch=stretch, anchor=anchor)
        vsb = ttk.Scrollbar(card, orient="vertical", command=tree.yview, style="Card.Vertical.TScrollbar")
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        return card, tree

    def _build_routes(self, host):
        card, self.routes_tree = self._table(host, (
            ("time", "Time on it", 110, False, "e"), ("share", "Share", 60, False, "e"),
            ("avg", "Destination avg", 110, False, "e"), ("loss", "Destination loss", 110, False, "e"),
            ("path", "Path (hop by hop)", 520, True, "w")), tree_col=("Route", 80))
        return card

    def _build_hops(self, host):
        card, self.hops_tree = self._table(host, (
            ("router", "Router", 200, True, "w"), ("share", "Answered", 80, False, "e"),
            ("loss", "Loss", 60, False, "e"), ("avg", "Avg", 60, False, "e"),
            ("min", "Best", 60, False, "e"), ("max", "Worst", 60, False, "e"),
            ("jitter", "Jitter", 60, False, "e")), tree_col=("Hop", 70))
        self.hops_tree.bind("<<TreeviewSelect>>", lambda _e: self._pick_hop())
        return card

    def _build_events(self, host):
        S = self.app.s
        view = ttk.Frame(host)
        filt = ttk.Frame(view)
        filt.pack(side="top", fill="x", pady=(0, S(6)))
        for key, label in EVENT_KINDS:
            ttk.Checkbutton(filt, text=label, variable=self.kind_vars[key],
                            command=self._fill_events).pack(side="left", padx=(0, S(10)))
        entry = ttk.Entry(filt, textvariable=self.search_var, width=28)
        entry.pack(side="right")
        ttk.Label(filt, text="Search").pack(side="right", padx=(0, S(6)))
        self.search_var.trace_add("write", lambda *_a: self._fill_events())
        card, self.events_tree = self._table(view, (
            ("time", "Time", 150, False, "w"), ("type", "Type", 90, False, "w"),
            ("detail", "Detail", 600, True, "w")))
        card.pack(side="top", fill="both", expand=True)
        self.events_tree.bind("<Double-1>", lambda _e: self._jump_to_event())
        return view

    def _show_tab(self, key: str) -> None:
        for k, v in self.tabs.items():
            if k == key:
                v.pack(fill="both", expand=True)
            else:
                v.pack_forget()

    def recolor(self) -> None:
        self.canvas.configure(bg=self.app._ink().col["graph_bg"])
        p = self.app._ink().col
        for kind in ("alert", "clear", "route", "warn"):
            self.events_tree.tag_configure(kind, background=p[f"ev_{kind}"], foreground=p["fg"])
        self._swatches.clear()
        if self.data is not None:
            self._fill()

    # --- what's shown -----------------------------------------------------------
    def show(self, target: Optional[str] = None) -> None:
        """Called when the page is opened (``target``: the one to show)."""
        if target:
            self.target_var.set(target)
        self.refresh()

    def hide(self) -> None:
        if self._refresh_job is not None:
            self.after_cancel(self._refresh_job)
            self._refresh_job = None

    def _span(self) -> int:
        return dict(RANGES).get(self.range_var.get(), 86400)

    def _range(self):
        until = self.until if self.until is not None else time.time()
        return until - self._span(), until

    def _step(self, direction: int) -> None:
        since, until = self._range()
        new = until + direction * self._span()
        # Stepping forward back to the present lands a few milliseconds short
        # of it; within a minute of now is now, and the page goes live again.
        self._set_until(None if new >= time.time() - 60 else new)

    def _set_until(self, until: Optional[float]) -> None:
        self.until = until
        self.refresh()

    def _pick_target(self) -> None:
        self.hop = None
        self.refresh()

    def _pick_hop(self) -> None:
        sel = self.hops_tree.selection()
        if not sel:
            return
        item = sel[0]
        parent = self.hops_tree.parent(item) or item
        ttl = int(self.hops_tree.item(parent, "text").split()[-1])
        last = self.data["hops"][-1].ttl if self.data and self.data["hops"] else None
        new = None if ttl == last else ttl
        if new != self.hop:
            self.hop = new
            self.refresh()

    def _jump_to_event(self) -> None:
        sel = self.events_tree.selection()
        if not sel or not self.data:
            return
        try:
            t = float(sel[0].split(":", 1)[1])
        except (IndexError, ValueError):
            return                       # the "showing the latest N" note
        half = self._span() / 2
        self._set_until(None if t + half >= time.time() else t + half)

    # --- loading --------------------------------------------------------------
    def refresh(self) -> None:
        if self._refresh_job is not None:
            self.after_cancel(self._refresh_job)
            self._refresh_job = None
        self._load_seq += 1
        seq = self._load_seq
        target = self.target_var.get()
        since, until = self._range()
        hop = self.hop
        threading.Thread(target=self._load, args=(seq, target, since, until, hop),
                         name="history-query", daemon=True).start()
        if self.until is None:
            self._refresh_job = self.after(LIVE_REFRESH_MS, self.refresh)

    def _load(self, seq, target, since, until, hop) -> None:
        rec = self.app.recorder
        if rec is not None:
            rec.flush(timeout=2)         # include what was measured a moment ago
        reader = history.Reader()
        try:
            targets = [r["target"] for r in reader.targets()]
            if not target and targets:
                target = targets[0]
            data = {"targets": targets, "target": target, "since": since, "until": until,
                    "series": [], "hops": [], "routes": [], "events": [], "route_index": {},
                    "dest": None, "error": ""}
            if target:
                whole = investigate.series(reader, target, since, until, buckets=1)
                data["dest"] = whole[0] if whole else None
                data["series"] = investigate.series(reader, target, since, until, buckets=600, ttl=hop)
                data["hops"] = investigate.hops(reader, target, since, until)
                data["routes"] = investigate.routes(reader, target, since, until)
                data["route_index"] = {r["id"]: i for i, r in enumerate(reader.routes(target))}
                data["events"] = [dict(e) for e in reader.events(since, until, target=target)]
                data["runs"] = [dict(r) for r in reader.runs(target)]
        except Exception as exc:  # a locked or damaged file: say so, don't die
            data = {"targets": [], "target": target, "since": since, "until": until, "series": [],
                    "hops": [], "routes": [], "events": [], "route_index": {}, "dest": None,
                    "error": str(exc)}
        finally:
            reader.close()
        self.app._post(lambda: self._loaded(seq, data))

    def _loaded(self, seq: int, data: dict) -> None:
        if seq != self._load_seq:
            return                       # a newer request is on its way
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        self.data = data
        live = set(self.app._monitors) - set(self.app._loaded)
        self.target_box.configure(values=sorted(set(data["targets"]) | live))
        if data["target"] and self.target_var.get() != data["target"]:
            self.target_var.set(data["target"])
        self.later_btn.state(["disabled"] if self.until is None else ["!disabled"])
        self._fill()

    # --- filling ---------------------------------------------------------------
    def _fill(self) -> None:
        d = self.data
        span = d["until"] - d["since"]
        if d["error"]:
            self.summary.configure(text="The history couldn't be read.")
            self.detail.configure(text=d["error"])
        elif not d["target"]:
            self.summary.configure(text="Nothing recorded yet.")
            self.detail.configure(text="Add a target on the Targets page; what it measures appears here.")
        else:
            dest = d["dest"]
            reroutes = sum(1 for e in d["events"] if e["kind"] == "route")
            alerts = sum(1 for e in d["events"] if e["kind"] == "alert")
            when = f"{_when(d['since'], span)} to {'now' if self.until is None else _when(d['until'], span)}"
            if dest and dest.sent:
                self.summary.configure(
                    text=f"{d['target']}: {fmt_ms(dest.avg)} ms average, {dest.loss_pct:.1f}% loss "
                         f"at the destination")
            else:
                self.summary.configure(text=f"{d['target']}: nothing recorded in this range")
            self.detail.configure(text=f"{when}  ·  {len(d['routes'])} route(s)  ·  {reroutes} reroute(s) "
                                       f"or moves  ·  {alerts} alert(s)")
        self.chart_note.configure(
            text="Graphing the destination. Pick a hop under Hops to graph it." if self.hop is None
            else f"Graphing hop {self.hop}. Pick the last hop to go back to the destination.")
        self._fill_routes()
        self._fill_hops()
        self._fill_events()
        self._draw()

    def _swatch(self, colour: str) -> tk.PhotoImage:
        img = self._swatches.get(colour)
        if img is None:
            n = self.app.s(12)
            img = tk.PhotoImage(master=self, width=n, height=n)
            img.put(colour, to=(0, 0, n, n))
            self._swatches[colour] = img
        return img

    def _fill_routes(self) -> None:
        tree = self.routes_tree
        tree.delete(*tree.get_children())
        dark = self.app.look.dark
        for r in self.data["routes"]:
            colour = route_colour(self.data["route_index"].get(r.id, -1), dark)
            tree.insert("", "end", text=f" {r.id}", image=self._swatch(colour), values=(
                _duration(r.seconds), f"{r.share * 100:.0f}%",
                f"{fmt_ms(r.dest_avg)} ms" if r.dest_avg is not None else "—",
                f"{r.dest_loss_pct:.1f}%" if r.dest_loss_pct is not None else "—",
                investigate.describe_route(r.hops)))

    def _fill_hops(self) -> None:
        tree = self.hops_tree
        tree.delete(*tree.get_children())
        for hop in self.data["hops"]:
            routers = hop.routers
            first = routers[0] if routers else None
            label = first.addr if len(routers) == 1 else (f"{len(routers)} routers" if routers else "no reply")
            parent = tree.insert("", "end", text=f"Hop {hop.ttl}", open=len(routers) > 1, values=(
                label, f"{hop.recv:,}", f"{hop.loss_pct:.1f}%",
                fmt_ms(first.avg) if len(routers) == 1 else "",
                fmt_ms(first.rtt_min) if len(routers) == 1 else "",
                fmt_ms(first.rtt_max) if len(routers) == 1 else "",
                fmt_ms(first.jitter) if len(routers) == 1 else ""))
            if len(routers) > 1:
                total = sum(r.recv for r in routers) or 1
                for r in routers:
                    tree.insert(parent, "end", text="", values=(
                        r.addr, f"{r.recv / total * 100:.0f}%", "", fmt_ms(r.avg),
                        fmt_ms(r.rtt_min), fmt_ms(r.rtt_max), fmt_ms(r.jitter)))

    def _fill_events(self) -> None:
        if self.data is None:
            return
        tree = self.events_tree
        tree.delete(*tree.get_children())
        kinds = {k for k, v in self.kind_vars.items() if v.get()}
        needle = self.search_var.get().strip().lower()
        span = self.data["until"] - self.data["since"]
        rows = [e for e in self.data["events"]
                if (e["kind"] if e["kind"] in self.kind_vars else "info") in kinds
                and (not needle or needle in e["text"].lower())]
        for i, e in enumerate(rows[-MAX_EVENT_ROWS:]):
            tag = e["kind"] if e["kind"] in ("alert", "clear", "route", "warn") else ""
            tree.insert("", "end", iid=f"{i}:{e['t']}", tags=(tag,),
                        values=(_when(e["t"], max(span, 86401)), e["kind"], e["text"]))
        if len(rows) > MAX_EVENT_ROWS:
            tree.insert("", 0, values=("", "", f"Showing the latest {MAX_EVENT_ROWS:,} of {len(rows):,}: "
                                               f"search or pick a shorter range to see the rest."))

    def _draw(self) -> None:
        if self.data is not None:
            draw_chart(self.canvas, self.app._ink(), self.data, self.app.look.dark, self.hop)

    def _hover(self, event) -> None:
        c = self.canvas
        c.delete("hover")
        d = self.data
        if not d or not d["series"]:
            return
        S, ink = self.app.s, self.app._ink()
        x0, x1 = S(52), c.winfo_width() - S(16)
        if not x0 <= event.x <= x1:
            return
        span = max(1.0, d["until"] - d["since"])
        t = d["since"] + span * (event.x - x0) / (x1 - x0)
        p = min(d["series"], key=lambda q: abs(q.t + 30 - t))
        route = next((r.id for r in d["routes"] if any(a <= t < b for a, b in r.spans)), None)
        text = f"{_when(p.t, max(span, 86401))}  ·  {fmt_ms(p.avg)} ms  ·  {p.loss_pct:.0f}% loss"
        if route is not None:
            text += f"  ·  route {route}"
        c.create_line(event.x, S(22), event.x, c.winfo_height() - S(24), fill=ink.col["axis_text"],
                      tags="hover")
        c.create_text(x1, S(10), text=text, anchor="e", fill=ink.col["title"], font=ink.font(-1),
                      tags="hover")

    # --- export -----------------------------------------------------------------
    def _export_menu(self) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Events in this range (CSV)…", command=lambda: self._export("events"))
        menu.add_command(label="Minute summaries in this range (CSV)…", command=lambda: self._export("minutes"))
        menu.add_command(label="Every probe in this range (CSV)…", command=lambda: self._export("probes"))
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def _export(self, what: str) -> None:
        d = self.data
        if not d or not d["target"]:
            return
        stamp = time.strftime("%Y%m%d-%H%M", time.localtime(d["until"]))
        path = filedialog.asksaveasfilename(
            parent=self, title="Export from the history", defaultextension=".csv",
            initialfile=f"pingerplot_{d['target']}_{what}_{stamp}.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            n = export(what, d["target"], d["since"], d["until"], path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        messagebox.showinfo("Exported", f"{n:,} rows written to\n{path}", parent=self)


def export(what: str, target: str, since: float, until: float, path: str,
           folder=None) -> int:
    """Write one kind of history for ``target`` over the range as CSV.
    Returns the number of rows. Text that a spreadsheet would run as a
    formula is defanged."""
    reader = history.Reader(folder)
    try:
        if what == "events":
            header = ["time", "iso_time", "kind", "text"]
            rows = [(f"{e['t']:.3f}", _iso(e["t"]), e["kind"], csv_safe(e["text"]))
                    for e in reader.events(since, until, target=target)]
        elif what == "minutes":
            header = ["minute_start", "iso_time", "hop", "router", "sent", "received", "loss_pct",
                      "avg_ms", "min_ms", "max_ms", "jitter_ms"]
            rows = []
            for m in reader.minutes(target, since, until):
                t = m["minute"] * 60.0
                loss = 100.0 * (m["sent"] - m["recv"]) / m["sent"] if m["sent"] else 0.0
                rows.append((f"{t:.0f}", _iso(t), m["ttl"], csv_safe(m["addr"]), m["sent"], m["recv"],
                             f"{loss:.1f}", _num(m["rtt_sum"] / m["recv"] if m["recv"] else None),
                             _num(m["rtt_min"]), _num(m["rtt_max"]),
                             _num(m["jit_sum"] / m["jit_n"] if m["jit_n"] else None)))
        elif what == "probes":
            header = ["time", "iso_time", "round", "hop", "router", "rtt_ms", "status"]
            rows = [(f"{p['t']:.3f}", _iso(p["t"]), p["round"], p["ttl"], csv_safe(p["addr"]),
                     _num(p["rtt"]), p["status"]) for p in reader.probes(target, since, until)]
        else:
            raise ValueError(f"nothing called {what!r} to export")
    finally:
        reader.close()
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["# PingerPlot history", csv_safe(target), _iso(since), _iso(until)])
        w.writerow(header)
        w.writerows(rows)
    return len(rows)


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def _num(v) -> str:
    return "" if v is None else f"{v:.3f}".rstrip("0").rstrip(".")
