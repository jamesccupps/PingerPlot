"""Tests for the route-discovery / grow / shrink logic, driven with monkeypatched
probe functions so no sockets, threads, or network are involved."""
from pingerplot import icmp
from pingerplot.monitor import UNREACHED_BEFORE_GROW, Monitor


def _ttl(addr, rtt=5.0):
    return icmp.PingResult(icmp.IP_TTL_EXPIRED_TRANSIT, rtt, addr, False)


def _dest(addr="1.2.3.4", rtt=7.0):
    return icmp.PingResult(icmp.IP_SUCCESS, rtt, addr, True)


def _timeout():
    return icmp.PingResult(icmp.IP_REQ_TIMED_OUT, None, None, False)


def _ready(monkeypatch):
    m = Monitor()
    m.target_ip = "1.2.3.4"
    m.running = True
    m._generation = 1
    monkeypatch.setattr(m, "_maybe_resolve", lambda *a: None)   # no reverse DNS
    return m


def test_trace_finds_route_and_trims_trailing_timeouts(monkeypatch):
    m = _ready(monkeypatch)
    m.max_hops = 10
    probes = {1: _ttl("10.0.0.1"), 2: _ttl("10.0.0.2"), 3: _dest()}
    monkeypatch.setattr(m, "_do_probe", lambda ttl, ip_ttl=None: probes.get(ttl, _timeout()))
    assert m._trace(gen=1) == 3
    assert len(m._hops) == 3 and m._hops[2].address == "1.2.3.4"
    m.shutdown()


def test_trace_returns_zero_on_total_silence(monkeypatch):
    m = _ready(monkeypatch)
    m.max_hops = 5
    monkeypatch.setattr(m, "_do_probe", lambda ttl, ip_ttl=None: _timeout())
    assert m._trace(gen=1) == 0
    m.shutdown()


def test_probe_round_shrinks_when_dest_answers_closer(monkeypatch):
    m = _ready(monkeypatch)
    m.reached_target = True
    for t in (1, 2, 3):
        m._ensure_hop(t)
    monkeypatch.setattr(m, "_gather_range", lambda lo, hi: {
        1: _ttl("10.0.0.1"), 2: _dest(), 3: _timeout()})
    assert m._probe_round(3, gen=1) == 2          # dest now at ttl 2
    assert len(m._hops) == 2
    m.shutdown()


def test_probe_round_grows_when_dest_moves_farther(monkeypatch):
    m = _ready(monkeypatch)
    m.reached_target = True
    m.max_hops = 10
    for t in (1, 2):
        m._ensure_hop(t)

    def gather(lo, hi):
        out = {}
        for t in range(lo, hi + 1):
            out[t] = _dest() if t == 4 else (_ttl(f"10.0.0.{t}") if t <= 2 else _timeout())
        return out

    monkeypatch.setattr(m, "_gather_range", gather)
    monkeypatch.setattr(m, "_do_probe", lambda ttl, ip_ttl=None: gather(ttl, ttl)[ttl])
    rl = 2
    for _ in range(UNREACHED_BEFORE_GROW):        # dest misses until we probe deeper
        rl = m._probe_round(rl, gen=1)
    assert rl == 4
    assert len(m._hops) == 4 and m._hops[3].address == "1.2.3.4"
    m.shutdown()
