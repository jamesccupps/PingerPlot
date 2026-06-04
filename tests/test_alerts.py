"""Alert state-machine tests: drive Monitor._evaluate_alerts directly with
hand-built hops, so no sockets or threads are involved."""
from pingerplot.model import Hop
from pingerplot.monitor import Monitor


def _monitor():
    m = Monitor()
    m.alert_enabled = True
    m.reached_target = True
    m.alert_loss_pct = 20.0
    m.alert_latency_ms = 0.0  # latency alert off for these loss-focused tests
    m.alert_window = 10
    m.alert_sound = False     # don't beep during tests
    return m


def _dest(samples):
    """Build a single destination hop from a list of rtt values (None = loss)."""
    h = Hop(1)
    for r in samples:
        h.record(r, "1.2.3.4", 0 if r is not None else 11010)
    return h


def test_sustained_loss_raises_then_clears():
    m = _monitor()
    # one good probe (so the dest has 'ever responded'), then a full window lost
    m._hops = [_dest([10.0] + [None] * 10)]
    m.route_len = 1
    m._evaluate_alerts(1)
    assert any("loss" in a for a in m.active_alerts())

    # recovery: a full window of good probes clears it
    for _ in range(10):
        m._hops[0].record(10.0, "1.2.3.4", 0)
    m._evaluate_alerts(1)
    assert m.active_alerts() == []


def test_no_alert_before_window_fills():
    m = _monitor()
    m._hops = [_dest([None] * 5)]  # only 5 probes, window is 10
    m.route_len = 1
    m._evaluate_alerts(1)
    assert m.active_alerts() == []


def test_icmp_blocking_destination_does_not_alarm():
    m = _monitor()
    # never responded at all -> looks like a target that blocks ICMP, not a fault
    m._hops = [_dest([None] * 12)]
    m.route_len = 1
    m._evaluate_alerts(1)
    assert m.active_alerts() == []


def test_latency_alert():
    m = _monitor()
    m.alert_loss_pct = 0.0       # loss alert off
    m.alert_latency_ms = 200.0   # latency alert on
    m._hops = [_dest([500.0] * 10)]
    m.route_len = 1
    m._evaluate_alerts(1)
    assert any("ms" in a for a in m.active_alerts())


def test_disabled_alerts_clear_existing():
    m = _monitor()
    m._hops = [_dest([10.0] + [None] * 10)]
    m.route_len = 1
    m._evaluate_alerts(1)
    assert m.active_alerts()
    m.alert_enabled = False
    m._evaluate_alerts(1)
    assert m.active_alerts() == []
