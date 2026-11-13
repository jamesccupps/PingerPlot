"""Route changes worth reporting, told apart from load balancing.

Every round is a full traceroute, so which router answers at each hop is
known every few seconds. Until 1.5 any difference from the previous round was
logged as "Hop 7: path changed A -> B". On a path through routers that spread
traffic over parallel links (ECMP), hop 7 alternates between two routers for
as long as you watch, so that event fired every round or two and buried the
reroutes it existed to report.

The rule here: a router new to a hop is on probation. If a router already
known at that hop answers again before ``confirm`` answering rounds have
passed, the hop is load-balanced and the newcomer joins its known set (said
once). If only newcomers answer for ``confirm`` rounds in a row, the path
really moved: the hop's known set is replaced, and hops that moved in the
same round are reported together. Rounds in which a hop doesn't answer count
for neither, so a lossy hop can't fake a reroute or hide one.

Pure logic, no sockets and no Tk: the engine feeds it each round, and the
tests feed it scripted rounds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

CONFIRM_ROUNDS = 3


@dataclass(frozen=True)
class Change:
    kind: str                  # "reroute" | "balanced"
    ttls: List[int]
    before: List[List[str]] = field(default_factory=list)   # per ttl, sorted
    after: List[List[str]] = field(default_factory=list)

    @property
    def text(self) -> str:
        if self.kind == "balanced":
            (ttl,), (now,) = self.ttls, self.after
            return f"Hop {ttl} is load-balanced across {', '.join(now)}"
        parts = [f"{' / '.join(b) or '*'} -> {' / '.join(a)}" for b, a in zip(self.before, self.after)]
        if len(self.ttls) == 1:
            return f"Route changed at hop {self.ttls[0]}: {parts[0]}"
        detail = "; ".join(f"hop {t}: {p}" for t, p in zip(self.ttls, parts))
        return f"Route changed at hops {self.ttls[0]}-{self.ttls[-1]}: {detail}"


def compatible(a: List[List[str]], b: List[List[str]]) -> bool:
    """Whether two recorded routes are the same path: as long as each other,
    and at every hop either one side was silent or they share a router. Load
    balancing makes the sets grow over time; sharing a router is enough."""
    if len(a) != len(b):
        return False
    return all(not x or not y or set(x) & set(y) for x, y in zip(a, b))


class RouteTracker:
    def __init__(self, confirm: int = CONFIRM_ROUNDS) -> None:
        self.confirm = max(1, int(confirm))
        self.known: Dict[int, Set[str]] = {}
        self._pending: Dict[int, Set[str]] = {}   # ttl -> newcomers on probation
        self._streak: Dict[int, int] = {}         # ttl -> rounds only newcomers answered

    def reset(self) -> None:
        self.known.clear()
        self._pending.clear()
        self._streak.clear()

    def trim(self, route_len: int) -> None:
        """Forget hops past the end of a route that got shorter."""
        for d in (self.known, self._pending, self._streak):
            for ttl in [t for t in d if t > route_len]:
                del d[ttl]

    def signature(self, route_len: int) -> List[List[str]]:
        """The route as recorded: each hop's known routers, [] for silent."""
        return [sorted(self.known.get(ttl, ())) for ttl in range(1, route_len + 1)]

    def observe(self, responders: Dict[int, Optional[str]]) -> List[Change]:
        """One round's responders by TTL (None = no reply). Returns what
        changed: at most one reroute (the hops that moved this round,
        together) and a "balanced" note per hop found to be load-balanced."""
        moved: List[tuple] = []
        changes: List[Change] = []
        for ttl in sorted(responders):
            addr = responders[ttl]
            if not addr:
                continue
            known = self.known.get(ttl)
            if not known:
                # A hop that was silent (or never seen) starts answering:
                # that's discovery, not a change of path.
                self.known[ttl] = {addr}
                continue
            if addr in known:
                newcomers = self._pending.pop(ttl, None)
                self._streak.pop(ttl, None)
                if newcomers:
                    known |= newcomers
                    changes.append(Change("balanced", [ttl], after=[sorted(known)]))
                continue
            self._pending.setdefault(ttl, set()).add(addr)
            self._streak[ttl] = self._streak.get(ttl, 0) + 1
            if self._streak[ttl] >= self.confirm:
                before = sorted(known)
                self.known[ttl] = self._pending.pop(ttl)
                del self._streak[ttl]
                moved.append((ttl, before, sorted(self.known[ttl])))
        if moved:
            changes.insert(0, Change("reroute", [m[0] for m in moved],
                                     [m[1] for m in moved], [m[2] for m in moved]))
        return changes
