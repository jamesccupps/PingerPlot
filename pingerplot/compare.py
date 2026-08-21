"""Compare a live run (or a saved session) against an earlier one.

The tool could always tell you what a path looks like *now*. It could not tell
you whether that is different from last Tuesday — which is the question people
actually arrive with. "Is it slower since the circuit was cut over?" "Did the
firewall change make hop 4 start dropping?" You had two exports and an eyeball.

Hops are keyed by TTL, because that is the only stable identity a traceroute
has. A TTL whose responding address changed is *not* the same router, so its
latency delta is meaningless — it gets its own verdict rather than being
reported as a regression. Conflating those two would produce the most
misleading output this module could emit: "hop 4 got 80 ms slower" when hop 4
is simply a different box now.

Pure standard library, no Tk, so the same comparison drives the GUI dialog, the
headless report, and the tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .model import DEFAULT_HISTORY, Hop, Sample, mos

# What counts as a real change rather than noise.
LOSS_DELTA_PTS = 5.0    # percentage points of packet loss
LATENCY_DELTA_PCT = 20.0  # relative latency change...
LATENCY_DELTA_MS = 5.0    # ...that must ALSO clear this, so 1.0 -> 1.3 ms is
                          # not reported as a 30% regression. Both must hold.
MOS_DELTA = 0.1           # MOS is shown to one decimal; a smaller move is noise

SAME = "same"
WORSE = "worse"
BETTER = "better"
REROUTED = "rerouted"   # this TTL is a different router now
NEW = "new"             # the path got longer; this TTL had no baseline
GONE = "gone"           # the path got shorter; this TTL is no longer probed


@dataclass(frozen=True)
class HopStats:
    """The subset of a hop's numbers a comparison needs."""

    ttl: int
    address: Optional[str]
    hostname: Optional[str]
    sent: int
    loss_pct: float
    avg: Optional[float]
    jitter: Optional[float]


@dataclass(frozen=True)
class HopDelta:
    ttl: int
    verdict: str
    base: Optional[HopStats]
    now: Optional[HopStats]

    @property
    def d_avg(self) -> Optional[float]:
        """Latency change in ms. None when either side has no measurement, or
        when the TTL is a different router and the number would be nonsense."""
        if self.verdict in (REROUTED, NEW, GONE):
            return None
        if self.base is None or self.now is None:
            return None
        if self.base.avg is None or self.now.avg is None:
            return None
        return self.now.avg - self.base.avg

    @property
    def d_loss(self) -> Optional[float]:
        if self.verdict in (REROUTED, NEW, GONE):
            return None
        if self.base is None or self.now is None:
            return None
        return self.now.loss_pct - self.base.loss_pct

    @property
    def address(self) -> Optional[str]:
        side = self.now or self.base
        return side.address if side else None


@dataclass(frozen=True)
class Comparison:
    target: str
    rows: List[HopDelta]
    base_hops: int
    now_hops: int
    base_mos: Optional[float]
    now_mos: Optional[float]

    @property
    def route_changed(self) -> bool:
        return self.base_hops != self.now_hops or any(
            r.verdict in (REROUTED, NEW, GONE) for r in self.rows)

    @property
    def regressions(self) -> List[HopDelta]:
        return [r for r in self.rows if r.verdict == WORSE]

    @property
    def improvements(self) -> List[HopDelta]:
        return [r for r in self.rows if r.verdict == BETTER]

    def summary(self) -> str:
        """One line for a status bar or the top of a report."""
        parts = []
        if self.base_hops != self.now_hops:
            parts.append(f"route {self.base_hops} -> {self.now_hops} hops")
        rerouted = [r for r in self.rows if r.verdict == REROUTED]
        if rerouted:
            parts.append(f"{len(rerouted)} hop{'s' if len(rerouted) != 1 else ''} rerouted")
        if self.regressions:
            parts.append(f"{len(self.regressions)} worse")
        if self.improvements:
            parts.append(f"{len(self.improvements)} better")
        # Only when it actually moved: "MOS 4.4 -> 4.4" is not information, and
        # a summary that always has something in it stops being read.
        if (self.base_mos is not None and self.now_mos is not None
                and abs(self.now_mos - self.base_mos) >= MOS_DELTA):
            parts.append(f"MOS {self.base_mos:.1f} -> {self.now_mos:.1f}")
        return "; ".join(parts) if parts else "no significant change"


def stats_from_hop(hop: Hop) -> HopStats:
    """Stats from a Hop the caller owns outright.

    Safe for a hop rebuilt from a saved session, which nothing else touches.
    NOT safe for a hop belonging to a running Monitor -- compute() iterates
    hop.samples, and iterating a deque another thread is appending to raises
    "deque mutated during iteration". Use stats_from_views(monitor.snapshot())
    for anything live.
    """
    sent, _recv, loss, _cur, avg, _best, _worst, jitter = hop.compute()
    return HopStats(hop.ttl, hop.address, hop.hostname, sent, loss, avg, jitter)


def stats_from_views(views) -> List[HopStats]:
    """Stats from a Monitor.snapshot(), which is taken under the lock.

    The safe way to summarise a monitor that is still probing, and the only
    construction both the GUI's compare dialog and headless's --baseline use.
    """
    return [HopStats(v.ttl, v.address, v.hostname, v.sent, v.loss_pct,
                     v.avg, v.jitter) for v in views]


def stats_from_session(data: dict) -> List[HopStats]:
    """Per-hop stats from a saved session dict (what Monitor.to_dict produces).

    Rebuilds Hop objects and reuses compute(), rather than re-deriving the
    statistics here, so a baseline and a live run can never be summarised by
    two subtly different formulas.
    """
    out: List[HopStats] = []
    for hd in (data.get("hops") or []):
        try:
            samples = hd.get("samples") or []
            hop = Hop(int(hd["ttl"]),
                      history=max(DEFAULT_HISTORY, len(samples)))
            hop.address = hd.get("address")
            hop.hostname = hd.get("hostname")
            for pair in samples:
                rtt = pair[1]
                hop.samples.append(
                    Sample(float(pair[0]), None if rtt is None else float(rtt)))
            out.append(stats_from_hop(hop))
        except (KeyError, ValueError, TypeError, IndexError):
            continue   # skip a malformed hop, same as load_dict
    return out


def _verdict(base: HopStats, now: HopStats) -> str:
    if base.address and now.address and base.address != now.address:
        return REROUTED
    d_loss = now.loss_pct - base.loss_pct
    if d_loss >= LOSS_DELTA_PTS:
        return WORSE
    if d_loss <= -LOSS_DELTA_PTS:
        return BETTER
    if base.avg is None or now.avg is None:
        return SAME
    d_ms = now.avg - base.avg
    if abs(d_ms) < LATENCY_DELTA_MS:
        return SAME
    # Relative as well as absolute, so a 6 ms move on a 300 ms satellite path
    # is not reported as a regression while the same 6 ms on a 2 ms LAN hop is.
    if base.avg > 0 and abs(d_ms) / base.avg * 100.0 < LATENCY_DELTA_PCT:
        return SAME
    return WORSE if d_ms > 0 else BETTER


def compare(baseline: List[HopStats], current: List[HopStats],
            target: str = "") -> Comparison:
    """Diff two runs of the same path, hop by hop."""
    by_ttl_base: Dict[int, HopStats] = {h.ttl: h for h in baseline}
    by_ttl_now: Dict[int, HopStats] = {h.ttl: h for h in current}
    rows: List[HopDelta] = []
    for ttl in sorted(set(by_ttl_base) | set(by_ttl_now)):
        b, n = by_ttl_base.get(ttl), by_ttl_now.get(ttl)
        if b is None:
            rows.append(HopDelta(ttl, NEW, None, n))
        elif n is None:
            rows.append(HopDelta(ttl, GONE, b, None))
        else:
            rows.append(HopDelta(ttl, _verdict(b, n), b, n))

    def _dest_mos(hops: List[HopStats]) -> Optional[float]:
        if not hops:
            return None
        d = hops[-1]
        return mos(d.avg, d.jitter, d.loss_pct)

    return Comparison(
        target=target,
        rows=rows,
        base_hops=len(baseline),
        now_hops=len(current),
        base_mos=_dest_mos(baseline),
        now_mos=_dest_mos(current),
    )


def _fmt(v: Optional[float], places: int = 1, width: int = 7) -> str:
    return f"{'-':>{width}}" if v is None else f"{v:>{width}.{places}f}"


def _fmt_delta(v: Optional[float], places: int = 1, width: int = 8) -> str:
    """Signed, so the direction is readable at a glance in a wall of numbers."""
    if v is None:
        return f"{'':>{width}}"
    return f"{v:>+{width}.{places}f}"


def format_comparison(cmp_: Comparison, baseline_label: str = "baseline") -> List[str]:
    """A text table of the diff, for the console report or a ticket.

    Deliberately ASCII-only. This goes to stdout, and a Windows console still
    defaults to cp1252 or cp437, neither of which has a delta sign — printing
    one raises UnicodeEncodeError and takes the whole report with it. The Tk
    dialog renders the same data and can use the nicer glyphs, because Tk is
    Unicode all the way down.
    """
    lines = [
        "",
        f"{cmp_.target or 'path'}: now vs {baseline_label}",
        f"  {cmp_.summary()}",
        f"  {'Hop':>3}  {'Address':<16} {'Loss%':>7}{'+/-':>8}  "
        f"{'Avg ms':>7}{'+/-':>8}  Verdict",
        "  " + "-" * 74,
    ]
    for r in cmp_.rows:
        side = r.now or r.base
        loss = side.loss_pct if side else None
        avg = side.avg if side else None
        note = r.verdict
        if r.verdict == REROUTED and r.base and r.now:
            note = f"rerouted {r.base.address} -> {r.now.address}"
        lines.append(
            f"  {r.ttl:>3}  {(r.address or '*'):<16} {_fmt(loss)}{_fmt_delta(r.d_loss)}  "
            f"{_fmt(avg)}{_fmt_delta(r.d_avg)}  {note}"
        )
    return lines
