"""Canvas drawing for the hop graph, the all-hops timeline and the map.

Pure drawing: each function takes a Canvas, the data to draw, and an ``Ink``
(the palette, fonts and pixel scale), and touches nothing else. The window
decides when to call them (only for the visible view, and only when the data
changed, see model.draw_version).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from . import geoip, worldmap
from .model import HopView, Sample

WARN_MS = 120.0   # a hop goes amber at/above this average latency
BAD_MS = 250.0    # ...and red at/above this


def fmt_ms(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if v < 1:
        return "<1"
    if v < 10:
        return f"{v:.1f}"
    return f"{v:.0f}"


def row_tag(v: HopView, is_dest: bool) -> str:
    """Row colour: red for a problem, amber for a warning.

    BAD_MS used to be tested one branch too late -- `avg >= BAD_MS` returned
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


@dataclass
class Ink:
    """What drawing needs from the window: colours by role, fonts, scale."""
    col: Dict[str, str]
    font: Callable[..., tuple]          # Theme.font(delta, weight)
    s: Callable[[float], int]           # logical pixels -> screen pixels

    def hop_fg(self, v: HopView, is_dest: bool) -> str:
        return {"bad": self.col["fg_bad"], "warn": self.col["fg_warn"],
                "dest": self.col["fg_dest"], "ok": self.col["fg_ok"]}[row_tag(v, is_dest)]


def _flush_segment(c, ink: Ink, segment: List[float], baseline: float) -> None:
    if len(segment) >= 4:  # at least two points
        poly = [segment[0], baseline] + segment + [segment[-2], baseline]
        c.create_polygon(poly, fill=ink.col["fill"], outline="")
        c.create_line(segment, fill=ink.col["line"], width=max(1, ink.s(2)), joinstyle="round")
    elif len(segment) == 2:
        r = max(1, ink.s(2))
        c.create_oval(segment[0] - r, segment[1] - r, segment[0] + r, segment[1] + r,
                      fill=ink.col["line"], outline="")


def plot_series(c, ink: Ink, samples, x0, x1, y_base, y_top, y_max, t_min, span, tick=8) -> None:
    """One latency series (filled line + loss ticks) inside a rectangle.
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
            _flush_segment(c, ink, segment, y_base)
            segment = []
        else:
            segment.extend((X(s.t), Y(s.rtt)))
    _flush_segment(c, ink, segment, y_base)

    tick_h = min(tick, max(2, y_base - y_top))
    for x in loss_x:
        c.create_line(x, y_base, x, y_base - tick_h, fill=ink.col["loss"], width=1)


def draw_graph(c, ink: Ink, ttl: Optional[int], samples: List[Sample]) -> None:
    """The selected hop's latency over the samples kept."""
    S = ink.s
    c.delete("all")
    w, h = c.winfo_width(), c.winfo_height()
    if w < 40 or h < 40:
        return
    pad_l, pad_r, pad_t, pad_b = S(52), S(16), S(30), S(26)
    x0, x1 = pad_l, w - pad_r
    y_base, y_top = h - pad_b, pad_t
    rtts = [s.rtt for s in samples if s.rtt is not None]
    y_max = max(10.0, (max(rtts) if rtts else 50.0) * 1.15)

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = y_base + (y_top - y_base) * frac
        c.create_line(x0, y, x1, y, fill=ink.col["grid"])
        c.create_text(x0 - S(8), y, text=f"{y_max * frac:.0f}", anchor="e",
                      fill=ink.col["axis_text"], font=ink.font(-2))

    title = "Select a hop to graph" if ttl is None else f"Hop {ttl} · latency (ms)"
    c.create_text(x0, y_top - S(16), text=title, anchor="w", fill=ink.col["title"],
                  font=ink.font(-1, "semibold"))
    if not samples:
        return

    t0, t1 = samples[0].t, samples[-1].t
    span = max(1e-6, t1 - t0)
    plot_series(c, ink, samples, x0, x1, y_base, y_top, y_max, t0, span, tick=S(10))

    last = samples[-1]
    if last.rtt is not None:
        lx = x0 + (x1 - x0) * ((last.t - t0) / span)
        ly = y_base + (y_top - y_base) * (min(last.rtt, y_max) / y_max)
        r = S(3)
        c.create_oval(lx - r, ly - r, lx + r, ly + r, fill=ink.col["line"], outline="")
        c.create_text(lx - S(6), ly - S(8), text=fmt_ms(last.rtt), anchor="se",
                      fill=ink.col["line"], font=ink.font(-2, "semibold"))
    c.create_text(x1, y_base + S(13), text=f"← {span:.0f}s  ·  now →",
                  anchor="e", fill=ink.col["axis_text"], font=ink.font(-2))


def fit_text(widget, text: str, font, width: int) -> str:
    """``text``, shortened with "…" to fit ``width`` pixels in ``font``.
    Measured, not counted: a long router name in a wide font (DejaVu Sans on
    Linux) ran into the column beside it at the 30 characters that fitted
    Segoe UI."""
    from tkinter import font as tkfont
    f = tkfont.Font(root=widget, font=font)
    if width <= 0 or f.measure(text) <= width:
        return text if width > 0 else ""
    lo, hi = 0, len(text)
    while lo < hi:                          # the longest prefix that fits with "…"
        mid = (lo + hi + 1) // 2
        if f.measure(text[:mid] + "…") <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…" if lo else ""


def draw_timeline(c, ink: Ink, views: List[HopView], data: Dict[int, List[Sample]]) -> str:
    """Every hop as a strip on one shared time and latency scale. Returns the
    caption for the view's header."""
    S = ink.s
    c.delete("all")
    w = c.winfo_width()
    if w < 60:
        return ""
    if not views:
        c.configure(scrollregion=(0, 0, w, 1))
        return "No data yet."

    all_s = [s for lst in data.values() for s in lst]
    rtts = [s.rtt for s in all_s if s.rtt is not None]
    g_max = max(10.0, (max(rtts) if rtts else 50.0) * 1.1)
    times = [s.t for s in all_s]
    t_min = min(times) if times else 0.0
    span = max(1e-6, (max(times) if times else 1.0) - t_min)

    n = len(views)
    strip_h, gutter, pad = S(44), S(200), S(6)
    c.configure(scrollregion=(0, 0, w, n * strip_h))
    last_ttl = views[-1].ttl

    for i, v in enumerate(views):
        y0 = i * strip_h
        y_top, y_base = y0 + pad, y0 + strip_h - pad
        gx0, gx1 = gutter, w - S(12)
        if i:
            c.create_line(0, y0, w, y0, fill=ink.col["strip_sep"])
        c.create_rectangle(gx0, y_top, gx1, y_base, fill=ink.col["graph_bg"],
                           outline=ink.col["strip_border"])
        loss = c.create_text(gutter - S(8), (y_top + y_base) / 2, anchor="e",
                             text=f"{v.loss_pct:.0f}% loss", fill=ink.col["axis_text"],
                             font=ink.font(-3))
        room = (c.bbox(loss) or (gutter - S(8),))[0] - S(8) - S(8)
        label = fit_text(c, f"{v.ttl}.  {v.hostname or v.address or '*'}", ink.font(-2), room)
        c.create_text(S(8), (y_top + y_base) / 2, anchor="w", text=label,
                      fill=ink.hop_fg(v, v.ttl == last_ttl), font=ink.font(-2))
        plot_series(c, ink, data.get(v.ttl, []), gx0, gx1, y_base, y_top, g_max, t_min, span,
                    tick=S(6))
        c.create_text(gx1 - S(4), y_top + S(2), anchor="ne", text=fmt_ms(v.current),
                      fill=ink.col["line"], font=ink.font(-3, "semibold"))
    return f"All hops on one scale, 0 to {g_max:.0f} ms  ·  last {span:.0f} s  ·  {n} hops"


def _ttl_list(ttls: List[int]) -> str:
    """[5] -> "5"; [5, 6] -> "5–6"; [2, 5] -> "2, 5"."""
    if len(ttls) > 1 and ttls == list(range(ttls[0], ttls[-1] + 1)):
        return f"{ttls[0]}–{ttls[-1]}"
    return ", ".join(str(t) for t in ttls)


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _crosses(seg, box) -> bool:
    """Whether the line segment ((x0, y0), (x1, y1)) passes through ``box``
    (Liang–Barsky clipping)."""
    (x0, y0), (x1, y1) = seg
    t0, t1 = 0.0, 1.0
    dx, dy = x1 - x0, y1 - y0
    for p, q in ((-dx, x0 - box[0]), (dx, box[2] - x0), (-dy, y0 - box[1]), (dy, box[3] - y0)):
        if p == 0:
            if q < 0:
                return False
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return False
    return True


def _label_places(c, ink: Ink, located, gap: int, rad: int = 0, bounds=None) -> None:
    """One label per place, in path order. Hops at the same spot share one
    ("5–6. Denver"). Each label goes right of its dot, left, above or below:
    the first of those that covers no other label or dot, stays inside
    ``bounds`` (x0, y0, x1, y1) and doesn't cross the path's line; failing
    that, the first that only crosses the line, on a plate in the map's
    colour so it stays readable; failing that, it's left off. At world scale
    a path within one region is a few pixels across, and one label per hop
    piled up into an unreadable smear."""
    places: List[list] = []                 # [x, y, ttls, name]
    for v, g, x, y in located:
        name = g.city or g.country or ""
        for p in places:
            if abs(p[0] - x) < 1 and abs(p[1] - y) < 1 and p[3] == name:
                p[2].append(v.ttl)
                break
        else:
            places.append([x, y, [v.ttl], name])
    path = [(located[i - 1][2:4], located[i][2:4]) for i in range(1, len(located))]
    taken: List[tuple] = [(x - rad, y - rad, x + rad, y + rad) for x, y, _t, _n in places]
    font = ink.font(-2)
    for x, y, ttls, name in places:
        text = f"{_ttl_list(ttls)}. {name}".strip()
        clear, on_line = None, None
        for anchor, dx, dy in (("w", gap, 0), ("e", -gap, 0), ("s", 0, -gap), ("n", 0, gap)):
            item = c.create_text(x + dx, y + dy, anchor=anchor, text=text, font=font)
            box = c.bbox(item)
            c.delete(item)
            if not box or any(_overlaps(box, b) for b in taken):
                continue
            if bounds and (box[0] < bounds[0] or box[1] < bounds[1]
                           or box[2] > bounds[2] or box[3] > bounds[3]):
                continue
            if any(_crosses(s, box) for s in path):
                on_line = on_line or (anchor, dx, dy, box)
                continue
            clear = (anchor, dx, dy, box)
            break
        chosen = clear or on_line
        if chosen is None:
            continue
        anchor, dx, dy, box = chosen
        taken.append(box)
        if chosen is on_line and ink.col.get("graph_bg"):
            c.create_rectangle(box[0] - 2, box[1], box[2] + 2, box[3],
                               fill=ink.col["graph_bg"], outline="")
        c.create_text(x + dx, y + dy, anchor=anchor, text=text, fill=ink.col["title"], font=font)


MAP_MIN_SPAN = 16.0    # degrees: the coastlines are Natural Earth 110m, about 0.8 deg a step
MAP_PAD = 0.25         # of the hops' extent, on each side


def map_view(points, w: float, h: float, margin: float = 0.0):
    """The Map's projection, ``(lon, lat) -> (x, y)``, for a ``w`` x ``h``
    canvas: zoomed to ``points`` (the located hops, as (lon, lat)) with some
    room around them, but never narrower than MAP_MIN_SPAN degrees, where the
    coarse coastlines would turn to zigzags. A degree of longitude is drawn
    cos(latitude) as wide as one of latitude, at the middle of the view, so
    nothing is stretched. With no points it's the whole world, as before.

    A path across the Pacific (lon jumping from about -120 to +140) gets a
    view nearly the world's width, as it did before; it isn't wrapped round
    the date line."""
    if not points:
        return lambda lon, lat: ((lon + 180.0) / 360.0 * w, (90.0 - lat) / 180.0 * h)
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    lat_mid = (min(lats) + max(lats)) / 2.0
    k = max(0.2, math.cos(math.radians(lat_mid)))
    x0, x1 = min(lons) * k, max(lons) * k
    y0, y1 = min(lats), max(lats)
    span_x = max((x1 - x0) * (1 + 2 * MAP_PAD), MAP_MIN_SPAN)
    span_y = max((y1 - y0) * (1 + 2 * MAP_PAD), MAP_MIN_SPAN / 2)
    scale = min(max(w - 2 * margin, 1) / span_x, max(h - 2 * margin, 1) / span_y)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    return lambda lon, lat: (w / 2.0 + (lon * k - cx) * scale, h / 2.0 - (lat - cy) * scale)


def draw_map(c, ink: Ink, views: List[HopView], geo) -> str:
    """Located public hops on the coastline backdrop, joined in path order,
    zoomed to the path (map_view). Asks ``geo`` for any it hasn't looked up.
    Returns the header caption."""
    S = ink.s
    c.delete("all")
    w, h = c.winfo_width(), c.winfo_height()
    if w < 80 or h < 80:
        return ""

    public = 0
    found = []
    for v in views:
        if v.address and geoip._is_public(v.address):
            public += 1
            geo.request(v.address)
            g = geo.get(v.address)
            if g:
                found.append((v, g))
    proj = map_view([(g.lon, g.lat) for _v, g in found], w, h, margin=S(24))

    for poly in worldmap.COASTLINES:
        pts = []
        for lon, lat in poly:
            pts.extend(proj(lon, lat))
        if len(pts) >= 4:
            c.create_line(pts, fill=ink.col["grid"], width=1)

    located = [(v, g) + proj(g.lon, g.lat) for v, g in found]

    for i in range(1, len(located)):
        c.create_line(located[i - 1][2], located[i - 1][3], located[i][2], located[i][3],
                      fill=ink.col["line"], width=max(1, S(2)))
    last_ttl = views[-1].ttl if views else None
    rad = max(2, S(4))
    for v, g, x, y in located:
        c.create_oval(x - rad, y - rad, x + rad, y + rad,
                      fill=ink.hop_fg(v, v.ttl == last_ttl), outline=ink.col["graph_bg"])
    _label_places(c, ink, located, rad + S(3), rad, bounds=(0, 0, w, h))

    if not views:
        return "No data yet: start a target to map its hops."
    # The failure count only appears when the lookup service is actually
    # misbehaving. Without it, a map that has quietly stopped resolving looks
    # identical to one whose hops simply have no location.
    note = f"  ·  {geo.failures} lookup error(s), retrying" if geo.failures else ""
    return (f"{len(located)} of {public} public hops located by ipwho.is "
            f"(private and LAN hops have no location){note}")
