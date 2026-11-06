"""start() caps the reply timeout and the alert window as well as raising
them: a 100 s timeout made every round (and closing the window) that slow,
and an alert window longer than the samples a hop keeps could never fill,
so every alert was silently off."""
from pingerplot import monitor
from pingerplot.model import DEFAULT_HISTORY
from pingerplot.monitor import Monitor


def _started(**opts):
    m = Monitor()
    m.start("127.0.0.1", interval=0.5, final_hop_only=True, **opts)
    m.shutdown()
    return m


def test_the_timeout_has_a_ceiling():
    assert _started(timeout_ms=100_000).timeout_ms == monitor.MAX_TIMEOUT_MS == 10_000
    assert _started(timeout_ms=50).timeout_ms == 100


def test_the_alert_window_fits_the_history():
    assert _started(alert_window=1000).alert_window == DEFAULT_HISTORY
    assert _started(alert_window=0).alert_window == 1
    assert _started(alert_window=20).alert_window == 20


def test_tcp_keeps_its_floor_under_the_ceiling():
    m = Monitor()
    m.start("127.0.0.1", interval=0.5, packet_type="tcp", final_hop_only=True, timeout_ms=500)
    m.shutdown()
    assert m.timeout_ms == monitor.TCP_REFUSAL_FLOOR_MS <= monitor.MAX_TIMEOUT_MS
