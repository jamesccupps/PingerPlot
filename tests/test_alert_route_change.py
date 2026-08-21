"""An alert must not outlive the hop it was raised against.

_active_alerts is keyed (ttl, kind), and _evaluate_alerts only ever evaluates
the *current* destination TTL. When a reroute changes the path length the old
destination's key is orphaned: nothing revisits it, so the red banner, the
"N active alerts" status suffix and the summary grid's flagged row all stayed
stuck for the rest of the run — reporting packet loss on a hop that no longer
exists while the real destination was perfectly healthy.

Route change plus degradation is the exact scenario this tool exists to catch,
so the display going permanently wrong at that moment is the worst possible
time for it.
"""
from pingerplot.model import Hop
from pingerplot.monitor import Monitor


def _monitor():
    m = Monitor()
    m.alert_enabled = True
    m.reached_target = True
    m.alert_loss_pct = 20.0
    m.alert_latency_ms = 0.0   # loss-focused
    m.alert_window = 10
    m.alert_sound = False
    return m


def _hop(ttl, rtts, addr="1.2.3.4"):
    h = Hop(ttl)
    for r in rtts:
        h.record(r, addr, 0 if r is not None else 11010)
    return h


def _failing(ttl):
    """One good probe (so the hop counts as ever-reached) then a lost window."""
    return _hop(ttl, [10.0] + [None] * 10)


def _healthy(ttl):
    return _hop(ttl, [10.0] * 12)


def test_alert_clears_when_the_route_shortens():
    m = _monitor()
    m._hops = [Hop(1), Hop(2), _failing(3)]
    m.route_len = 3
    m._evaluate_alerts(3)
    assert m.active_alerts(), "precondition: the ttl-3 destination should alert"

    # The destination now answers one hop closer, and it is healthy.
    m._hops = [Hop(1), _healthy(2)]
    m.route_len = 2
    m._evaluate_alerts(2)
    assert m.active_alerts() == [], "the ttl-3 alert outlived hop 3"
    m.shutdown()


def test_alert_clears_when_the_route_lengthens():
    m = _monitor()
    m._hops = [Hop(1), _failing(2)]
    m.route_len = 2
    m._evaluate_alerts(2)
    assert m.active_alerts()

    m._hops = [Hop(1), Hop(2), Hop(3), _healthy(4)]
    m.route_len = 4
    m._evaluate_alerts(4)
    assert m.active_alerts() == []
    m.shutdown()


def test_retiring_a_stale_alert_is_logged():
    """A raise is logged, so its clear must be too — otherwise the Events tab
    shows an alert that never ends."""
    m = _monitor()
    m._hops = [Hop(1), Hop(2), _failing(3)]
    m.route_len = 3
    m._evaluate_alerts(3)

    m._hops = [Hop(1), _healthy(2)]
    m.route_len = 2
    m._evaluate_alerts(2)

    events, _ = m.events_after(-1)
    kinds = [e.kind for e in events]
    assert "alert" in kinds and "clear" in kinds
    cleared = [e.text for e in events if e.kind == "clear"]
    assert any("Hop 3" in t for t in cleared)
    m.shutdown()


def test_a_still_valid_alert_survives_evaluation():
    """The retirement must be surgical: only keys for hops that are no longer
    the destination go, never the live one."""
    m = _monitor()
    m._hops = [Hop(1), Hop(2), _failing(3)]
    m.route_len = 3
    m._evaluate_alerts(3)
    first = m.active_alerts()
    m._evaluate_alerts(3)          # same destination, still failing
    assert m.active_alerts() == first
    m.shutdown()


def test_new_destination_raises_its_own_alert():
    """A shortened route whose new destination is ALSO failing must alert on
    the new hop number, not keep quoting the old one."""
    m = _monitor()
    m._hops = [Hop(1), Hop(2), _failing(3)]
    m.route_len = 3
    m._evaluate_alerts(3)

    m._hops = [Hop(1), _failing(2)]
    m.route_len = 2
    m._evaluate_alerts(2)

    active = m.active_alerts()
    assert len(active) == 1
    assert "Hop 2" in active[0] and "Hop 3" not in active[0]
    m.shutdown()


def test_stale_alert_goes_even_before_the_new_window_fills():
    """The new destination may not have enough history to judge yet. That is a
    reason to say nothing about it — not a reason to keep showing the old
    hop's alert, which _evaluate_alerts used to do by returning early."""
    m = _monitor()
    m._hops = [Hop(1), Hop(2), _failing(3)]
    m.route_len = 3
    m._evaluate_alerts(3)

    m._hops = [Hop(1), _hop(2, [10.0] * 3)]   # only 3 probes, window is 10
    m.route_len = 2
    m._evaluate_alerts(2)
    assert m.active_alerts() == []
    m.shutdown()
