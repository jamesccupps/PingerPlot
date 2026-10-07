"""The destination's alert window must only hold the destination's samples.

Stats are kept per TTL, and Hop.record adopts a new responding address while
keeping the samples it already had. That is the MTR model and is fine for the
hop table, but the alert reads the last N samples of the destination TTL, and
after a reroute those can belong to a router that used to sit at that TTL.

Reproduced on a namespace lab (you -> r1 -> r2 -> r3 -> dest, plus a spare r4):
after the path went 4 -> 5 -> 4 hops, the destination's 10-probe window held
6 samples from the router that had briefly been hop 4 -- rate-limited
TTL-exceeded replies, mostly lost -- and raised "30% packet loss" against a
destination that was answering every probe. The webhook fired with it.
"""
from pingerplot.model import Hop
from pingerplot.monitor import Monitor

ROUTER = "203.0.113.3"
DEST = "203.0.113.10"


def _monitor(window=10):
    m = Monitor()
    m.alert_enabled = True
    m.reached_target = True
    m.alert_loss_pct = 20.0
    m.alert_latency_ms = 0.0
    m.alert_window = window
    m.alert_sound = False
    return m


def _record(hop, rtts, addr):
    for r in rtts:
        hop.record(r, addr if r is not None else None, 0 if r is not None else 11010)


def test_router_samples_left_at_the_ttl_do_not_alert_on_the_destination():
    hop = Hop(4)
    # A router held TTL 4 while the path was 5 hops long; it rate-limits its
    # TTL-exceeded replies, so most of its probes read as lost.
    _record(hop, [5.0, None, None, None, 5.0, None, None, None], ROUTER)
    # The path is back to 4 hops and the destination answers every probe.
    _record(hop, [10.0] * 4, DEST)

    m = _monitor()
    m._hops = [Hop(1), Hop(2), Hop(3), hop]
    m.route_len = 4
    m._evaluate_alerts(4)
    assert m.active_alerts() == [], "router's losses were charged to the destination"

    _record(hop, [10.0] * 6, DEST)       # window now holds 10 destination samples
    m._evaluate_alerts(4)
    assert m.active_alerts() == []
    m.shutdown()


def test_a_genuinely_lossy_destination_still_alerts_after_a_responder_change():
    """The fix must not turn into "never alert after a reroute": losses after
    the destination took the TTL are the destination's."""
    hop = Hop(4)
    _record(hop, [5.0] * 8, ROUTER)
    _record(hop, [10.0] + [None, 10.0] * 5, DEST)   # 11 samples, 5 lost

    m = _monitor()
    m._hops = [Hop(1), Hop(2), Hop(3), hop]
    m.route_len = 4
    m._evaluate_alerts(4)
    active = m.active_alerts()
    assert len(active) == 1 and "50% packet loss" in active[0], active
    m.shutdown()


def test_since_change_counts_losses_but_resets_on_a_new_responder():
    hop = Hop(1)
    _record(hop, [1.0, None, 1.0], ROUTER)
    assert hop.since_change == 3
    _record(hop, [1.0], DEST)
    assert hop.since_change == 1
    _record(hop, [None, None], DEST)      # losses carry no address: no reset
    assert hop.since_change == 3
    _record(hop, [2.0], DEST)             # same responder again: no reset
    assert hop.since_change == 4


def test_a_loaded_session_counts_its_whole_history_as_current():
    """Session files carry no per-sample responder, so the honest reading is
    that everything belongs to the address the file names."""
    m = Monitor()
    m.load_dict({"hops": [{"ttl": 1, "address": DEST,
                           "samples": [[0, 1.0], [1, None], [2, 1.0]]}]})
    assert m._hops[0].since_change == 3
    m.shutdown()
