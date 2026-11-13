"""Where on the path the trouble is: which place adds the delay, and where the
loss starts that reaches the destination.

The rule is the one mtr users learn the hard way. Loss at a router in the
middle of the path is usually that router declining to answer pings it's
busy forwarding past, not lost traffic: if it were, every hop after it would
show it too. So a hop's loss only counts as far as it carries: the least
loss of that hop and every hop after it, destination included. Delay is the
same idea in a gentler form: a hop's latency minus the latency of the hop
before it that answered is what that hop's link added (a negative step is
the earlier hop answering slowly, and counts as nothing).

Hops are grouped by place (from the router's name or the IP database), so
"loss starts entering Denver, +31 ms in Chicago" reads straight off the
result. No Tk: the map draws it, the tests check it, a report can print it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence


@dataclass
class PlaceRow:
    place: str
    ttls: List[int] = field(default_factory=list)
    delay_added: float = 0.0            # ms added by the links into this place's hops
    loss_starts: float = 0.0            # % of probes lost from here to the destination

    @property
    def score(self) -> float:
        # Loss outranks delay: 1% of lost packets matters more than 1 ms.
        return self.loss_starts * 10 + self.delay_added


def carried_loss(losses: Sequence[float]) -> List[float]:
    """Each hop's loss, counted only as far as it reaches: the least loss of
    that hop and all after it."""
    out: List[float] = []
    floor = float("inf")
    for loss in reversed(losses):
        floor = min(floor, loss)
        out.append(floor)
    return list(reversed(out))


def rank(hops: Sequence, locate: Callable[[object], Optional[str]]) -> List[PlaceRow]:
    """``hops`` in path order, each with ``ttl``, ``loss_pct``, ``avg`` (None
    for a hop that never answered) and whatever ``locate`` needs. Returns one
    row per place, worst first; hops with no place are left out (they still
    count for the hops around them)."""
    answered = [h for h in hops if h.avg is not None]
    if not answered:
        return []
    carried = dict(zip((h.ttl for h in answered),
                       carried_loss([h.loss_pct for h in answered])))
    rows: dict = {}
    prev_avg: Optional[float] = None
    prev_carried = 0.0
    for h in answered:
        step = max(0.0, h.avg - prev_avg) if prev_avg is not None else max(0.0, h.avg)
        starts = max(0.0, carried[h.ttl] - prev_carried)
        place = locate(h)
        if place:
            row = rows.setdefault(place, PlaceRow(place))
            row.ttls.append(h.ttl)
            row.delay_added += step
            row.loss_starts += starts
        prev_avg, prev_carried = h.avg, carried[h.ttl]
    return sorted(rows.values(), key=lambda r: -r.score)


def verdict(rows: Sequence[PlaceRow]) -> str:
    """One line for the top of the list, or "" when nothing stands out."""
    if not rows:
        return ""
    lossy = max(rows, key=lambda r: r.loss_starts)
    slow = max(rows, key=lambda r: r.delay_added)
    parts = []
    if lossy.loss_starts >= 1.0:
        parts.append(f"loss reaching the destination starts in {lossy.place} "
                     f"(hop {lossy.ttls[0]}, {lossy.loss_starts:.0f}%)")
    if slow.delay_added >= 10.0:
        parts.append(f"the most delay is added in {slow.place} (+{slow.delay_added:.0f} ms)")
    if not parts:
        return "Nothing on the path stands out."
    return parts[0][0].upper() + parts[0][1:] + ("; " + parts[1] if len(parts) > 1 else "") + "."
