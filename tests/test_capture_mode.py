"""The TCP/UDP capture socket: opened at the IP level, not promiscuous, and
taking only ICMP errors addressed to this computer (the 2026-11-05 review,
M5). The real socket needs Administrator; these fake the ioctl."""
import logging
import socket

import pytest

from pingerplot import tcpudp
from mock_router import (DEST, ICMP_TTL_EXCEEDED, LOCAL, MockCapture, UdpRound,
                         router_reply)

UDP_BASE = 33434


class _FakeRaw:
    """Stands in for the raw socket: records ioctl options, and can refuse one."""
    refuse = None
    made = []

    def __init__(self, *args):
        self.options, self.closed = [], False
        _FakeRaw.made.append(self)

    def bind(self, addr):
        self.addr = addr

    def ioctl(self, code, option):
        if option == self.refuse:
            raise OSError(10022, "An invalid argument was supplied")
        self.options.append(option)

    def setblocking(self, flag):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def raw(monkeypatch):
    _FakeRaw.made, _FakeRaw.refuse = [], None
    monkeypatch.setattr(tcpudp.socket, "SIO_RCVALL", 0x98000001, raising=False)
    monkeypatch.setattr(tcpudp.socket, "RCVALL_ON", 1, raising=False)
    monkeypatch.setattr(tcpudp.socket, "SOCK_RAW", getattr(socket, "SOCK_RAW", 3), raising=False)
    monkeypatch.setattr(tcpudp.socket, "socket", _FakeRaw)
    return _FakeRaw


def test_the_capture_is_ip_level_not_promiscuous(raw):
    s = tcpudp._open_capture(LOCAL)
    assert s.options == [3], "RCVALL_IPLEVEL, never RCVALL_ON (1), which is promiscuous"


def test_a_stack_without_ip_level_falls_back_and_says_so(raw, caplog):
    raw.refuse = 3
    with caplog.at_level(logging.WARNING, logger="pingerplot.tcpudp"):
        s = tcpudp._open_capture(LOCAL)
    assert s.options == [1]
    assert "IP-level capture refused" in caplog.text


def test_a_capture_that_cant_open_is_closed(raw, monkeypatch):
    class _Neither(_FakeRaw):
        def ioctl(self, code, option):
            raise OSError(10013, "access denied")
    monkeypatch.setattr(tcpudp.socket, "socket", _Neither)
    with pytest.raises(OSError):
        tcpudp._open_capture(LOCAL)
    assert raw.made[-1].closed, "the raw socket is closed, not leaked"


def test_an_error_addressed_to_another_host_is_not_ours():
    for_us = router_reply("10.0.0.1", ICMP_TTL_EXCEEDED, socket.IPPROTO_UDP, 50000, UDP_BASE)
    for_them = router_reply("10.0.0.1", ICMP_TTL_EXCEEDED, socket.IPPROTO_UDP, 50000, UDP_BASE,
                            to="192.0.2.50")
    assert tcpudp._match(for_us, 50000, UDP_BASE, DEST, "udp", LOCAL) == "ttl"
    assert tcpudp._match(for_them, 50000, UDP_BASE, DEST, "udp", LOCAL) is None
    assert tcpudp._match(for_them, 50000, UDP_BASE, DEST, "udp", "0.0.0.0") == "ttl", \
        "address not known: as before"


def test_a_round_ignores_errors_addressed_to_another_host(monkeypatch):
    capture = MockCapture()
    try:
        capture.install(monkeypatch, tcpudp)
        UdpRound(capture, monkeypatch, tcpudp).answer([
            (1, "10.0.0.1", ICMP_TTL_EXCEEDED, {"to": "192.0.2.50"}),
            (2, "10.0.0.2", ICMP_TTL_EXCEEDED)])
        res = tcpudp.probe_path(DEST, [1, 2], timeout_ms=600, mode="udp", port=UDP_BASE,
                                local_ip=LOCAL)
    finally:
        capture.close()
    assert res[1].address is None, "another host's error taken as hop 1's"
    assert res[2].address == "10.0.0.2"
