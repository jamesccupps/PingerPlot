"""Unit tests for the pure data model / statistics (platform-independent)."""
from pingerplot.model import Hop, HopView


def _fill(hop: Hop, rtts):
    for r in rtts:
        hop.record(r, "10.0.0.1", 11013)


def test_empty_hop_has_no_stats():
    h = Hop(1)
    assert h.sent == 0
    assert h.loss_pct == 0.0
    assert h.avg is None and h.best is None and h.worst is None and h.jitter is None


def test_basic_stats():
    h = Hop(1)
    _fill(h, [10.0, 20.0, 30.0])
    assert h.sent == 3
    assert h.received == 3
    assert h.loss_pct == 0.0
    assert h.best == 10.0
    assert h.worst == 30.0
    assert h.avg == 20.0
    assert h.current == 30.0


def test_loss_accounting():
    h = Hop(1)
    h.record(10.0, "10.0.0.1", 11013)
    h.record(None, None, 11010)  # lost probe in the middle
    h.record(30.0, "10.0.0.1", 11013)
    assert h.sent == 3
    assert h.received == 2
    assert round(h.loss_pct, 1) == 33.3
    assert h.avg == 20.0  # losses excluded from latency stats
    assert h.current == 30.0  # current tracks the most recent probe


def test_current_is_none_when_last_probe_lost():
    h = Hop(1)
    h.record(10.0, "10.0.0.1", 11013)
    h.record(None, None, 11010)  # most recent probe was lost
    assert h.current is None
    assert h.avg == 10.0


def test_jitter_is_population_stddev():
    h = Hop(1)
    _fill(h, [10.0, 10.0, 10.0])
    assert h.jitter == 0.0
    h2 = Hop(2)
    _fill(h2, [10.0, 20.0])
    assert h2.jitter == 5.0  # stddev of [10,20] about mean 15


def test_history_is_bounded():
    h = Hop(1, history=5)
    _fill(h, [float(i) for i in range(100)])
    assert h.sent == 5
    assert h.worst == 99.0
    assert h.best == 95.0


def test_address_change_triggers_rerresolve():
    h = Hop(1)
    h.record(10.0, "10.0.0.1", 11013)
    h.hostname = "router.local"
    h.record(10.0, "10.0.0.2", 11013)  # responder changed
    assert h.address == "10.0.0.2"
    assert h.hostname is None  # name invalidated


def test_hopview_snapshot_matches():
    h = Hop(3)
    _fill(h, [5.0, 15.0])
    v = HopView.of(h)
    assert v.ttl == 3
    assert v.address == "10.0.0.1"
    assert v.avg == 10.0
    assert v.sent == 2


def test_recent_window_stats():
    h = Hop(1)
    # 10 good probes, then the last 4 all lost
    _fill(h, [50.0] * 10)
    for _ in range(4):
        h.record(None, "10.0.0.1", 11010)
    # over the whole window, loss is 4/14
    assert round(h.recent_loss_pct(0), 1) == round(100 * 4 / 14, 1)
    # over just the last 4, it is total loss — the "sustained" view
    assert h.recent_loss_pct(4) == 100.0
    assert h.recent_received(4) == 0
    assert h.recent_avg(4) is None
    # the last 6 mix 2 good + 4 lost
    assert h.recent_received(6) == 2
    assert h.recent_avg(6) == 50.0


def test_recent_window_larger_than_history_uses_all():
    h = Hop(1)
    _fill(h, [10.0, 20.0])
    assert h.recent_loss_pct(1000) == 0.0
    assert h.recent_avg(1000) == 15.0
