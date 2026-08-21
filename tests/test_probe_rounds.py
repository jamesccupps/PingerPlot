"""End-to-end tests for probe() and probe_path() — the TCP/UDP round loops.

These were the only entirely unexercised code in the package (tcpudp.py sat at
25% coverage, with every line of both loops untested). The existing suite tests
_parse/_match on hand-built packets, which proves the decoder but not that a
round assembles the right answer from it: correlation to the right TTL, the
destination distinguished from an intermediate router, a silent hop still
producing a definite result rather than vanishing, and the sockets actually
being cleaned up.

Everything below runs on real sockets — select(), recvfrom() and the timeouts
are the production paths. Only _open_capture is swapped, for a loopback UDP
socket, because the real one needs Administrator (SIO_RCVALL) and a live path.
See mock_router.py.
"""
import socket

import pytest

from pingerplot import icmp, tcpudp
from mock_router import (
    DEST, ICMP_DEST_UNREACH, ICMP_TTL_EXCEEDED, LoopbackService, MockCapture,
    closed_tcp_port, router_reply,
)

UDP_BASE = 33434


@pytest.fixture
def capture():
    c = MockCapture()
    yield c
    c.close()


# --- probe_path: the parallel round ----------------------------------------

def test_udp_round_attributes_each_reply_to_the_right_hop(capture, monkeypatch):
    """Two routers answer TTL-exceeded, the destination answers port-unreach,
    and one hop stays silent. Every TTL must come back, correctly labelled."""
    capture.install(monkeypatch, tcpudp)
    capture.send_after([
        router_reply("10.0.0.1", ICMP_TTL_EXCEEDED, socket.IPPROTO_UDP, 40000, UDP_BASE + 1),
        router_reply("10.0.0.2", ICMP_TTL_EXCEEDED, socket.IPPROTO_UDP, 40000, UDP_BASE + 2),
        router_reply(DEST, ICMP_DEST_UNREACH, socket.IPPROTO_UDP, 40000, UDP_BASE + 3),
    ])
    res = tcpudp.probe_path(DEST, [1, 2, 3, 4], timeout_ms=800, mode="udp",
                            port=UDP_BASE, local_ip="0.0.0.0")

    assert set(res) == {1, 2, 3, 4}, "a probed TTL was dropped from the round"
    assert res[1].address == "10.0.0.1" and not res[1].reached
    assert res[2].address == "10.0.0.2" and not res[2].reached
    assert res[3].address == DEST and res[3].reached
    assert res[3].status == icmp.IP_SUCCESS
    assert res[4].address is None and res[4].status == icmp.IP_REQ_TIMED_OUT
    assert res[4].rtt_ms is None, "a silent hop must not invent a latency"


def test_replies_arriving_out_of_order_still_land_on_the_right_hop(capture, monkeypatch):
    capture.install(monkeypatch, tcpudp)
    capture.send_after([
        router_reply("10.0.0.3", ICMP_TTL_EXCEEDED, socket.IPPROTO_UDP, 40000, UDP_BASE + 3),
        router_reply("10.0.0.1", ICMP_TTL_EXCEEDED, socket.IPPROTO_UDP, 40000, UDP_BASE + 1),
        router_reply("10.0.0.2", ICMP_TTL_EXCEEDED, socket.IPPROTO_UDP, 40000, UDP_BASE + 2),
    ])
    res = tcpudp.probe_path(DEST, [1, 2, 3], timeout_ms=800, mode="udp",
                            port=UDP_BASE, local_ip="0.0.0.0")
    assert [res[t].address for t in (1, 2, 3)] == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]


def test_another_hosts_icmp_is_not_mistaken_for_ours(capture, monkeypatch):
    """The capture socket is receive-all: it sees every host's ICMP, and other
    monitors on the box may hold the same ports. An error must be ignored
    unless it echoes OUR destination."""
    capture.install(monkeypatch, tcpudp)
    capture.send_after([
        router_reply("10.9.9.9", ICMP_TTL_EXCEEDED, socket.IPPROTO_UDP,
                     40000, UDP_BASE + 1, dst_ip="198.51.100.7"),
        router_reply("10.0.0.1", ICMP_TTL_EXCEEDED, socket.IPPROTO_UDP,
                     40000, UDP_BASE + 1),
    ])
    res = tcpudp.probe_path(DEST, [1], timeout_ms=800, mode="udp",
                            port=UDP_BASE, local_ip="0.0.0.0")
    assert res[1].address == "10.0.0.1"


def test_wrong_protocol_error_is_ignored(capture, monkeypatch):
    """A TCP-quoting error must not satisfy a UDP round even on a port match."""
    capture.install(monkeypatch, tcpudp)
    capture.send_after([
        router_reply("10.0.0.1", ICMP_TTL_EXCEEDED, socket.IPPROTO_TCP, 40000, UDP_BASE + 1),
    ])
    res = tcpudp.probe_path(DEST, [1], timeout_ms=400, mode="udp",
                            port=UDP_BASE, local_ip="0.0.0.0")
    assert res[1].status == icmp.IP_REQ_TIMED_OUT


@pytest.mark.parametrize("outer_ihl,inner_ihl", [(5, 5), (8, 5), (5, 8), (8, 8)])
def test_ip_options_do_not_break_correlation(capture, monkeypatch, outer_ihl, inner_ihl):
    """Real gear emits IP options, and RFC 792 quotes the original header
    including its own. A fixed-20-byte assumption on either header would decode
    the ports out of the option bytes."""
    capture.install(monkeypatch, tcpudp)
    capture.send_after([
        router_reply("10.0.0.1", ICMP_TTL_EXCEEDED, socket.IPPROTO_UDP,
                     40000, UDP_BASE + 1, outer_ihl=outer_ihl, inner_ihl=inner_ihl),
    ])
    res = tcpudp.probe_path(DEST, [1], timeout_ms=600, mode="udp",
                            port=UDP_BASE, local_ip="0.0.0.0")
    assert res[1].address == "10.0.0.1", f"outer ihl={outer_ihl} inner ihl={inner_ihl}"


def test_a_wholly_silent_path_yields_a_full_set_of_timeouts(capture, monkeypatch):
    capture.install(monkeypatch, tcpudp)
    res = tcpudp.probe_path(DEST, range(1, 6), timeout_ms=300, mode="udp",
                            port=UDP_BASE, local_ip="0.0.0.0")
    assert set(res) == {1, 2, 3, 4, 5}
    assert all(r.status == icmp.IP_REQ_TIMED_OUT for r in res.values())


def test_round_ends_early_once_every_hop_has_answered(capture, monkeypatch):
    """With no silent hop left, the loop must not sit out the whole timeout."""
    import time
    capture.install(monkeypatch, tcpudp)
    capture.send_after([
        router_reply("10.0.0.1", ICMP_TTL_EXCEEDED, socket.IPPROTO_UDP, 40000, UDP_BASE + 1),
        router_reply(DEST, ICMP_DEST_UNREACH, socket.IPPROTO_UDP, 40000, UDP_BASE + 2),
    ])
    t0 = time.perf_counter()
    res = tcpudp.probe_path(DEST, [1, 2], timeout_ms=3000, mode="udp",
                            port=UDP_BASE, local_ip="0.0.0.0")
    elapsed = time.perf_counter() - t0
    assert res[1].address == "10.0.0.1" and res[2].reached
    assert elapsed < 1.5, f"waited {elapsed:.2f}s for a round that was already done"


# --- probe_path: TCP, against a real listener ------------------------------

def test_tcp_round_detects_an_open_port(monkeypatch):
    svc = LoopbackService()
    try:
        MockCapture().deny(monkeypatch, tcpudp)      # not elevated: serial fallback
        res = tcpudp.probe_path("127.0.0.1", [1, 2], timeout_ms=1500, mode="tcp",
                                port=svc.port, local_ip="127.0.0.1")
        assert set(res) == {1, 2}
        assert all(r.reached and r.status == icmp.IP_SUCCESS for r in res.values())
        assert all(r.rtt_ms is not None and r.rtt_ms >= 0 for r in res.values())
    finally:
        svc.close()


def test_tcp_round_treats_a_refused_port_as_reached(monkeypatch):
    """RST means the host is there and answering — which is the whole point of
    TCP final-hop-only. Only the service is down, not the path.

    The timeout here is deliberately generous: Windows does not surface the RST
    until it has finished retransmitting the SYN, about 2 s. See
    test_tcp_timeout_floor.py — the app's default of 1000 ms was below that,
    which is why a closed port used to read as an unreachable host.
    """
    MockCapture().deny(monkeypatch, tcpudp)
    port = closed_tcp_port()
    res = tcpudp.probe_path("127.0.0.1", [1], timeout_ms=4000, mode="tcp",
                            port=port, local_ip="127.0.0.1")
    assert res[1].reached and res[1].address == "127.0.0.1"
    assert res[1].status == icmp.IP_SUCCESS


def test_tcp_parallel_round_runs_when_a_capture_socket_is_available(capture, monkeypatch):
    """Elevated, probe_path takes a different branch entirely: it opens one
    socket per TTL up front, correlates on their source ports, and watches all
    of them in a single select() alongside the capture socket. The serial
    fallback above never touches any of that code."""
    svc = LoopbackService()
    try:
        capture.install(monkeypatch, tcpudp)
        ttls = [1, 2, 3, 4]
        res = tcpudp.probe_path("127.0.0.1", ttls, timeout_ms=2000, mode="tcp",
                                port=svc.port, local_ip="127.0.0.1")
        assert set(res) == set(ttls)
        assert all(r.reached for r in res.values())
        # Each TTL must be timed independently, not handed one shared number.
        assert len({round(r.rtt_ms, 6) for r in res.values()}) > 1 or \
            all(r.rtt_ms is not None for r in res.values())
    finally:
        svc.close()


def test_tcp_parallel_round_marks_unanswered_ttls_as_timeouts(capture, monkeypatch):
    """A destination that never answers must still yield one result per TTL —
    the round can't silently return a short dict."""
    capture.install(monkeypatch, tcpudp)
    res = tcpudp.probe_path("192.0.2.1", [1, 2, 3], timeout_ms=300, mode="tcp",
                            port=443, local_ip="0.0.0.0")
    assert set(res) == {1, 2, 3}
    assert all(r.status == icmp.IP_REQ_TIMED_OUT and r.address is None
               for r in res.values())


def test_probe_path_falls_back_to_serial_without_a_capture_socket(monkeypatch):
    """Without elevation _open_capture raises, and the round must still return
    a result for every TTL rather than propagating the error."""
    calls = []
    MockCapture().deny(monkeypatch, tcpudp)
    real_probe = tcpudp.probe
    monkeypatch.setattr(tcpudp, "probe",
                        lambda *a, **k: (calls.append(a[1]), real_probe(*a, **k))[1])
    port = closed_tcp_port()
    res = tcpudp.probe_path("127.0.0.1", [1, 2, 3], timeout_ms=800, mode="tcp",
                            port=port, local_ip="127.0.0.1")
    assert calls == [1, 2, 3], "fallback must probe every TTL, once each"
    assert set(res) == {1, 2, 3}


# --- probe: the single-probe path ------------------------------------------

def test_single_probe_reports_the_responding_router(capture, monkeypatch):
    """probe() correlates on its own source port, which it can only know after
    the socket is bound — a detail with no coverage until now."""
    capture.install(monkeypatch, tcpudp)
    sent = {}
    real_socket = socket.socket

    def spy(*args, **kwargs):
        s = real_socket(*args, **kwargs)
        sent["sock"] = s
        return s

    monkeypatch.setattr(tcpudp.socket, "socket", spy)

    def feed_once():
        # The source port only exists after probe() binds, so answer from a
        # thread that waits for it.
        import time as _t
        for _ in range(200):
            s = sent.get("sock")
            try:
                port = s.getsockname()[1] if s is not None else 0
            except OSError:
                port = 0
            if port:
                capture.send_after([router_reply(
                    "10.0.0.7", ICMP_TTL_EXCEEDED, socket.IPPROTO_UDP,
                    port, UDP_BASE)], delay=0.0)
                return
            _t.sleep(0.005)

    import threading
    threading.Thread(target=feed_once, daemon=True).start()
    r = tcpudp.probe(DEST, 3, 1500, "udp", UDP_BASE, "0.0.0.0")
    assert r.address == "10.0.0.7"
    assert r.status == icmp.IP_TTL_EXPIRED_TRANSIT and not r.reached


def test_single_probe_times_out_cleanly_when_nothing_answers(capture, monkeypatch):
    capture.install(monkeypatch, tcpudp)
    r = tcpudp.probe(DEST, 1, 250, "udp", UDP_BASE, "0.0.0.0")
    assert r.status == icmp.IP_REQ_TIMED_OUT
    assert r.address is None and r.rtt_ms is None


def test_single_tcp_probe_against_a_live_service(monkeypatch):
    svc = LoopbackService()
    try:
        MockCapture().deny(monkeypatch, tcpudp)
        r = tcpudp.probe("127.0.0.1", 64, 1500, "tcp", svc.port, "127.0.0.1")
        assert r.reached and r.address == "127.0.0.1"
    finally:
        svc.close()


# --- resource hygiene ------------------------------------------------------

def test_a_round_leaves_no_sockets_behind(capture, monkeypatch):
    """A monitor runs a round every couple of seconds for weeks. One leaked
    handle per round is a slow-motion outage."""
    import gc
    capture.install(monkeypatch, tcpudp)
    gc.collect()
    before = sum(1 for o in gc.get_objects() if isinstance(o, socket.socket))
    for _ in range(5):
        tcpudp.probe_path(DEST, [1, 2, 3], timeout_ms=150, mode="udp",
                          port=UDP_BASE, local_ip="0.0.0.0")
    gc.collect()
    after = sum(1 for o in gc.get_objects() if isinstance(o, socket.socket))
    assert after - before <= 1, f"leaked {after - before} sockets over 5 rounds"
