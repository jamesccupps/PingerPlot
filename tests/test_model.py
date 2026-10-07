"""Unit tests for the pure data model / statistics (platform-independent)."""
import pytest

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


def test_jitter_is_the_mean_change_between_consecutive_replies():
    """Packet-to-packet variation (RFC 3550's idea, averaged rather than
    smoothed), which is what the MOS E-model's jitter term means and what
    PingPlotter reports. It used to be the population standard deviation."""
    h = Hop(1)
    _fill(h, [10.0, 10.0, 10.0])
    assert h.jitter == 0.0
    h2 = Hop(2)
    _fill(h2, [10.0, 20.0])
    assert h2.jitter == 10.0


def test_a_clean_latency_step_is_not_jitter():
    """The case standard deviation got wrong: a reroute that moves a steady
    20 ms path to a steady 60 ms one is a latency change, not jitter. Stddev
    scored it 20 ms of jitter for as long as the step sat in the window (and
    MOS charged twice that); consecutive differences see one 40 ms jump."""
    h = Hop(1)
    _fill(h, [20.0] * 10 + [60.0] * 10)
    assert h.jitter == pytest.approx(40.0 / 19)
    assert h.recent_stats(20)[2] == pytest.approx(40.0 / 19)


def test_alternating_latency_is_jitter():
    """And the converse: 10/30/10/30 has a stddev of only 10, but every packet
    is 20 ms off the last -- that is what a jitter buffer has to absorb."""
    h = Hop(1)
    _fill(h, [10.0, 30.0] * 5)
    assert h.jitter == 20.0


def test_jitter_skips_over_lost_probes():
    """A lost probe has no RTT to compare; the next reply is compared with the
    last one that arrived."""
    h = Hop(1)
    _fill(h, [10.0])
    h.record(None, None, 11010)
    _fill(h, [30.0])
    assert h.jitter == 20.0
    assert h.recent_stats(3)[2] == 20.0


def test_window_jitter_uses_only_the_window():
    h = Hop(1)
    _fill(h, [100.0, 10.0, 10.0, 10.0])
    assert h.recent_stats(3)[2] == 0.0      # the 100 -> 10 jump is outside it


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


def test_compute_matches_individual_properties():
    # the single-pass compute() (per-tick hot path) must agree with the
    # property-by-property values it replaces in HopView.of
    h = Hop(1)
    _fill(h, [10.0, 20.0, 30.0, 50.0])
    h.record(None, "10.0.0.1", 11010)          # include a loss
    sent, received, loss, current, avg, best, worst, jitter = h.compute()
    assert (sent, received, current, best, worst) == \
        (h.sent, h.received, h.current, h.best, h.worst)
    assert loss == pytest.approx(h.loss_pct)
    assert avg == pytest.approx(h.avg)
    assert jitter == pytest.approx(h.jitter)


def test_compute_empty_and_all_loss():
    assert Hop(1).compute() == (0, 0, 0.0, None, None, None, None, None)
    h = Hop(2)
    h.record(None, "10.0.0.1", 11010)
    h.record(None, "10.0.0.1", 11010)
    sent, received, loss, current, avg, best, worst, jitter = h.compute()
    assert (sent, received, loss, current, avg, best, worst, jitter) == \
        (2, 0, 100.0, None, None, None, None, None)
