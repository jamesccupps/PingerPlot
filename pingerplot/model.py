"""Data model and per-hop statistics.

Pure standard-library, no Tk or ctypes here so it stays unit-testable on any
platform. A :class:`Hop` owns a bounded ring buffer of :class:`Sample`s and
derives all statistics from it. :class:`HopView` is an immutable snapshot the
GUI renders without touching the live (monitor-owned, lock-protected) Hop.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional

DEFAULT_HISTORY = 600  # samples kept per hop (~25 min at a 2.5 s interval)


@dataclass(frozen=True)
class Sample:
    t: float                 # epoch seconds when recorded
    rtt: Optional[float]     # round-trip ms, or None for a lost probe


@dataclass(frozen=True)
class Event:
    """A timestamped notable occurrence: a route change or an alert."""

    seq: int                 # monotonic id, for "show me what's new" in the UI
    t: float                 # epoch seconds
    kind: str                # "route" | "alert" | "clear" | "info"
    text: str


class Hop:
    """One position along the path (a TTL). Mutable; lives inside the monitor."""

    def __init__(self, ttl: int, history: int = DEFAULT_HISTORY) -> None:
        self.ttl = ttl
        self.address: Optional[str] = None
        self.hostname: Optional[str] = None  # None=unresolved, ""=no PTR record
        self.last_status: Optional[int] = None
        self.resolving = False
        self.samples: Deque[Sample] = deque(maxlen=history)

    def record(self, rtt: Optional[float], address: Optional[str], status: int) -> None:
        self.last_status = status
        if address and address != self.address:
            # Path changed under this TTL (load balancing / reroute): adopt the
            # new responder and re-resolve its name.
            self.address = address
            self.hostname = None
        self.samples.append(Sample(time.time(), rtt))

    # --- derived statistics, all computed over the current window ----------
    def _rtts(self) -> List[float]:
        return [s.rtt for s in self.samples if s.rtt is not None]

    def _recent(self, k: int) -> List[Sample]:
        """The last ``k`` samples (all of them when ``k`` is 0 or oversized)."""
        if not k or k >= len(self.samples):
            return list(self.samples)
        return list(self.samples)[-k:]

    def recent_loss_pct(self, k: int = 0) -> float:
        """Packet loss over the last ``k`` probes — the 'sustained' signal."""
        recent = self._recent(k)
        if not recent:
            return 0.0
        lost = sum(1 for s in recent if s.rtt is None)
        return 100.0 * lost / len(recent)

    def recent_received(self, k: int = 0) -> int:
        return sum(1 for s in self._recent(k) if s.rtt is not None)

    def recent_avg(self, k: int = 0) -> Optional[float]:
        rtts = [s.rtt for s in self._recent(k) if s.rtt is not None]
        return sum(rtts) / len(rtts) if rtts else None

    @property
    def sent(self) -> int:
        return len(self.samples)

    @property
    def received(self) -> int:
        return sum(1 for s in self.samples if s.rtt is not None)

    @property
    def lost(self) -> int:
        return self.sent - self.received

    @property
    def loss_pct(self) -> float:
        return (100.0 * self.lost / self.sent) if self.sent else 0.0

    @property
    def current(self) -> Optional[float]:
        """Most recent sample's RTT (None means the last probe was lost)."""
        return self.samples[-1].rtt if self.samples else None

    @property
    def avg(self) -> Optional[float]:
        r = self._rtts()
        return sum(r) / len(r) if r else None

    @property
    def best(self) -> Optional[float]:
        r = self._rtts()
        return min(r) if r else None

    @property
    def worst(self) -> Optional[float]:
        r = self._rtts()
        return max(r) if r else None

    @property
    def jitter(self) -> Optional[float]:
        """Population standard deviation of RTT over the window."""
        r = self._rtts()
        if len(r) < 2:
            return None
        mean = sum(r) / len(r)
        return (sum((x - mean) ** 2 for x in r) / len(r)) ** 0.5

    def compute(self):
        """All window stats in a *single* pass — much cheaper than the
        individual properties above, each of which re-scans the deque. Returns
        ``(sent, received, loss_pct, current, avg, best, worst, jitter)``. This
        is the per-tick hot path (see :meth:`HopView.of`)."""
        samples = self.samples
        n = len(samples)
        if n == 0:
            return (0, 0, 0.0, None, None, None, None, None)
        received = 0
        total = 0.0
        total_sq = 0.0
        best: Optional[float] = None
        worst: Optional[float] = None
        for s in samples:
            r = s.rtt
            if r is None:
                continue
            received += 1
            total += r
            total_sq += r * r
            if best is None or r < best:
                best = r
            if worst is None or r > worst:
                worst = r
        current = samples[-1].rtt
        loss_pct = 100.0 * (n - received) / n
        if received:
            avg = total / received
            # population variance via E[x^2] - E[x]^2 (one pass); clamp tiny
            # negative FP results to 0 for the all-equal case.
            var = total_sq / received - avg * avg
            jitter = (var ** 0.5 if var > 0 else 0.0) if received >= 2 else None
        else:
            avg = jitter = None
        return (n, received, loss_pct, current, avg, best, worst, jitter)


@dataclass(frozen=True)
class HopView:
    """Immutable snapshot of a :class:`Hop` for the UI thread."""

    ttl: int
    address: Optional[str]
    hostname: Optional[str]
    last_status: Optional[int]
    sent: int
    received: int
    loss_pct: float
    current: Optional[float]
    avg: Optional[float]
    best: Optional[float]
    worst: Optional[float]
    jitter: Optional[float]

    @classmethod
    def of(cls, hop: Hop) -> "HopView":
        sent, received, loss_pct, current, avg, best, worst, jitter = hop.compute()
        return cls(
            ttl=hop.ttl,
            address=hop.address,
            hostname=hop.hostname,
            last_status=hop.last_status,
            sent=sent,
            received=received,
            loss_pct=loss_pct,
            current=current,
            avg=avg,
            best=best,
            worst=worst,
            jitter=jitter,
        )


def mos(avg_ms: Optional[float], jitter_ms: Optional[float], loss_pct: float) -> Optional[float]:
    """Estimated Mean Opinion Score (1.0–5.0) for a VoIP-style call over this
    path, from the simplified ITU-T G.107 E-model used by most ping/traceroute
    tools. Needs a latency measurement; returns None without one.

    ~4.4 is a clean LAN/broadband path; <3.1 is poor. It is only meaningful for
    the *end-to-end* path (the destination hop), not an intermediate router."""
    if avg_ms is None:
        return None
    effective_latency = avg_ms + (jitter_ms or 0.0) * 2.0 + 10.0
    if effective_latency < 160.0:
        r = 93.2 - effective_latency / 40.0
    else:
        r = 93.2 - (effective_latency - 120.0) / 10.0
    r -= loss_pct * 2.5  # each 1% loss costs ~2.5 R-factor points
    if r < 0.0:
        return 1.0
    r = min(r, 100.0)
    score = 1.0 + 0.035 * r + r * (r - 60.0) * (100.0 - r) * 7e-6
    return max(1.0, min(5.0, score))


def mos_label(score: Optional[float]) -> str:
    if score is None:
        return "—"
    if score >= 4.3:
        return "Excellent"
    if score >= 4.0:
        return "Good"
    if score >= 3.6:
        return "Fair"
    if score >= 3.1:
        return "Poor"
    return "Bad"


def draw_version(active_name, monitor, selected_ttl, theme, geo_count: int):
    """Everything a canvas redraw depends on, as one comparable tuple.

    The GUI's refresh timer ticks faster than probes arrive, so a canvas is
    only redrawn when this changes. ``geo_count`` belongs in it because geo
    lookups land asynchronously, roughly a second apart, and touch none of the
    other four — so without it the Map tab drew its coastlines, fired off the
    lookups and then never plotted the answers. A live monitor hid that behind
    the next round's redraw; a *loaded session* has no next round, so its map
    stayed empty until the window happened to be resized.

    Here rather than in gui.py so it is testable on any platform, like the
    other pure presentation helpers below.
    """
    return (active_name,
            getattr(monitor, "_round", -1) if monitor is not None else -1,
            selected_ttl, theme, geo_count)


# --- CSV hardening ---------------------------------------------------------
_CSV_TRIGGERS = ("=", "+", "-", "@")


def csv_safe(value: str) -> str:
    """Neutralise spreadsheet formula injection (CWE-1236) in a CSV cell.

    Reverse-DNS hostnames are attacker-influenced — a hop's PTR record can be
    anything its operator chooses — and a saved session's address/hostname come
    from a file that may not be ours. A value like ``=cmd|'/c calc'!A1`` (or one
    starting ``+ - @``) is run as a formula when the export is opened in
    Excel/Sheets. Prefix any such cell with a single quote, the conventional
    defang; plain values (IPv4 addresses, normal hostnames) pass through
    untouched. ``csv.writer`` quoting handles delimiters but not this. Leading
    whitespace is defanged too (some importers strip it before evaluating)."""
    if value and (value[0] in _CSV_TRIGGERS or value[0].isspace()):
        return "'" + value
    return value
