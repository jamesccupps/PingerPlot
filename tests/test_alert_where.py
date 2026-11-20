"""An alert says where on the path the trouble is (the Map's verdict), in its
event text and, as data, in the webhook payload. Places come from routers'
names only, so the headless runner says it too."""
import threading

from pingerplot import monitor as monmod
from pingerplot.model import Hop
from pingerplot.monitor import Monitor

DEST = "203.0.113.10"


def _record(hop, rtts, addr):
    for r in rtts:
        hop.record(r, addr if r is not None else None, 0 if r is not None else 11010)


def _lossy_path():
    """Boston fine; loss that reaches the destination starts in Chicago."""
    h1, h2, h3, h4 = Hop(1), Hop(2), Hop(3), Hop(4)
    _record(h1, [4.0] * 20, "198.51.100.1")
    _record(h2, ([30.0] * 3 + [None]) * 5, "198.51.100.2")       # 25% from here on
    _record(h3, ([31.0] * 3 + [None]) * 5, "198.51.100.3")
    _record(h4, ([32.0] * 3 + [None]) * 5, DEST)
    # Named once answering, as the resolver does (a new router clears the name).
    for h, name in ((h1, "ae1.cr1.bos1.example.net"), (h2, "ae2.cr2.chcgil09.example.net"),
                    (h3, "core3.example.net")):
        h.hostname = name
    return [h1, h2, h3, h4]


def _monitor():
    m = Monitor()
    m.alert_enabled, m.reached_target, m.alert_sound = True, True, False
    m.alert_loss_pct, m.alert_latency_ms, m.alert_window = 20.0, 0.0, 20
    m._hops = _lossy_path()
    m.route_len = 4
    return m


def test_the_alert_says_where_the_loss_starts():
    m = _monitor()
    m._evaluate_alerts(4)
    events, _last = m.events_after(-1)
    (alert,) = [e.text for e in events if e.kind == "alert"]
    assert alert.startswith("ALERT: Hop 4 (203.0.113.10): 25% packet loss")
    assert "Loss reaching the destination starts in Chicago, US (hop 2, 25%)" in alert
    m.shutdown()


def test_the_webhook_gets_the_places_as_data(monkeypatch):
    sent, done = [], threading.Event()

    def fake_send(url, payload):
        sent.append(payload)
        done.set()
    monkeypatch.setattr(monmod, "_send_webhook", fake_send)
    m = _monitor()
    m.webhook_url = "https://hooks.example.com/services/x"
    m._evaluate_alerts(4)
    assert done.wait(5)
    (payload,) = sent
    assert payload["event"] == "alert" and "Chicago, US" in payload["text"]
    chicago = next(p for p in payload["where"] if p["place"] == "Chicago, US")
    assert chicago == {"place": "Chicago, US", "hops": [2], "delay_added_ms": 26.0, "loss_starts_pct": 25.0}
    assert all(set(p) == {"place", "hops", "delay_added_ms", "loss_starts_pct"} for p in payload["where"])
    m.shutdown()


def test_a_router_without_a_city_in_its_name_stands_for_itself():
    m = _monitor()
    m._hops[1].hostname = "edge7.example.net"
    verdict, rows = m._where(4)
    assert "starts in edge7.example.net (hop 2, 25%)" in verdict
    m.shutdown()


def test_a_clear_carries_no_verdict(monkeypatch):
    m = _monitor()
    m._evaluate_alerts(4)
    for h in m._hops:
        _record(h, [5.0] * 20, h.address)
    m._evaluate_alerts(4)
    events, _last = m.events_after(-1)
    (clear,) = [e.text for e in events if e.kind == "clear"]
    assert clear.startswith("Cleared: Hop 4 (203.0.113.10): ") and "starts in" not in clear
    m.shutdown()
