"""When an ICMP error aborts a TCP connect, the router's identity must win.

Both fire in the same select() wakeup: the capture socket becomes readable with
the ICMP error, and the probe socket lands in the exception set because that
same error aborted the connect. probe() and probe_path() both checked the
socket error first, so _tcp_reach classified it as a bare timeout and broke out
(or discarded the TTL from `pending`) before the capture socket was ever read.

The information is only in the captured packet. SO_ERROR says "that didn't
work"; the packet says *which box* said so. Losing it turns "hop 7, the border
firewall, is refusing to forward" into "hop 7: *" — which is precisely the
answer a traceroute exists to produce.

The trigger is narrow and I could not reproduce it on a live path: Windows TCP
ignores ICMP time-exceeded on a connecting socket, so the plausible cause is a
mid-path firewall's administratively-prohibited (type 3 code 13), which RFC
1122 does treat as a hard error. The ordering is verified here with a scripted
select() rather than claimed against real hardware.
"""
import select
import socket

import pytest

from pingerplot import icmp, tcpudp
from mock_router import (
    DEST, ICMP_DEST_UNREACH, ICMP_TTL_EXCEEDED, MockCapture, router_reply,
)


@pytest.fixture
def capture():
    c = MockCapture()
    yield c
    c.close()


class SimultaneousReady:
    """A select() that reports the capture socket readable AND every probe
    socket exceptional in the same wakeup — the exact race, made repeatable.

    It emits the router's reply, correlated to the probe sockets' real source
    ports, just before returning. Those ports are only knowable from inside the
    call, which is why the stub does it rather than the test body.
    """

    def __init__(self, capture, itype=ICMP_TTL_EXCEEDED, router="10.0.0.7",
                 proto=socket.IPPROTO_TCP):
        self.capture = capture
        self.itype = itype
        self.router = router
        self.proto = proto
        self.calls = 0
        self._real = select.select

    def __call__(self, rlist, wlist, xlist, timeout=None):
        self.calls += 1
        if self.calls == 1 and wlist:
            packets = []
            for s in wlist:
                try:
                    port = s.getsockname()[1]
                except OSError:
                    continue
                packets.append(router_reply(self.router, self.itype, self.proto,
                                            port, 443))
            self.capture.send_after(packets, delay=0.0)
            # Let the datagrams land before claiming the socket is readable.
            self._real([self.capture.sock], [], [], 0.3)
            return ([self.capture.sock], [], list(wlist))
        return self._real(rlist, wlist, xlist, timeout)


def test_single_probe_prefers_the_captured_router(capture, monkeypatch):
    capture.install(monkeypatch, tcpudp)
    stub = SimultaneousReady(capture)
    monkeypatch.setattr(tcpudp.select, "select", stub)

    r = tcpudp.probe(DEST, 7, 2000, "tcp", 443, "0.0.0.0")

    assert r.address == "10.0.0.7", "the responding router was thrown away"
    assert r.status == icmp.IP_TTL_EXPIRED_TRANSIT
    assert r.rtt_ms is not None


def test_parallel_round_prefers_the_captured_router(capture, monkeypatch):
    capture.install(monkeypatch, tcpudp)
    stub = SimultaneousReady(capture)
    monkeypatch.setattr(tcpudp.select, "select", stub)

    res = tcpudp.probe_path(DEST, [5, 6, 7], timeout_ms=2000, mode="tcp",
                            port=443, local_ip="0.0.0.0")

    assert set(res) == {5, 6, 7}
    named = [t for t, v in res.items() if v.address == "10.0.0.7"]
    assert named, f"no TTL kept the router's identity: {res}"


def test_a_blocking_firewall_is_still_reported_as_a_responding_hop(capture, monkeypatch):
    """An administratively-prohibited unreachable from a box that is not the
    target is a hop that answered, not the end of the path — the case most
    likely to produce this race in the field."""
    capture.install(monkeypatch, tcpudp)
    stub = SimultaneousReady(capture, itype=ICMP_DEST_UNREACH, router="10.0.0.99")
    monkeypatch.setattr(tcpudp.select, "select", stub)

    r = tcpudp.probe(DEST, 7, 2000, "tcp", 443, "0.0.0.0")

    assert r.address == "10.0.0.99"
    assert not r.reached, "a mid-path firewall is not the destination"
    assert r.status == icmp.IP_TTL_EXPIRED_TRANSIT


def test_the_socket_error_still_decides_when_nothing_was_captured(monkeypatch):
    """The reorder must not disable the socket-error path — it is the only
    signal in TCP final-hop-only mode, which runs with no capture socket."""
    from mock_router import LoopbackService
    svc = LoopbackService()
    try:
        MockCapture().deny(monkeypatch, tcpudp)
        r = tcpudp.probe("127.0.0.1", 64, 2000, "tcp", svc.port, "127.0.0.1")
        assert r.reached and r.address == "127.0.0.1"
    finally:
        svc.close()
