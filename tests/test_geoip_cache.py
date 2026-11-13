"""Router lookups: whose network, kept on disk, polite to the service.
urlopen is faked; nothing goes to the network."""
import json
import time
import urllib.error

import pytest

from pingerplot import geoip
from pingerplot.geoip import GeoInfo, GeoResolver

PUBLIC = "8.8.8.8"          # a well-known public resolver: the lookup is faked


def _reply(monkeypatch, payload):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return json.dumps(payload).encode()
    monkeypatch.setattr(geoip.urllib.request, "urlopen", lambda req, timeout=0: _Resp())


def test_the_network_owner_comes_with_the_place(monkeypatch):
    _reply(monkeypatch, {"success": True, "latitude": 41.9, "longitude": -87.6, "city": "Chicago",
                         "region": "Illinois", "country": "United States",
                         "connection": {"asn": 64500, "org": "Example Transit", "isp": "Example ISP"}})
    info = GeoResolver()._lookup(PUBLIC)
    assert info.asn == 64500 and info.org == "Example Transit"
    assert info.network() == "AS64500 Example Transit"
    assert info.place() == "Chicago, Illinois, United States"


def test_a_missing_or_odd_asn_is_just_absent(monkeypatch):
    _reply(monkeypatch, {"success": True, "latitude": 1, "longitude": 2,
                         "connection": {"asn": "n/a", "isp": "Some ISP"}})
    info = GeoResolver()._lookup(PUBLIC)
    assert info.asn is None and info.network() == "Some ISP"


def test_answers_survive_a_restart_and_age_out(tmp_path):
    path = tmp_path / geoip.CACHE_NAME
    now = [1_700_000_000.0]
    r = GeoResolver(path, clock=lambda: now[0])
    r.cache[PUBLIC] = GeoInfo(1.0, 2.0, "Chicago", "IL", "US", "ISP", 64500, "Org")
    r._looked_up[PUBLIC] = now[0]
    r.cache["1.1.1.1"] = None                       # looked up: no location
    r._looked_up["1.1.1.1"] = now[0]
    assert r.save()

    again = GeoResolver(path, clock=lambda: now[0] + 86400)
    assert again.get(PUBLIC).asn == 64500 and again.get(PUBLIC).city == "Chicago"
    assert "1.1.1.1" in again.cache and again.get("1.1.1.1") is None
    again.request(PUBLIC)
    assert again._q.empty(), "a router looked up yesterday is not asked again"

    month_later = GeoResolver(path, clock=lambda: now[0] + 31 * 86400)
    assert PUBLIC not in month_later.cache
    month_later.request(PUBLIC)
    assert month_later._q.qsize() == 1, "after 30 days it's asked again"


def test_a_damaged_cache_file_is_ignored(tmp_path):
    path = tmp_path / geoip.CACHE_NAME
    for junk in ("not json", "[]", '{"hosts": []}', '{"hosts": {"8.8.8.8": {"t": "x"}}}'):
        path.write_text(junk)
        assert GeoResolver(path).cache == {}


def test_too_many_requests_pauses_everything_for_an_hour(monkeypatch):
    def limited(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
    monkeypatch.setattr(geoip.urllib.request, "urlopen", limited)
    r = GeoResolver()
    with pytest.raises(geoip.RateLimited):
        r._lookup(PUBLIC)
    r.request(PUBLIC)
    r._process(r._q.get_nowait())
    assert r._held() and "slow down" in r.paused
    assert r._paused_until - time.monotonic() > 3500
    assert r._q.qsize() == 1, "the address waits its turn, it isn't dropped"


def test_the_daily_cap(monkeypatch):
    r = GeoResolver()
    assert not r._held()
    r._today = geoip._DAILY_CAP
    assert r._held() and "tomorrow" in r.paused
    r._day = "1999-01-01"                           # a new day
    assert not r._held()


def test_nothing_private_is_ever_queued():
    r = GeoResolver()
    for ip in ("10.0.0.1", "100.64.0.1", "192.168.1.1", "172.16.5.4"):
        r.request(ip)
    assert r._q.empty()
