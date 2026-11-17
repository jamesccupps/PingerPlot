"""Where a router is: its name first, then the IP database, unless the round
trips show that the database's answer can't be right.

The IP database is good for homes and offices and poor for the backbone
(places.py says why): it puts a carrier's routers at its registered address,
and an anycast address where its block was registered, so 1.1.1.1 answering
in 3 ms "is" in Brisbane. A reply can't come from farther away than light in
fiber travels in half its round trip, so two routers are within half the sum
of their round trips of each other: each is that close to this computer (the
triangle inequality), wherever this computer is. A database answer farther
than that from a router whose name places it is impossible, and is left out:
no point on the map, a blank cell, and Details says why. It is never replaced
by a guess.

Physics can't rule out much near this computer: from Maine, any reply slower
than about 2 ms could come from Washington. So one judgement is added: a
router that answers within NEAR_MS of the hop just before or after it on the
same path, placed by its name, is a link away from it, and a database answer
more than FAR_KM from there is almost certainly a registered address. Only
when both round trips mean what they seem: inside an MPLS tunnel every
router reports the round trip of the tunnel's far end (Atlanta and San
Antonio both at 54 ms), and a router can be slow to answer. So the hop is
skipped when a later hop answers faster, and the named hop is used only when
its round trip is about the quickest seen for its city. Even then it isn't
proof (a reply can come back a shorter way than it went), so it too only
leaves the answer out.

Every place that shows a router's location gets it from a Locator, so the
tables, the map, Details, the CSV export and "where the trouble is" agree.
The round trips must come from this computer, and from one place: a laptop
carried across the country while recording could see a correct answer left
out, never a wrong one drawn.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from . import places

# Light in fiber covers about 204 km per millisecond (c / 1.47). Rounded
# down, so a doubt only ever favors the database.
FIBER_KM_PER_MS = 200.0
SLACK_KM = 150.0        # city-center coordinates and metro sprawl
# Windows reports whole milliseconds (IcmpSendEcho's RoundTripTime), "<1" as
# 0: a reading can be up to this much under the real round trip.
CLOCK_MS = 1.0
# The neighbor judgement: a symmetric 2 ms covers about 200 km of fiber, so
# 500 km leaves room for a reply that came back another way.
NEAR_MS = 2.0
NEAR_HOPS = 2       # how many links away the named hop may be
FAR_KM = 500.0


def distance_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lon) points."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def reach_km(a_ms: float, b_ms: float) -> float:
    """How far apart two routers can be, given each one's best round trip."""
    return (a_ms + b_ms + 2 * CLOCK_MS) / 2 * FIBER_KM_PER_MS + SLACK_KM


def _usable(ms) -> bool:
    return isinstance(ms, (int, float)) and math.isfinite(ms) and ms >= 0


@dataclass(frozen=True)
class Anchor:
    """A router placed by its name, with its best round trip from here."""
    lat: float
    lon: float
    best: float
    city: str
    router: str         # its name, for saying which router ruled an answer out

    @property
    def point(self) -> Tuple[float, float]:
        return self.lat, self.lon


def anchors(routers: Iterable[Tuple[Optional[str], Optional[float]]]) -> List[Anchor]:
    """The anchors among ``routers`` (``(hostname, best_ms)`` pairs): those
    whose names place them, the quickest in each city (it gives the tightest
    bound; the others add nothing), less any that contradict the rest."""
    best: dict = {}
    for host, ms in routers:
        if not _usable(ms):
            continue
        place = places.from_name(host)
        point = places.coords(place)
        if point is None:
            continue
        if place.city not in best or ms < best[place.city].best:
            best[place.city] = Anchor(point[0], point[1], float(ms), place.city, host or "")
    return _consistent(list(best.values()))


def _conflict(a: Anchor, b: Anchor) -> bool:
    return distance_km(a.point, b.point) > reach_km(a.best, b.best)


def _consistent(found: List[Anchor]) -> List[Anchor]:
    """Drop anchors that contradict other anchors. A name is a hint: one
    router named after the far end of a transatlantic link would otherwise
    rule out every correct answer near this computer. The one contradicting
    the most goes first, and when two contradict only each other there's no
    telling which is wrong, so both go."""
    keep = found
    while True:
        counts = [sum(_conflict(a, b) for b in keep if b is not a) for a in keep]
        worst = max(counts, default=0)
        if worst == 0:
            return keep
        keep = [a for a, n in zip(keep, counts) if n < worst]


def ruled_out(point: Tuple[float, float], best: Optional[float],
              found: Sequence[Anchor]) -> Optional[Anchor]:
    """The anchor that proves a router whose best round trip is ``best``
    can't be at ``point`` (the one it misses by most), or None."""
    if not _usable(best):
        return None
    worst, by = None, 0.0
    for a in found:
        over = distance_km(point, a.point) - reach_km(best, a.best)
        if over > by:
            worst, by = a, over
    return worst


def _ms(ms: float) -> str:
    return f"{ms:.0f}"


@dataclass(frozen=True)
class Where:
    """One router's location, and how it was decided."""
    place: Optional[places.Place]           # from its name
    info: object                            # the IP database's answer (GeoInfo), used or not
    point: Optional[Tuple[float, float]]    # where to draw it
    source: str                             # where the point came from: "name", "ip" or ""
    why: str = ""                           # why the database's answer was left out

    @property
    def text(self) -> str:
        """For a table cell."""
        if self.place is not None:
            return str(self.place)
        if self.source != "ip":
            return ""
        info = self.info
        if info.city and info.country:
            return f"{info.city}, {info.country}"
        return info.city or info.country

    @property
    def label(self) -> str:
        """For the map: the city alone."""
        if self.source == "name":
            return self.place.city
        if self.source == "ip":
            return self.info.city or self.info.country
        return ""


@dataclass(frozen=True)
class _Hop:
    address: Optional[str]
    hostname: Optional[str]
    best: Optional[float]


def path_of(views) -> List[Tuple[int, Optional[str], Optional[str], Optional[float]]]:
    """A path for a Locator from hop views: ``(ttl, address, hostname,
    best)``, where best is the current router's own best round trip."""
    return [(v.ttl, v.address, v.hostname, getattr(v, "best_here", None)) for v in views]


class Locator:
    """Locations for the routers of one path, checked against ``anchors``
    (from every path measured from here at about the same time). ``geo``
    is the IP database (``.get(address)``)."""

    def __init__(self, geo, found: Sequence[Anchor] = (), path=()) -> None:
        self.geo = geo
        self.anchors = list(found)
        self._quickest = {a.city: a.best for a in self.anchors}
        self._hops = {ttl: _Hop(addr, host, best) for ttl, addr, host, best in path}
        self._named: List[Tuple[int, Anchor]] = []      # the path's routers placed by name
        for ttl, _addr, host, best in sorted(path, key=lambda h: h[0]):
            place = places.from_name(host)
            point = places.coords(place)
            if point is not None and _usable(best):
                self._named.append((ttl, Anchor(point[0], point[1], float(best), place.city, host)))

    def where(self, address: Optional[str], hostname: Optional[str],
              best: Optional[float] = None, ttl: Optional[int] = None) -> Where:
        """``best`` is this router's own best round trip; given only ``ttl``,
        it's taken from the path when the path's router there is this one."""
        if best is None and ttl in self._hops and self._hops[ttl].address == address:
            best = self._hops[ttl].best
        place = places.from_name(hostname)
        info = self.geo.get(address) if address else None
        point = places.coords(place)
        if point is not None:
            return Where(place, info, point, "name")
        if info is None:
            return Where(place, None, None, "")
        why = self._doubt((info.lat, info.lon), info, best, ttl)
        if why:
            return Where(place, info, None, "", why)
        return Where(place, info, (info.lat, info.lon), "ip")

    def view(self, v) -> Where:
        """A hop view's router."""
        return self.where(v.address, v.hostname, getattr(v, "best_here", None), v.ttl)

    def _doubt(self, point, info, best, ttl) -> str:
        there = info.city or info.country or "that place"
        a = ruled_out(point, best, self.anchors)
        if a is not None:
            return (f"Not possible: it answers in {_ms(best)} ms and {a.router} ({a.city}, from its "
                    f"name) in {_ms(a.best)} ms, so the two are within about "
                    f"{reach_km(best, a.best):,.0f} km of each other, and {there} is "
                    f"{distance_km(point, a.point):,.0f} km from {a.city}.")
        if ttl is None or not _usable(best) or self._inflated(ttl, best):
            return ""
        for t, n in self._named:
            if not 0 < abs(t - ttl) <= NEAR_HOPS or self._inflated(t, n.best):
                continue
            if n.best > self._quickest.get(n.city, -math.inf) + NEAR_MS:
                continue        # slower than its city's quickest router: a tunnel or a slow answer
            far = distance_km(point, n.point)
            if abs(best - n.best) <= NEAR_MS and far > FAR_KM:
                return (f"Doubtful: it answers within {NEAR_MS:g} ms of hop {t}, {n.router} ({n.city}, "
                        f"from its name), so it's most likely a link away from it, and {there} is "
                        f"{far:,.0f} km from {n.city}.")
        return ""

    def _inflated(self, ttl: int, best: float) -> bool:
        """Whether a later hop on the path answers faster than ``best``: the
        router at ``ttl`` is inside an MPLS tunnel or slow to answer."""
        return any(t > ttl and _usable(h.best) and h.best < best - NEAR_MS for t, h in self._hops.items())
