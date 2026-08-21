"""Tests for the geoip helper: public/private classification and the resolver's
dedup/caching. urlopen is mocked — no real network."""
import json
import time

from pingerplot import geoip


def test_is_public_truth_table():
    for ip in ("8.8.8.8", "1.1.1.1", "9.9.9.9"):          # global unicast
        assert geoip._is_public(ip) is True, ip
    for ip in ("10.0.0.1", "192.168.1.1", "172.16.0.1",    # private
               "127.0.0.1", "169.254.1.1", "224.0.0.1",    # loopback/link-local/multicast
               "0.0.0.0", "::1", "not-an-ip", ""):          # unspecified/v6-loopback/junk
        assert geoip._is_public(ip) is False, ip


def test_request_skips_private_without_lookup():
    r = geoip.GeoResolver()
    r.request("10.0.0.1")                 # private -> cached None, never queued
    assert r.get("10.0.0.1") is None
    assert "10.0.0.1" in r._seen
    assert r._q.qsize() == 0


def test_request_dedups_public():
    r = geoip.GeoResolver()
    r.request("8.8.8.8")
    r.request("8.8.8.8")                  # same ip again -> not re-queued
    assert r._q.qsize() == 1


def test_lookup_parses_and_caches(monkeypatch):
    payload = {"success": True, "latitude": 37.75, "longitude": -122.4,
               "city": "Somewhere", "region": "CA", "country": "US",
               "connection": {"isp": "Example ISP"}}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(payload).encode("utf-8")

    seen = []

    def fake_urlopen(req, timeout=0):
        seen.append(req.full_url)
        return _Resp()

    monkeypatch.setattr(geoip.urllib.request, "urlopen", fake_urlopen)
    info = geoip.GeoResolver()._lookup("8.8.8.8")
    assert info is not None
    assert (info.lat, info.lon) == (37.75, -122.4)
    assert info.city == "Somewhere" and info.isp == "Example ISP"
    assert len(seen) == 1 and "8.8.8.8" in seen[0]


def test_lookup_swallows_network_error(monkeypatch):
    def boom(req, timeout=0):
        raise OSError("network down")
    monkeypatch.setattr(geoip.urllib.request, "urlopen", boom)
    assert geoip.GeoResolver()._lookup("8.8.8.8") is None


def test_lookup_rejects_unsuccessful_body(monkeypatch):
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"success": false}'
    monkeypatch.setattr(geoip.urllib.request, "urlopen", lambda req, timeout=0: _Resp())
    assert geoip.GeoResolver()._lookup("8.8.8.8") is None


def _body(monkeypatch, raw: bytes):
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, *a): return raw
    monkeypatch.setattr(geoip.urllib.request, "urlopen", lambda req, timeout=0: _Resp())


def test_lookup_rejects_a_body_that_is_valid_json_but_not_an_object(monkeypatch):
    """json.loads happily returns a list, a string or a number. A captive
    portal, a proxy error page or a CDN interstitial can all produce one, and
    data.get() on it raises AttributeError rather than returning falsey."""
    for raw in (b"[]", b'"nope"', b"42", b"null", b'[{"success": true}]'):
        _body(monkeypatch, raw)
        assert geoip.GeoResolver()._lookup("8.8.8.8") is None, raw


def test_a_malformed_body_does_not_kill_the_worker(monkeypatch):
    """The worker thread is started once and never restarted, so anything that
    escapes _lookup ends geolocation for the whole session -- with no error, no
    status change and no retry. The queue just fills up forever."""
    _body(monkeypatch, b"[]")
    r = geoip.GeoResolver()
    r._stop.wait = lambda *_a: None          # don't pay the politeness delay
    r.start()
    try:
        r.request("8.8.8.8")
        deadline = time.monotonic() + 5.0
        while "8.8.8.8" not in r.cache and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "8.8.8.8" in r.cache and r.cache["8.8.8.8"] is None
        assert r._thread.is_alive(), "worker died on a malformed reply"
        assert r.failures == 0, "a handled shape is not a crash"

        # And it must still serve the next address rather than sitting dead.
        _body(monkeypatch, json.dumps({"success": True, "latitude": 1.0,
                                       "longitude": 2.0}).encode())
        r.request("1.1.1.1")
        deadline = time.monotonic() + 5.0
        while r.get("1.1.1.1") is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert r.get("1.1.1.1") is not None, "worker stopped serving later lookups"
    finally:
        r.stop()


def test_an_unexpected_exception_is_counted_not_fatal(monkeypatch):
    """Belt and braces for the shape guard: whatever else a third party finds
    to do, the thread survives it and says so."""
    monkeypatch.setattr(geoip.GeoResolver, "_lookup",
                        lambda self, ip: (_ for _ in ()).throw(RuntimeError("surprise")))
    r = geoip.GeoResolver()
    r._stop.wait = lambda *_a: None
    r.start()
    try:
        r.request("8.8.8.8")
        deadline = time.monotonic() + 5.0
        while r.failures == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert r.failures == 1
        assert r._thread.is_alive()
        assert r.cache["8.8.8.8"] is None
    finally:
        r.stop()
