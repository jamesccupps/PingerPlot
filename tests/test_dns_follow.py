"""A hostname target is looked up again every few minutes and followed when
it moves: the Microsoft-style "the service sent me to another front end"."""
import time
from concurrent.futures import ThreadPoolExecutor

from pingerplot import icmp, monitor as monitor_mod
from pingerplot.history import Reader, Recorder
from pingerplot.monitor import Monitor

OLD, NEW, OTHER = "203.0.113.9", "203.0.113.20", "203.0.113.30"
ROUTER = "198.51.100.1"


def _monitor(monkeypatch, target="svc.example.com", recorder=None):
    m = Monitor(recorder=recorder)
    m.target_input = target
    m.target_ip = OLD
    m.running = True
    m._generation = 1
    m.reached_target = True
    m.max_hops = 8
    m._dns_pool = ThreadPoolExecutor(1)
    monkeypatch.setattr(m, "_maybe_resolve", lambda *a: None)
    if recorder is not None:
        m._rec_runs[1] = recorder.begin_run(target, "icmp", None, {})
        recorder.set_target_ip(m._rec_runs[1], OLD)
    return m


def _lookup(m, route_len=3):
    """Run one re-check to completion, as the worker's loop would."""
    m._dns_next = 0.0
    route_len = m._check_dns(route_len, gen=1)          # submits the lookup
    deadline = time.monotonic() + 5
    while m._dns_check is not None and not m._dns_check.done() and time.monotonic() < deadline:
        time.sleep(0.01)
    return m._check_dns(route_len, gen=1)               # acts on the answer


def _events(m):
    return [(e.kind, e.text) for e in m.events_after(-1)[0]]


def _route_to(addr):
    def probe(ttl, ip_ttl=None):
        if ttl == 2:
            return icmp.PingResult(icmp.IP_SUCCESS, 9.0, addr, True)
        return icmp.PingResult(icmp.IP_TTL_EXPIRED_TRANSIT, 5.0, ROUTER, False)
    return probe


def test_round_robin_rotation_is_not_a_move(monkeypatch):
    m = _monitor(monkeypatch)
    monkeypatch.setattr(monitor_mod, "_addresses_of", lambda name: [OTHER, OLD])
    assert _lookup(m) == 3
    assert m.target_ip == OLD and _events(m) == []
    m.shutdown()


def test_a_move_is_logged_followed_and_recorded_as_a_new_run(tmp_path, monkeypatch):
    rec = Recorder(tmp_path).start()
    m = _monitor(monkeypatch, recorder=rec)
    with m._lock:
        m._ensure_hop(1)
        m._ensure_hop(2)
    monkeypatch.setattr(monitor_mod, "_addresses_of", lambda name: [NEW, OTHER])
    monkeypatch.setattr(m, "_do_probe", _route_to(NEW))

    route_len = _lookup(m)

    assert m.target_ip == NEW and route_len == 2
    assert m._hops[-1].address == NEW, "the route to the new address was traced"
    (kind, text), = _events(m)
    assert kind == "route"
    assert text == f"svc.example.com now resolves to {NEW}, {OTHER} (was {OLD}); following it to {NEW}"
    rec.end_run(m._rec_runs[1])
    m.shutdown()
    rec.close()
    runs = Reader(tmp_path).runs("svc.example.com")
    assert [r["target_ip"] for r in runs] == [OLD, NEW]
    assert all(r["ended"] is not None for r in runs)


def test_a_failing_lookup_warns_once_and_says_when_it_recovers(monkeypatch):
    m = _monitor(monkeypatch)

    def fails(name):
        raise OSError(11002, "temporary failure in name resolution")
    monkeypatch.setattr(monitor_mod, "_addresses_of", fails)
    for _ in range(3):
        assert _lookup(m) == 3
    monkeypatch.setattr(monitor_mod, "_addresses_of", lambda name: [OLD])
    _lookup(m)
    kinds = [k for k, _ in _events(m)]
    m.shutdown()
    assert kinds == ["warn", "info"]
    assert m.target_ip == OLD


def test_an_address_target_is_never_looked_up(monkeypatch):
    m = _monitor(monkeypatch, target=OLD)
    monkeypatch.setattr(monitor_mod, "_addresses_of",
                        lambda name: (_ for _ in ()).throw(AssertionError("looked up")))
    assert _lookup(m) == 3
    m.shutdown()


def test_the_first_recheck_waits_its_interval():
    m = Monitor()
    m._run = lambda gen: None
    m.start("svc.example.com", final_hop_only=True)
    try:
        assert m._dns_next - time.monotonic() > monitor_mod.DNS_RECHECK_S - 5
    finally:
        m.shutdown()
