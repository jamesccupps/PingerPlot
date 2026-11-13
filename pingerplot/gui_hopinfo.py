"""What's known about a hop's router: where it is, whose network it's on, and
which routers have answered at that hop lately.

Two witnesses for "where": the router's DNS name (places.py), which for the
backbone is usually right, and the IP database (geoip.py), which is usually
right for the edges and often wrong for the backbone. The tables show the
name's answer when there is one; the Details window shows both, each with its
source, so a disagreement is visible rather than hidden.
"""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import List, Optional, Tuple

from . import history, investigate, places
from .gui_charts import fmt_ms


def location(address: Optional[str], hostname: Optional[str], geo) -> str:
    """The best short answer to "where is it?" for a table cell."""
    hint = places.from_name(hostname)
    if hint is not None:
        return str(hint)
    info = geo.get(address) if address else None
    if info is None:
        return ""
    if info.city and info.country:
        return f"{info.city}, {info.country}"
    return info.city or info.country


def network(address: Optional[str], geo) -> str:
    info = geo.get(address) if address else None
    return info.network() if info is not None else ""


def facts(address: Optional[str], hostname: Optional[str], geo) -> List[Tuple[str, str]]:
    """Every known fact about the router, as (label, value) rows."""
    rows: List[Tuple[str, str]] = [("Address", address or "no reply")]
    rows.append(("Name", hostname or ("looking it up…" if hostname is None and address else "none")))
    hint = places.from_name(hostname)
    info = geo.get(address) if address else None
    if hint is not None:
        rows.append(("Where, from its name", f"{hint}  (the \"{hint.clue}\" in its name)"))
    if info is not None:
        where = info.place()
        rows.append(("Where, from the IP database", where or "not known"))
        if hint is not None and info.city and info.city.lower() not in hint.city.lower():
            rows.append(("", "The two disagree: for a carrier's router, the name is usually right."))
        rows.append(("Network", info.network() or "not known"))
        if info.isp and info.isp != (info.org or ""):
            rows.append(("Internet provider", info.isp))
    elif address:
        rows.append(("Where, from the IP database",
                     "not looked up (a private address, or lookups are off or pending)"))
    return rows


class HopDetails(tk.Toplevel):
    """One hop, everything known about it."""

    def __init__(self, app, target: str, ttl: int, address: Optional[str],
                 hostname: Optional[str]) -> None:
        super().__init__(app.root)
        look, S = app.look, app.s
        self.title(f"Hop {ttl} of {target}")
        self.transient(app.root)
        self.geometry(f"{S(640)}x{S(520)}")
        look.style_window(self)
        body = ttk.Frame(self, padding=(S(20), S(16)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"Hop {ttl}", style="Heading.TLabel").pack(anchor="w")

        grid = ttk.Frame(body)
        grid.pack(fill="x", pady=(S(8), 0))
        grid.columnconfigure(1, weight=1)
        for i, (label, value) in enumerate(facts(address, hostname, app.geo)):
            ttk.Label(grid, text=label, style="Muted.TLabel").grid(row=i, column=0, sticky="nw",
                                                                   padx=(0, S(12)), pady=S(2))
            ttk.Label(grid, text=value, wraplength=S(430), justify="left").grid(
                row=i, column=1, sticky="w", pady=S(2))

        ttk.Label(body, text="Routers at this hop in the last 24 hours", style="Strong.TLabel").pack(
            anchor="w", pady=(S(16), S(4)))
        cols = (("router", "Router", 140), ("where", "Where", 130), ("share", "Answered", 70),
                ("avg", "Avg", 50), ("worst", "Worst", 55))
        tree = ttk.Treeview(body, columns=[c[0] for c in cols], show="headings", height=6)
        for key, title, width in cols:
            tree.heading(key, text=title, anchor="w")
            tree.column(key, width=S(width), anchor="w", stretch=key == "where")
        tree.pack(fill="both", expand=True)
        seen = self._recent_routers(app, target, ttl)
        if not seen:
            tree.insert("", "end", values=("Nothing recorded yet (or history is off).", "", "", "", ""))
        total = sum(r.recv for r in seen) or 1
        for r in seen:
            app.geo.request(r.addr)
            tree.insert("", "end", values=(r.addr, location(r.addr, None, app.geo),
                                           f"{r.recv / total * 100:.0f}%", fmt_ms(r.avg),
                                           fmt_ms(r.rtt_max)))
        ttk.Button(body, text="Close", command=self.destroy).pack(anchor="e", pady=(S(12), 0))
        self.bind("<Escape>", lambda _e: self.destroy())

    @staticmethod
    def _recent_routers(app, target: str, ttl: int) -> list:
        if app.recorder is not None:
            app.recorder.flush(timeout=2)
        reader = history.Reader()
        try:
            now = time.time()
            for hop in investigate.hops(reader, target, now - 86400, now):
                if hop.ttl == ttl:
                    return hop.routers
            return []
        except Exception:
            return []
        finally:
            reader.close()
