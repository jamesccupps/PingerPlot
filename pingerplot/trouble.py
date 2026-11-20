"""Where on the path the trouble is: which place adds the delay, and where the
loss starts that reaches the destination.

The rule is the one mtr users learn the hard way. Loss at a router in the
middle of the path is usually that router declining to answer pings it's
busy forwarding past, not lost traffic: if it were, every hop after it would
show it too. So a hop's loss only counts as far as it carries: the least
loss of that hop and every hop after it, destination included. Delay is
counted the same way, for the same reason: a router slow to answer its own
pings shows delay that the hops after it don't have. So a hop's latency
counts as the least average of that hop and every hop after it, and what a
hop adds is the step from the hop before; the steps add up to the last
answering hop's latency, so nothing is blamed for delay that never reaches
the destination.

A destination that never answers (one that drops pings, or a trace that
never reaches it) has no loss to carry, so none is placed: the last router
that answers is usually just rate-limiting its replies.

Hops are grouped by place (from the router's name or the IP database), so
"loss starts entering Denver, +31 ms in Chicago" reads straight off the
result. The two witnesses name one metro differently ("New York" from a
router's name, "New York City" from the database), so places within
MERGE_KM of each other are one row, under the first one's name. No Tk: the
map draws it, the tests check it, a report can print it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple, Union

from .locating import distance_km

MERGE_KM = 25.0
NOTHING = "Nothing on the path stands out."


@dataclass
class PlaceRow:
    place: str
    ttls: List[int] = field(default_factory=list)
    delay_added: float = 0.0            # ms added by the links into this place's hops
    loss_starts: float = 0.0            # % of probes lost from here to the destination
    point: Optional[Tuple[float, float]] = None

    @property
    def score(self) -> float:
        # Loss outranks delay: 1% of lost packets matters more than 1 ms.
        return self.loss_starts * 10 + self.delay_added


def carried(values: Sequence[float]) -> List[float]:
    """Each hop's loss or latency, counted only as far as it reaches: the
    least of that hop's and every later hop's."""
    out: List[float] = []
    floor = float("inf")
    for value in reversed(values):
        floor = min(floor, value)
        out.append(floor)
    return list(reversed(out))


carried_loss = carried


def destination_silent(hops: Sequence) -> bool:
    """Whether the last hop (the destination, or the last one probed) never
    answered."""
    return bool(hops) and hops[-1].avg is None


Place = Union[None, str, Tuple[str, Optional[Tuple[float, float]]]]


def rank(hops: Sequence, locate: Callable[[object], Place]) -> List[PlaceRow]:
    """``hops`` in path order, each with ``ttl``, ``loss_pct``, ``avg`` (None
    for a hop that never answered) and whatever ``locate`` needs, which gives
    a hop's place as text or as ``(text, (lat, lon))``. Returns one row per
    place, worst first; hops with no place are left out (they still count
    for the hops around them). With the destination silent, no place gets
    any loss."""
    answered = [h for h in hops if h.avg is not None]
    if not answered:
        return []
    silent = destination_silent(hops)
    ttls = [h.ttl for h in answered]
    loss = dict(zip(ttls, carried([h.loss_pct for h in answered])))
    delay = dict(zip(ttls, carried([max(0.0, h.avg) for h in answered])))
    rows: dict = {}
    prev_delay = prev_loss = 0.0
    for h in answered:
        place, point = _place(locate(h))
        if place:
            row = rows.get(place)
            if row is None and point is not None:
                row = next((r for r in rows.values()
                            if r.point is not None and distance_km(r.point, point) <= MERGE_KM), None)
            if row is None:
                row = rows[place] = PlaceRow(place, point=point)
            row.ttls.append(h.ttl)
            row.delay_added += delay[h.ttl] - prev_delay
            row.loss_starts += 0.0 if silent else max(0.0, loss[h.ttl] - prev_loss)
        prev_delay, prev_loss = delay[h.ttl], loss[h.ttl]
    return sorted(rows.values(), key=lambda r: -r.score)


def _place(found: Place) -> Tuple[Optional[str], Optional[Tuple[float, float]]]:
    if isinstance(found, tuple):
        return found[0], found[1]
    return found, None


def verdict(rows: Sequence[PlaceRow], silent: bool = False) -> str:
    """One line for the top of the list, or "" when there's nothing to rank.
    ``silent``: the destination never answered (destination_silent)."""
    if not rows:
        return ""
    lossy = max(rows, key=lambda r: r.loss_starts)
    slow = max(rows, key=lambda r: r.delay_added)
    parts = []
    if silent:
        parts.append("the destination doesn't answer, so where loss starts can't be told")
    if lossy.loss_starts >= 1.0:
        parts.append(f"loss reaching the destination starts in {lossy.place} "
                     f"(hop {lossy.ttls[0]}, {lossy.loss_starts:.0f}%)")
    if slow.delay_added >= 10.0:
        parts.append(f"the most delay is added in {slow.place} (+{slow.delay_added:.0f} ms)")
    if not parts:
        return NOTHING
    return parts[0][0].upper() + parts[0][1:] + ("; " + parts[1] if len(parts) > 1 else "") + "."
