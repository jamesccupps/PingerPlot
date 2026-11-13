"""Reroutes, told apart from load balancing (routes.RouteTracker)."""
from pingerplot.routes import Change, RouteTracker, compatible

A, B, C, D = "198.51.100.1", "198.51.100.2", "198.51.100.3", "198.51.100.4"
DEST = "203.0.113.9"


def _feed(tracker, rounds):
    out = []
    for r in rounds:
        out.extend(tracker.observe(r))
    return out


def test_a_steady_path_reports_nothing():
    t = RouteTracker()
    assert _feed(t, [{1: A, 2: B, 3: DEST}] * 20) == []
    assert t.signature(3) == [[A], [B], [DEST]]


def test_alternating_routers_are_load_balancing_said_once():
    """The old engine logged "path changed" on every alternation."""
    t = RouteTracker()
    rounds = [{1: A, 2: B if i % 2 else C, 3: DEST} for i in range(40)]
    changes = _feed(t, rounds)
    assert changes == [Change("balanced", [2], after=[[B, C]])]
    assert t.signature(3) == [[A], [B, C], [DEST]]


def test_a_router_that_takes_over_and_stays_is_a_reroute():
    t = RouteTracker(confirm=3)
    changes = _feed(t, [{1: A, 2: B, 3: DEST}] * 5 + [{1: A, 2: C, 3: DEST}] * 2)
    assert changes == [], "not yet: two rounds could still be load balancing"
    (change,) = t.observe({1: A, 2: C, 3: DEST})
    assert change.kind == "reroute" and change.ttls == [2]
    assert change.before == [[B]] and change.after == [[C]]
    assert change.text == f"Route changed at hop 2: {B} -> {C}"
    assert t.signature(3) == [[A], [C], [DEST]]


def test_hops_that_move_together_are_one_change():
    t = RouteTracker(confirm=2)
    _feed(t, [{1: A, 2: B, 3: C, 4: DEST}] * 3)
    changes = _feed(t, [{1: A, 2: D, 3: "198.51.100.5", 4: DEST}] * 2)
    (change,) = changes
    assert change.ttls == [2, 3]
    assert change.text.startswith("Route changed at hops 2-3: hop 2: ")


def test_silent_rounds_count_for_neither():
    """A lossy hop must not turn one sighting of a newcomer into a reroute,
    nor hide a real one."""
    t = RouteTracker(confirm=3)
    _feed(t, [{1: A, 2: B}] * 3)
    rounds = [{1: A, 2: C}, {1: A, 2: None}, {1: A, 2: None}, {1: A, 2: C}]
    assert _feed(t, rounds) == []
    (change,) = t.observe({1: A, 2: C})
    assert change.kind == "reroute"


def test_a_silent_hop_starting_to_answer_is_not_a_change():
    t = RouteTracker()
    _feed(t, [{1: A, 2: None, 3: DEST}] * 5)
    assert _feed(t, [{1: A, 2: B, 3: DEST}] * 5) == []
    assert t.signature(3) == [[A], [B], [DEST]]


def test_trim_forgets_hops_past_a_shorter_route():
    t = RouteTracker()
    _feed(t, [{1: A, 2: B, 3: C, 4: DEST}] * 2)
    t.trim(2)
    assert t.signature(4) == [[A], [B], [], []]


def test_compatible_routes_share_a_router_at_every_answering_hop():
    assert compatible([[A], [B]], [[A], [B, C]])          # more balancing seen later
    assert compatible([[A], []], [[A], [B]])              # a silent hop matches anything
    assert not compatible([[A], [B]], [[A], [C]])
    assert not compatible([[A]], [[A], [B]])              # different length
