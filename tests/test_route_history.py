"""Reroutes in the engine and in the history, driven by scripted rounds (no
sockets): a load-balanced hop is said once, a real reroute once, and the
history keeps which route was in use when."""
import json

from pingerplot import icmp
from pingerplot.history import Reader, Recorder
from pingerplot.monitor import Monitor
from pingerplot.routes import CONFIRM_ROUNDS

DEST = "203.0.113.9"
R1, R2a, R2b, R3 = "198.51.100.1", "198.51.100.2", "198.51.100.22", "198.51.100.3"


def _ttl(addr):
    return icmp.PingResult(icmp.IP_TTL_EXPIRED_TRANSIT, 5.0, addr, False)


def _dest():
    return icmp.PingResult(icmp.IP_SUCCESS, 9.0, DEST, True)


def _monitor(monkeypatch, recorder=None):
    m = Monitor(recorder=recorder)
    m.target_input = "example.com"
    m.target_ip = DEST
    m.running = True
    m._generation = 1
    m.reached_target = True
    monkeypatch.setattr(m, "_maybe_resolve", lambda *a: None)
    if recorder is not None:
        m._rec_runs[1] = recorder.begin_run("example.com", "icmp", None, {})
        recorder.set_target_ip(m._rec_runs[1], DEST)
    return m


def _rounds(m, monkeypatch, paths):
    """Run one monitoring round per path (a list of hop-2 routers)."""
    for hop2 in paths:
        monkeypatch.setattr(m, "_gather_range", lambda lo, hi, gen=None, h=hop2: {
            1: _ttl(R1), 2: _ttl(h), 3: _dest()})
        m._probe_round(3, gen=1)
        m._record_route(3, gen=1)


def _events(m):
    return [(e.kind, e.text) for e in m.events_after(-1)[0]]


def test_a_load_balanced_hop_is_said_once_not_every_round(monkeypatch):
    m = _monitor(monkeypatch)
    _rounds(m, monkeypatch, [R2a if i % 2 else R2b for i in range(30)])
    events = _events(m)
    m.shutdown()
    assert [k for k, _ in events] == ["info"], events
    assert "load-balanced" in events[0][1] and R2a in events[0][1] and R2b in events[0][1]


def test_a_reroute_is_one_event_and_a_new_route_in_the_history(tmp_path, monkeypatch):
    rec = Recorder(tmp_path).start()
    m = _monitor(monkeypatch, rec)
    _rounds(m, monkeypatch, [R2a] * 5 + [R3] * (CONFIRM_ROUNDS + 5))
    events = _events(m)
    rec.end_run(m._rec_runs[1])
    m.shutdown()
    rec.close()

    assert events == [("route", f"Route changed at hop 2: {R2a} -> {R3}")]
    r = Reader(tmp_path)
    routes = [json.loads(row["hops"]) for row in r.routes("example.com")]
    assert routes == [[[R1], [R2a], [DEST]], [[R1], [R3], [DEST]]]
    segs = r.segments("example.com")
    assert [s["route"] for s in segs] == [1, 2]
    assert segs[0]["end"] == segs[1]["start"], "the first route ends where the second begins"
    assert segs[1]["end"] is not None


def test_a_route_that_comes_back_is_the_same_route(tmp_path, monkeypatch):
    rec = Recorder(tmp_path).start()
    m = _monitor(monkeypatch, rec)
    flip = CONFIRM_ROUNDS + 2
    _rounds(m, monkeypatch, [R2a] * flip + [R3] * flip + [R2a] * flip)
    rec.end_run(m._rec_runs[1])
    m.shutdown()
    rec.close()
    r = Reader(tmp_path)
    assert len(r.routes("example.com")) == 2
    assert [s["route"] for s in r.segments("example.com")] == [1, 2, 1]


def test_more_load_balancing_widens_the_route_instead_of_starting_a_new_one(tmp_path, monkeypatch):
    rec = Recorder(tmp_path).start()
    m = _monitor(monkeypatch, rec)
    _rounds(m, monkeypatch, [R2a] * 3 + [R2b, R2a] * 3)
    rec.end_run(m._rec_runs[1])
    m.shutdown()
    rec.close()
    r = Reader(tmp_path)
    (route,) = r.routes("example.com")
    assert json.loads(route["hops"]) == [[R1], sorted([R2a, R2b]), [DEST]]
    assert len(r.segments("example.com")) == 1


def test_destination_only_records_no_route(tmp_path, monkeypatch):
    rec = Recorder(tmp_path).start()
    m = _monitor(monkeypatch, rec)
    m.final_hop_only = True
    monkeypatch.setattr(m, "_do_probe", lambda ttl, ip_ttl=None: _dest())
    for _ in range(5):
        m._probe_round(1, gen=1)
        m._record_route(1, gen=1)
    m.shutdown()
    rec.close()
    assert Reader(tmp_path).routes("example.com") == []
