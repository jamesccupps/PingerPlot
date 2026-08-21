"""Alerting on MOS, the number that actually says "voice will be bad".

MOS was computed, shown in the status bar and written into the CSV export, but
nothing could trigger on it. You could alert on loss and on latency separately
and still miss the combination that ruins a call: the E-model folds latency,
jitter and loss together, and a path can sit under every individual threshold
while scoring Poor — 30 ms of jitter and 4% loss at 90 ms is comfortably inside
"loss >= 20%" and "latency >= 250 ms", and is also unusable for voice.

Direction matters here and is the easy thing to get backwards: MOS runs 1.0 to
5.0 and *lower* is worse, so the alert fires at or below the threshold, unlike
loss and latency which fire at or above.
"""
import pytest

from pingerplot.model import Hop, mos
from pingerplot.monitor import Monitor


def _monitor(threshold=3.6):
    m = Monitor()
    m.alert_enabled = True
    m.reached_target = True
    m.alert_loss_pct = 0.0        # the other two off, so only MOS can fire
    m.alert_latency_ms = 0.0
    m.alert_mos = threshold
    m.alert_window = 10
    m.alert_sound = False
    return m


def _dest(rtts, ttl=1):
    h = Hop(ttl)
    for r in rtts:
        h.record(r, "1.2.3.4", 0 if r is not None else 11010)
    return h


def _run(m, hop):
    m._hops = [hop]
    m.route_len = 1
    m._evaluate_alerts(1)
    return m.active_alerts()


# --- the score itself ------------------------------------------------------

def test_a_clean_lan_path_scores_well():
    assert mos(5.0, 1.0, 0.0) >= 4.3


def test_a_bad_path_scores_poorly():
    assert mos(300.0, 60.0, 15.0) < 3.1


# --- firing and clearing ---------------------------------------------------

def test_a_healthy_path_does_not_alert():
    assert _run(_monitor(), _dest([8.0, 9.0] * 6)) == []


def test_a_degraded_path_alerts():
    alerts = _run(_monitor(), _dest([400.0, 90.0, None, 500.0, 120.0] * 3))
    assert any("MOS" in a for a in alerts)


def test_the_alert_names_the_score_and_its_label():
    alerts = _run(_monitor(), _dest([400.0, 90.0, None, 500.0, 120.0] * 3))
    text = next(a for a in alerts if "MOS" in a)
    assert "Hop 1" in text
    assert any(word in text for word in ("Bad", "Poor", "Fair"))


def test_it_clears_when_the_path_recovers():
    m = _monitor()
    hop = _dest([400.0, 90.0, None, 500.0, 120.0] * 3)
    assert _run(m, hop)
    for _ in range(10):
        hop.record(6.0, "1.2.3.4", 0)
    m._evaluate_alerts(1)
    assert m.active_alerts() == []
    m.shutdown()


def test_zero_disables_it():
    assert _run(_monitor(threshold=0.0),
                _dest([400.0, 90.0, None, 500.0, 120.0] * 3)) == []


def test_it_fires_below_the_threshold_not_above():
    """The direction trap: worse MOS is a *lower* number."""
    good = _dest([8.0, 9.0] * 6)
    bad = _dest([400.0, 90.0, None, 500.0, 120.0] * 3)
    assert _run(_monitor(threshold=3.6), good) == []
    assert _run(_monitor(threshold=3.6), bad) != []
    # A threshold of 5.0 means "alert unless the path is perfect".
    assert _run(_monitor(threshold=5.0), good) != []


# --- the case the other two thresholds miss --------------------------------

def test_it_catches_a_path_that_passes_loss_and_latency_thresholds():
    """The reason for the feature. Under both defaults, still bad for voice."""
    m = _monitor(threshold=4.0)
    m.alert_loss_pct = 20.0        # the shipped defaults, both enabled
    m.alert_latency_ms = 250.0
    hop = _dest([90.0, 150.0, 60.0, 180.0, None, 70.0, 200.0, 55.0, 165.0, 75.0])
    alerts = _run(m, hop)
    assert not any("packet loss" in a for a in alerts), "loss threshold should not fire"
    assert not any(" ms " in a for a in alerts), "latency threshold should not fire"
    assert any("MOS" in a for a in alerts), "but MOS should"
    m.shutdown()


# --- gating, same as the other alerts --------------------------------------

def test_no_alert_before_the_window_fills():
    m = _monitor()
    assert _run(m, _dest([400.0] * 4)) == []
    m.shutdown()


def test_a_destination_that_never_answered_does_not_alert():
    """Same gate as the loss alert: a target that blocks ICMP is not a fault,
    and it has no MOS to speak of anyway."""
    assert _run(_monitor(), _dest([None] * 12)) == []


def test_it_retires_with_the_others_when_the_route_moves():
    m = _monitor()
    m._hops = [Hop(1), Hop(2), _dest([400.0, 90.0, None, 500.0, 120.0] * 3, ttl=3)]
    m.route_len = 3
    m._evaluate_alerts(3)
    assert m.active_alerts()

    m._hops = [Hop(1), _dest([8.0, 9.0] * 6, ttl=2)]
    m.route_len = 2
    m._evaluate_alerts(2)
    assert m.active_alerts() == []
    m.shutdown()


# --- plumbing --------------------------------------------------------------

@pytest.mark.parametrize("given,expected", [
    (3.6, 3.6), (0, 0.0), (-1, 0.0), (9.9, 5.0), ("4.1", 4.1),
])
def test_start_clamps_the_threshold(given, expected):
    m = Monitor()
    m._run = lambda gen: None
    m.start("127.0.0.1", alert_mos=given)
    m.stop()
    try:
        assert m.alert_mos == expected
    finally:
        m.shutdown()


def test_headless_config_accepts_it():
    from pingerplot import headless
    _t, kw = headless._target_options({}, {"target": "8.8.8.8", "alert_mos": 3.9})
    assert kw["alert_mos"] == 3.9


def test_headless_defaults_it_off():
    from pingerplot import headless
    _t, kw = headless._target_options({}, {"target": "8.8.8.8"})
    assert kw["alert_mos"] == 0.0
