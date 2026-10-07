"""A POSIX probe must only accept replies to its own echo.

Linux keeps parallel probes apart for us: each SOCK_DGRAM ICMP socket gets a
kernel-assigned identifier and only receives replies carrying it, and router
errors arrive on that socket's own error queue. The code relied on that
("the kernel only delivers replies to echoes this socket sent").

macOS gives no such guarantee. Its ICMP datagram socket behaves like a raw
socket (it even hands back the IP header -- strip_ip_header exists for that),
the identifier is not rewritten, and TTL-exceeded arrives as an ordinary
readable message. With up to 30 probes in flight, each would take the first
ICMP message it saw as its own and the trace would come back scrambled.

Untestable here without a Mac, so: match on what a reply provably carries --
the echo's sequence number (unique per probe now; it used to be the
millisecond clock, shared by every probe submitted in the same millisecond)
and, off Linux, the identifier; and for a router's error, the echo header it
quotes back. Everything below is synthetic bytes or a fake socket, so it runs
on every platform.
"""
import socket
import struct
import threading

import pytest

from pingerplot import icmp, icmp_posix
from pingerplot.icmp_posix import (
    ICMP_DEST_UNREACH, ICMP_ECHO_REPLY, ICMP_ECHO_REQUEST, ICMP_TIME_EXCEEDED,
    build_echo, reply_is_ours,
)

DEST = "203.0.113.10"
ROUTER = "198.51.100.1"
IDENT, SEQ = 0x1234, 777


def _ip(src, dst, payload, proto=1):
    return struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 0, 0, 64, proto, 0,
                       socket.inet_aton(src), socket.inet_aton(dst)) + payload


def _echo_reply(ident=IDENT, seq=SEQ, payload=b"PingerPlot probe "):
    return struct.pack("!BBHHH", ICMP_ECHO_REPLY, 0, 0, ident, seq) + payload


def _error(itype, quoted_dst=DEST, ident=IDENT, seq=SEQ, quoted_proto=1, inner_type=ICMP_ECHO_REQUEST):
    inner = struct.pack("!BBHHH", inner_type, 0, 0, ident, seq)
    quoted = _ip("192.0.2.50", quoted_dst, inner, proto=quoted_proto)
    return struct.pack("!BBHI", itype, 0, 0, 0) + quoted


# --- echo replies ------------------------------------------------------------

def test_our_echo_reply_is_ours():
    assert reply_is_ours(_echo_reply(), DEST, IDENT, SEQ, check_ident=True)


def test_another_probes_sequence_is_not():
    assert not reply_is_ours(_echo_reply(seq=SEQ + 1), DEST, IDENT, SEQ, check_ident=True)


def test_another_process_with_our_sequence_is_not_when_ids_are_kept():
    assert not reply_is_ours(_echo_reply(ident=0x9999), DEST, IDENT, SEQ, check_ident=True)


def test_linux_rewrites_the_identifier_so_it_is_not_checked_there():
    assert reply_is_ours(_echo_reply(ident=0x9999), DEST, IDENT, SEQ, check_ident=False)


def test_an_ip_wrapped_reply_as_macos_delivers_it():
    assert reply_is_ours(_ip(DEST, "192.0.2.50", _echo_reply()), DEST, IDENT, SEQ, check_ident=True)


# --- router errors (the macOS readable path) ----------------------------------

@pytest.mark.parametrize("itype", [ICMP_TIME_EXCEEDED, ICMP_DEST_UNREACH])
def test_an_error_quoting_our_echo_is_ours(itype):
    assert reply_is_ours(_ip(ROUTER, "192.0.2.50", _error(itype)), DEST, IDENT, SEQ, check_ident=True)


def test_an_error_quoting_another_sequence_is_not():
    assert not reply_is_ours(_error(ICMP_TIME_EXCEEDED, seq=SEQ + 5), DEST, IDENT, SEQ, check_ident=True)


def test_an_error_quoting_another_destination_is_not():
    assert not reply_is_ours(_error(ICMP_TIME_EXCEEDED, quoted_dst="203.0.113.99"),
                             DEST, IDENT, SEQ, check_ident=True)


def test_an_error_about_someone_elses_udp_is_not():
    assert not reply_is_ours(_error(ICMP_TIME_EXCEEDED, quoted_proto=17), DEST, IDENT, SEQ,
                             check_ident=True)


def test_an_error_whose_quoted_id_a_nat_rewrote_is_still_ours():
    """The quoted identifier is the one field a NAT may legitimately change
    on the way back; the sequence number it leaves alone."""
    assert reply_is_ours(_error(ICMP_TIME_EXCEEDED, ident=0x4242), DEST, IDENT, SEQ, check_ident=True)


@pytest.mark.parametrize("cut", [0, 4, 8, 20, 27])
def test_a_truncated_message_is_not_ours(cut):
    assert not reply_is_ours(_error(ICMP_TIME_EXCEEDED)[:cut], DEST, IDENT, SEQ, check_ident=True)


# --- sequence numbers -----------------------------------------------------------

def test_sequence_numbers_are_unique_across_concurrent_probes():
    seen, lock = [], threading.Lock()

    def grab():
        got = [icmp_posix.next_seq() for _ in range(200)]
        with lock:
            seen.extend(got)

    threads = [threading.Thread(target=grab) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(seen) == len(set(seen)) == 6000
    assert all(0 <= s <= 0xFFFF for s in seen)


# --- the whole ping() loop, on a fake macOS socket -------------------------------

class _FakeMacSocket:
    """Delivers a stranger's echo reply first, then a reply to whatever
    sequence number ping() actually sent."""

    def __init__(self, *_a, **_kw):
        self.sent = None
        self.queue = []

    def setsockopt(self, *_a):
        pass

    def setblocking(self, _flag):
        pass

    def sendto(self, pkt, _addr):
        _t, _c, _s, ident, seq = struct.unpack("!BBHHH", pkt[:8])
        self.queue = [(_ip("192.0.2.200", "192.0.2.50", _echo_reply(ident, seq ^ 0x5555)), ("192.0.2.200", 0)),
                      (_ip(DEST, "192.0.2.50", _echo_reply(ident, seq)), (DEST, 0))]
        return len(pkt)

    def recvfrom(self, _n):
        if not self.queue:
            raise BlockingIOError
        return self.queue.pop(0)

    def close(self):
        pass


def test_ping_skips_a_strangers_reply_on_macos(monkeypatch):
    monkeypatch.setattr(icmp_posix, "_SUPPORTED_PLATFORM", True)
    monkeypatch.setattr(icmp_posix, "_LINUX", False)
    monkeypatch.setattr(icmp_posix.socket, "socket", _FakeMacSocket)
    monkeypatch.setattr(icmp_posix.select, "select", lambda r, w, x, t: (r, [], []))
    r = icmp_posix.ping(DEST, ttl=64, timeout_ms=500)
    assert r.reached and r.address == DEST, r


def test_build_echo_still_carries_the_sequence_it_was_given():
    pkt = build_echo(IDENT, SEQ, b"x")
    assert struct.unpack("!HH", pkt[4:8]) == (IDENT, SEQ)
    assert pkt[0] == ICMP_ECHO_REQUEST and icmp.IP_SUCCESS == 0
