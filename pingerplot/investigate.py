"""Questions asked of the history, for the History view and exports.

"Which route was slow?", "which router at hop 7 dropped probes on Tuesday?",
"what did the destination do while we were on the other path?" -- each is a
query over the per-minute summaries (kept a year by default) plus the route
segments, answered here as plain data. No Tk: the History page draws what
these return, the tests check them against hand-made histories, and an
export writes them out.

Loss at a hop in the middle of the path is usually that router ignoring
pings it has no reason to answer, not a fault: what matters is loss that
carries through to the destination. The numbers here are the measurements;
the page says which ones to trust.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .history import Reader


@dataclass
class Point:
    """One bucket of a time series."""
    t: float                    # bucket start, epoch seconds
    sent: int
    recv: int
    avg: Optional[float]        # mean RTT of the replies, ms
    rtt_min: Optional[float]
    rtt_max: Optional[float]
    jitter: Optional[float]

    @property
    def loss_pct(self) -> float:
        return 100.0 * (self.sent - self.recv) / self.sent if self.sent else 0.0


@dataclass
class Router:
    addr: str
    recv: int
    avg: Optional[float]
    rtt_min: Optional[float]
    rtt_max: Optional[float]
    jitter: Optional[float]


@dataclass
class HopRow:
    ttl: int
    sent: int
    recv: int
    routers: List[Router] = field(default_factory=list)   # busiest first

    @property
    def loss_pct(self) -> float:
        return 100.0 * (self.sent - self.recv) / self.sent if self.sent else 0.0


@dataclass
class RouteRow:
    id: int
    hops: List[List[str]]
    seconds: float              # time on it within the range
    share: float                # of the range's recorded time, 0..1
    first_seen: float
    spans: List[tuple]          # (start, end) within the range
    dest_sent: int = 0
    dest_recv: int = 0
    dest_rtt_sum: float = 0.0

    @property
    def dest_avg(self) -> Optional[float]:
        return self.dest_rtt_sum / self.dest_recv if self.dest_recv else None

    @property
    def dest_loss_pct(self) -> Optional[float]:
        if not self.dest_sent:
            return None
        return 100.0 * (self.dest_sent - self.dest_recv) / self.dest_sent


def _point(t, sent, recv, rtt_sum, rtt_min, rtt_max, jit_sum, jit_n) -> Point:
    return Point(t, sent, recv, rtt_sum / recv if recv else None, rtt_min, rtt_max,
                 jit_sum / jit_n if jit_n else None)


def series(reader: Reader, target: str, since: float, until: float,
           buckets: int = 600, ttl: Optional[int] = None) -> List[Point]:
    """One hop's latency and loss over the range, in at most ``buckets``
    points. ``ttl`` None means the destination, whichever hop it was at the
    time (the last hop probed in each minute, so a route that got longer or
    shorter is followed)."""
    db = reader._conn()
    if db is None:
        return []
    lo, hi = int(since // 60), int(until // 60)
    width = max(1, math.ceil((hi - lo + 1) / max(1, buckets)))
    if ttl is None:
        hop_filter = ("JOIN (SELECT m2.run, m2.minute, max(m2.ttl) AS ttl FROM minutes m2 "
                      "JOIN runs r2 ON r2.id = m2.run WHERE r2.target = ? AND m2.minute BETWEEN ? AND ? "
                      "GROUP BY m2.run, m2.minute) d ON d.run = m.run AND d.minute = m.minute "
                      "AND d.ttl = m.ttl")
        args = [target, lo, hi]
    else:
        hop_filter, args = "", []
    sql = (f"SELECT (m.minute - ?) / ? AS b, sum(m.sent), sum(m.recv), sum(m.rtt_sum), "
           f"min(m.rtt_min), max(m.rtt_max), sum(m.jit_sum), sum(m.jit_n) "
           f"FROM minutes m JOIN runs r ON r.id = m.run {hop_filter} "
           f"WHERE r.target = ? AND m.minute BETWEEN ? AND ?"
           f"{' AND m.ttl = ?' if ttl is not None else ''} GROUP BY b ORDER BY b")
    params = [lo, width, *args, target, lo, hi] + ([ttl] if ttl is not None else [])
    return [_point((lo + b * width) * 60.0, *rest) for b, *rest in db.execute(sql, params)]


def hops(reader: Reader, target: str, since: float, until: float) -> List[HopRow]:
    """Every hop position in the range, with each router that answered there
    and how it did. A hop whose router changed (a reroute, load balancing)
    lists them all: the question "which router?" is answered here."""
    db = reader._conn()
    if db is None:
        return []
    rows = db.execute(
        "SELECT m.ttl, a.addr, sum(m.sent), sum(m.recv), sum(m.rtt_sum), min(m.rtt_min), "
        "max(m.rtt_max), sum(m.jit_sum), sum(m.jit_n) FROM minutes m "
        "JOIN runs r ON r.id = m.run JOIN addrs a ON a.id = m.addr "
        "WHERE r.target = ? AND m.minute BETWEEN ? AND ? GROUP BY m.ttl, a.addr ORDER BY m.ttl",
        (target, int(since // 60), int(until // 60))).fetchall()
    out: Dict[int, HopRow] = {}
    for ttl, addr, sent, recv, rtt_sum, rmin, rmax, jsum, jn in rows:
        hop = out.setdefault(ttl, HopRow(ttl, 0, 0))
        hop.sent += sent
        hop.recv += recv
        if addr:
            hop.routers.append(Router(addr, recv, rtt_sum / recv if recv else None, rmin, rmax,
                                      jsum / jn if jn else None))
    for hop in out.values():
        hop.routers.sort(key=lambda r: -r.recv)
    return [out[t] for t in sorted(out)]


def routes(reader: Reader, target: str, since: float, until: float,
           now: Optional[float] = None) -> List[RouteRow]:
    """Each route the target was on within the range: how long, what share,
    and how the destination did meanwhile, so a bad path stands out."""
    import time as _time
    now = _time.time() if now is None else now
    stored = {r["id"]: r for r in reader.routes(target)}
    rows: Dict[int, RouteRow] = {}
    for seg in reader.segments(target, since, until):
        start = max(since, seg["start"])
        end = min(until, seg["end"] if seg["end"] is not None else now)
        if end <= start or seg["route"] not in stored:
            continue
        r = stored[seg["route"]]
        row = rows.get(seg["route"])
        if row is None:
            row = rows[seg["route"]] = RouteRow(seg["route"], json.loads(r["hops"]), 0.0, 0.0,
                                                r["first"], [])
        row.seconds += end - start
        row.spans.append((start, end))
    total = sum(r.seconds for r in rows.values())
    for row in rows.values():
        row.share = row.seconds / total if total else 0.0
    if rows:
        # The destination, minute by minute, credited to the route in use.
        for p in series(reader, target, since, until, buckets=10 ** 6):
            mid = p.t + 30
            for row in rows.values():
                if any(a <= mid < b for a, b in row.spans):
                    row.dest_sent += p.sent
                    row.dest_recv += p.recv
                    row.dest_rtt_sum += (p.avg or 0.0) * p.recv
                    break
    return sorted(rows.values(), key=lambda r: r.spans[0][0])


def describe_route(hops_: List[List[str]], names: Optional[Dict[str, str]] = None) -> str:
    """A route on one line: routers in hop order, "/" between load-balanced
    ones, "*" for a hop that never answered."""
    names = names or {}
    return "  >  ".join(" / ".join(names.get(a, a) for a in hop) or "*" for hop in hops_)
