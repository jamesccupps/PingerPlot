"""Small reusable pieces of the window: a scrolling frame, underlined
navigation, the target card, and the settings row."""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Optional, Sequence, Tuple


class WheelScroll:
    """Wheel events as whole scroll units, ``scroll(n)``, on every platform.

    Windows sends <MouseWheel> in multiples of 120 for a mouse but small
    deltas (30, say) for a precision touchpad, and int(-30 / 120) is 0: the
    touchpad never scrolled. The deltas now add up until they make a step.
    Tk 8.6 on X11 sends no <MouseWheel> at all, only Button-4 and Button-5,
    so on Linux the wheel did nothing. macOS sends small deltas, one per
    unit."""

    EVENTS = ("<MouseWheel>", "<Button-4>", "<Button-5>")

    def __init__(self, scroll) -> None:
        self._scroll, self._acc = scroll, 0.0

    def wheel(self, e) -> None:
        self._acc += -e.delta if sys.platform == "darwin" else -e.delta / 120
        steps = int(self._acc)
        if steps:
            self._acc -= steps
            self._scroll(steps)

    def up(self, _e=None) -> None:
        self._scroll(-1)

    def down(self, _e=None) -> None:
        self._scroll(1)

    def bind(self, widget) -> None:
        for ev, fn in zip(self.EVENTS, (self.wheel, self.up, self.down)):
            widget.bind(ev, fn)

    def bind_all(self, widget) -> None:
        for ev, fn in zip(self.EVENTS, (self.wheel, self.up, self.down)):
            widget.bind_all(ev, fn)

    @classmethod
    def unbind_all(cls, widget) -> None:
        for ev in cls.EVENTS:
            widget.unbind_all(ev)


class ScrollFrame(ttk.Frame):
    """A vertically scrolling frame. Put children in ``.inner``.

    The mouse wheel scrolls it while the pointer is over it, and only when
    there is something to scroll: Tk binds the wheel per widget, so the
    binding is added on enter and removed on leave.
    """

    def __init__(self, parent, look, style: str = "TFrame") -> None:
        super().__init__(parent, style=style)
        self.look = look
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=look.c["bg"])
        self.inner = ttk.Frame(self.canvas, style=style)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.wheel = WheelScroll(self._scroll)
        self.bind("<Enter>", lambda _e: self.wheel.bind_all(self))
        self.bind("<Leave>", lambda _e: WheelScroll.unbind_all(self))

    def _on_inner(self, _e=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, e) -> None:
        self.canvas.itemconfigure(self._win, width=e.width)

    def _scroll(self, units: int) -> None:
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(units, "units")

    def recolor(self) -> None:
        self.canvas.configure(bg=self.look.c["bg"])


class Nav(ttk.Frame):
    """Text tabs with an accent underline under the current one: the house
    navigation (UniFi Stock Watcher, Slate)."""

    def __init__(self, parent, look, items: Sequence[Tuple[str, str]],
                 on_select: Callable[[str], None]) -> None:
        super().__init__(parent)
        self.look = look
        self.on_select = on_select
        self.current: Optional[str] = None
        self._labels: Dict[str, ttk.Label] = {}
        self._bars: Dict[str, ttk.Frame] = {}
        S = look.S
        for i, (key, text) in enumerate(items):
            cell = ttk.Frame(self)
            cell.grid(row=0, column=i, padx=(0 if i == 0 else S(20), 0), sticky="s")
            lab = ttk.Label(cell, text=text, style="Nav.TLabel", cursor="hand2")
            lab.pack(side="top")
            bar = ttk.Frame(cell, height=S(3), style="TFrame")
            bar.pack(side="top", fill="x")
            lab.bind("<Button-1>", lambda _e, k=key: self.select(k, notify=True))
            self._labels[key], self._bars[key] = lab, bar

    def select(self, key: str, notify: bool = False) -> None:
        self.current = key
        for k, lab in self._labels.items():
            on = k == key
            lab.configure(style="NavActive.TLabel" if on else "Nav.TLabel")
            self._bars[k].configure(style="NavBar.TFrame" if on else "TFrame")
        if notify:
            self.on_select(key)


class TargetCard(ttk.Frame):
    """One monitored target in the list: a status dot, its name and state,
    and the destination's numbers. Click selects; right-click or the ⋯
    button opens its menu."""

    DOTS = {"ok": "green", "alert": "red", "warn": "amber", "idle": "text3", "paused": "text3"}

    def __init__(self, parent, look, name: str, on_select: Callable[[str], None],
                 on_menu: Callable[[str, int, int], None]) -> None:
        super().__init__(parent, style="Tile.TFrame", padding=(look.S(12), look.S(8)))
        self.look, self.name = look, name
        S = look.S
        self.columnconfigure(1, weight=1)
        self.dot = ttk.Label(self, text="●", style="Card.Dot.TLabel")
        self.dot.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, S(10)), pady=(S(1), 0))
        self.title = ttk.Label(self, text=name, style="Card.Strong.TLabel")
        self.title.grid(row=0, column=1, sticky="w")
        self.stats = ttk.Label(self, text="", style="Card.Muted.TLabel")
        self.stats.grid(row=1, column=1, sticky="w")
        self.more = ttk.Button(self, text=look.icon("more"), style="Card.Icon.Subtle.TButton",
                               width=0, command=self._menu_from_button)
        self.more.grid(row=0, column=2, rowspan=2, sticky="e", padx=(S(6), 0))
        self.on_menu = on_menu
        for w in (self, self.dot, self.title, self.stats):
            w.bind("<Button-1>", lambda _e: on_select(self.name))
            w.bind("<Button-3>", lambda e: on_menu(self.name, e.x_root, e.y_root))
        self._state = None

    def _menu_from_button(self) -> None:
        self.on_menu(self.name, self.more.winfo_rootx(),
                     self.more.winfo_rooty() + self.more.winfo_height())

    def update_card(self, state: str, title: str, stats: str, selected: bool) -> None:
        key = (state, title, stats, selected, self.look.c["bg"])
        if key == self._state:
            return                      # nothing changed: don't make Tk relayout
        self._state = key
        style = "Alert.Tile.TFrame" if state == "alert" else (
            "Selected.Tile.TFrame" if selected else "Tile.TFrame")
        self.configure(style=style)
        self.dot.configure(foreground=self.look.c[self.DOTS.get(state, "text3")])
        self.title.configure(text=title)
        self.stats.configure(text=stats)


def setting_row(parent, look, title: str, description: str = "",
                control: Optional[Callable[[ttk.Frame], tk.Widget]] = None,
                first: bool = False) -> ttk.Frame:
    """A row in a settings card: a title and an optional description on the
    left, the control on the right, a divider above all but the first."""
    S = look.S
    if not first:
        ttk.Frame(parent, style="Divider.TFrame", height=1).pack(side="top", fill="x", padx=S(14))
    row = ttk.Frame(parent, style="Card.TFrame", padding=(S(16), S(10)))
    row.pack(side="top", fill="x", padx=S(1))
    row.columnconfigure(0, weight=1)
    ttk.Label(row, text=title, style="Card.TLabel").grid(row=0, column=0, sticky="w")
    if description:
        ttk.Label(row, text=description, style="Card.Muted.TLabel",
                  wraplength=S(460), justify="left").grid(row=1, column=0, sticky="w", pady=(S(2), 0))
    if control is not None:
        w = control(row)
        w.grid(row=0, column=1, rowspan=2, sticky="e", padx=(S(16), 0))
    return row


def section(parent, look, heading: str) -> ttk.Frame:
    """A heading over a rounded card; rows go in the returned card."""
    S = look.S
    ttk.Label(parent, text=heading, style="Heading.TLabel").pack(
        side="top", anchor="w", pady=(S(18), S(8)))
    card = ttk.Frame(parent, style="Tile.TFrame", padding=(S(1), S(4)))
    card.pack(side="top", fill="x")
    return card
