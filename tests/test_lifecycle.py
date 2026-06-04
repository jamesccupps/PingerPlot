"""Regression tests for Monitor lifecycle hardening:
- C1: a probe from a superseded run (generation) is dropped (restart race)
- C2: thread pools are rebuilt after shutdown() (reuse), and a fresh Monitor
      allocates none until needed
- S2: a session declaring a huge sample list is clamped on load
All deterministic: no real sockets, threads, or network.
"""
from pingerplot import icmp, monitor
from pingerplot.monitor import MAX_LOAD_HISTORY, Monitor


def test_stale_generation_probe_is_dropped():
    m = Monitor()
    m._generation = 5
    ok = icmp.PingResult(icmp.IP_SUCCESS, 10.0, "1.2.3.4", True)

    m._apply_probe(1, ok, gen=4)          # a probe from the previous run
    assert m._hops == []                  # must not touch the new run's state
    assert m.reached_target is False

    m._apply_probe(1, ok, gen=5)          # current run applies normally
    assert len(m._hops) == 1 and m._hops[0].sent == 1
    assert m.reached_target is True
    m.shutdown()


def test_pools_rebuilt_after_shutdown():
    m = Monitor()
    assert m._ping_pool is None and m._dns_pool is None   # lazy: none until used
    m._ensure_pools()
    assert m._ping_pool is not None and m._dns_pool is not None
    assert m._ping_pool.submit(lambda: 7).result(timeout=2) == 7

    m.shutdown()
    assert m._ping_pool is None and m._dns_pool is None    # torn down

    m._ensure_pools()                                      # what start() does
    assert m._ping_pool.submit(lambda: 8).result(timeout=2) == 8
    m.shutdown()


def test_load_dict_caps_sample_history():
    big = MAX_LOAD_HISTORY + 5000
    data = {
        "hops": [{"ttl": 1, "address": "1.2.3.4", "hostname": "",
                  "samples": [[float(i), 1.0] for i in range(big)]}],
        "events": [],
    }
    m = Monitor()
    m.load_dict(data)
    assert len(m._hops) == 1
    assert m._hops[0].samples.maxlen == MAX_LOAD_HISTORY
    assert len(m._hops[0].samples) == MAX_LOAD_HISTORY     # truncated, not OOM
    m.shutdown()


def test_pause_resume_flags():
    m = Monitor()
    assert m.paused is False
    m.pause()
    assert m.paused is True
    m.resume()
    assert m.paused is False
    m.shutdown()


def test_probe_log_rotates_at_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "MAX_LOG_BYTES", 200)     # tiny cap to force a roll
    log = tmp_path / "probe.csv"
    m = Monitor()
    m.log_path = str(log)
    m._open_log()
    r = icmp.PingResult(icmp.IP_SUCCESS, 5.0, "1.2.3.4", True)
    for _ in range(60):
        m._log_probe(1, r)
    m._close_log()
    assert (tmp_path / "probe.1.csv").exists()   # rotated backup created
    assert log.exists()                          # fresh current log re-created
    assert log.stat().st_size < 5000             # current is small after the roll
    m.shutdown()


def test_send_webhook_rejects_non_http():
    assert monitor._send_webhook("file:///etc/passwd", {"x": 1}) is False
    assert monitor._send_webhook("ftp://host/p", {"x": 1}) is False
    assert monitor._send_webhook("", {"x": 1}) is False


def test_send_webhook_posts_json(monkeypatch):
    seen = {}

    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        seen["url"], seen["data"] = req.full_url, req.data
        return _Resp()

    monkeypatch.setattr(monitor.urllib.request, "urlopen", fake_urlopen)
    assert monitor._send_webhook("https://example.com/hook", {"event": "alert"}) is True
    import json as _json
    assert seen["url"] == "https://example.com/hook"
    assert _json.loads(seen["data"]) == {"event": "alert"}


def test_log_event_alert_fires_webhook(monkeypatch):
    import threading as _t
    fired, captured = _t.Event(), {}

    def fake_send(url, payload):
        captured["url"], captured["payload"] = url, payload
        fired.set()
        return True

    monkeypatch.setattr(monitor, "_send_webhook", fake_send)
    m = Monitor()
    m.webhook_url = "https://example.com/hook"
    m.target_input = "8.8.8.8"
    m._log_event("alert", "Hop 5: 30% loss")
    assert fired.wait(timeout=2)
    assert captured["url"] == "https://example.com/hook"
    assert captured["payload"]["event"] == "alert"
    assert captured["payload"]["text"] == "Hop 5: 30% loss"
    m.shutdown()
