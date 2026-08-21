"""The POSIX ICMP backend: packet building, error-queue decoding, reply matching.

Windows gets both halves of a traceroute from one call — IcmpSendEcho returns
the echo reply or the router's TTL-exceeded, already decoded. POSIX has no such
call, so this backend assembles the same result from two mechanisms that do not
resemble each other:

* the echo reply, on an unprivileged SOCK_DGRAM/IPPROTO_ICMP socket;
* the router's TTL-exceeded, which on Linux is not a message addressed to us
  but an *error* about the datagram we sent, and therefore arrives on the
  socket's error queue via recvmsg(MSG_ERRQUEUE) — the mtr technique.

macOS has neither IP_RECVERR nor MSG_ERRQUEUE and delivers the error as an
ordinary readable ICMP message, so both paths exist.

Everything below runs on any platform, because it works on bytes rather than
sockets. That matters: the code is Linux/macOS-only but Windows is where it is
being written, so if the decoding were only testable on the target platform it
would effectively be untested.

Honest status, repeated in the module docstring: this has NOT been run against
a real multi-hop path on real hardware. The live-socket tests below run only
where the platform actually permits an unprivileged ICMP socket.
"""
import errno
import socket
import struct
import sys

import pytest

from pingerplot import icmp, icmp_posix
from pingerplot.icmp_posix import (
    ICMP_DEST_UNREACH, ICMP_ECHO_REPLY, ICMP_ECHO_REQUEST, ICMP_TIME_EXCEEDED,
    SO_EE_ORIGIN_ICMP,
)


# --- checksum --------------------------------------------------------------

def test_checksum_of_a_known_packet():
    """RFC 1071: the checksum over a message that already contains its own
    correct checksum is zero. That is the property a receiver verifies."""
    pkt = icmp_posix.build_echo(0x1234, 1, b"abcdefgh")
    assert icmp_posix.checksum(pkt) == 0


def test_checksum_handles_an_odd_length():
    assert 0 <= icmp_posix.checksum(b"\x01\x02\x03") <= 0xFFFF


def test_checksum_of_empty_input():
    assert icmp_posix.checksum(b"") == 0xFFFF


# --- the echo request ------------------------------------------------------

def test_echo_request_has_the_right_shape():
    pkt = icmp_posix.build_echo(0xBEEF, 7, b"payload!")
    itype, code, csum, ident, seq = struct.unpack("!BBHHH", pkt[:8])
    assert itype == ICMP_ECHO_REQUEST and code == 0
    assert ident == 0xBEEF and seq == 7
    assert pkt[8:] == b"payload!"
    assert csum != 0


def test_echo_request_carries_the_payload_verbatim():
    """Payload size is a user setting used to test MTU behaviour, so it must
    go out at exactly the requested length."""
    for size in (0, 1, 32, 1472):
        pkt = icmp_posix.build_echo(1, 1, b"x" * size)
        assert len(pkt) == 8 + size


def test_identifier_and_sequence_are_masked_to_16_bits():
    pkt = icmp_posix.build_echo(0x1FFFF, 0x2FFFF, b"")
    _t, _c, _s, ident, seq = struct.unpack("!BBHHH", pkt[:8])
    assert ident == 0xFFFF and seq == 0xFFFF


# --- IP header detection ---------------------------------------------------

def _ip_wrapped(icmp_bytes, ihl_words=5):
    header = bytearray(ihl_words * 4)
    header[0] = 0x40 | ihl_words
    header[9] = 1
    return bytes(header) + icmp_bytes


def test_a_bare_icmp_message_is_left_alone():
    """Linux hands over the ICMP message with no IP header."""
    body = struct.pack("!BBHHH", ICMP_ECHO_REPLY, 0, 0, 1, 1) + b"data"
    assert icmp_posix.strip_ip_header(body) == body


def test_an_ip_wrapped_message_is_unwrapped():
    """Some BSD-derived stacks include the IPv4 header."""
    body = struct.pack("!BBHHH", ICMP_ECHO_REPLY, 0, 0, 1, 1) + b"data"
    assert icmp_posix.strip_ip_header(_ip_wrapped(body)) == body


def test_ip_options_are_accounted_for():
    body = struct.pack("!BBHHH", ICMP_TIME_EXCEEDED, 0, 0, 0, 0) + b"quoted"
    assert icmp_posix.strip_ip_header(_ip_wrapped(body, ihl_words=8)) == body


@pytest.mark.parametrize("itype", [ICMP_ECHO_REPLY, ICMP_DEST_UNREACH, ICMP_TIME_EXCEEDED])
def test_no_icmp_type_can_be_mistaken_for_an_ip_header(itype):
    """The detection keys on the first byte being 0x45-0x4F. None of the types
    this module handles land in that range, which is what makes the heuristic
    safe rather than a guess."""
    assert not (0x45 <= itype <= 0x4F)
    body = struct.pack("!BBHHH", itype, 0, 0, 1, 1) + b"xxxxxxxx"
    assert icmp_posix.strip_ip_header(body) == body


def test_a_truncated_message_is_not_mangled():
    assert icmp_posix.strip_ip_header(b"") == b""
    assert icmp_posix.strip_ip_header(b"\x45") == b"\x45"


# --- parse_icmp ------------------------------------------------------------

def test_parse_icmp_returns_type_code_and_rest():
    body = struct.pack("!BBHHH", ICMP_TIME_EXCEEDED, 0, 0, 0, 0) + b"originalpkt"
    assert icmp_posix.parse_icmp(body) == (ICMP_TIME_EXCEEDED, 0, b"originalpkt")


def test_parse_icmp_rejects_a_runt():
    assert icmp_posix.parse_icmp(b"\x00\x00") is None
    assert icmp_posix.parse_icmp(b"") is None


# --- the Linux error queue -------------------------------------------------

def _sock_extended_err(ee_type, ee_code, offender="10.0.0.1",
                       origin=SO_EE_ORIGIN_ICMP, with_offender=True):
    """A struct sock_extended_err followed by the offender's sockaddr_in,
    exactly as the kernel lays it out in the control message."""
    blob = struct.pack("=IBBBBII", 0, origin, ee_type, ee_code, 0, 0, 0)
    if with_offender:
        blob += struct.pack("=HH", socket.AF_INET, 0)
        blob += socket.inet_aton(offender)
        blob += b"\x00" * 8
    return blob


def test_a_ttl_exceeded_yields_the_router_address():
    """The one thing a traceroute needs, and the only place it exists on Linux."""
    got = icmp_posix.parse_errqueue(_sock_extended_err(ICMP_TIME_EXCEEDED, 0, "192.0.2.44"))
    assert got == (ICMP_TIME_EXCEEDED, 0, "192.0.2.44")


def test_a_destination_unreachable_is_decoded():
    got = icmp_posix.parse_errqueue(_sock_extended_err(ICMP_DEST_UNREACH, 3, "192.0.2.9"))
    assert got == (ICMP_DEST_UNREACH, 3, "192.0.2.9")


def test_a_local_error_is_not_a_hop():
    """SO_EE_ORIGIN_LOCAL (1) is the kernel reporting about itself; there is no
    router involved and treating it as a hop would invent one."""
    assert icmp_posix.parse_errqueue(
        _sock_extended_err(ICMP_TIME_EXCEEDED, 0, origin=1)) is None


def test_a_truncated_control_message_is_rejected():
    assert icmp_posix.parse_errqueue(b"") is None
    assert icmp_posix.parse_errqueue(b"\x00" * 15) is None


def test_a_control_message_without_an_offender_still_decodes():
    """The type is usable even when the kernel supplied no sockaddr."""
    got = icmp_posix.parse_errqueue(
        _sock_extended_err(ICMP_TIME_EXCEEDED, 0, with_offender=False))
    assert got == (ICMP_TIME_EXCEEDED, 0, None)


def test_the_offender_address_is_read_from_the_right_offset():
    """sin_addr sits 4 bytes into the sockaddr_in, which starts 16 bytes into
    the control message. Off by four and every hop reports 0.0.0.0 or garbage."""
    for ip in ("1.2.3.4", "255.254.253.252", "10.20.0.1"):
        assert icmp_posix.parse_errqueue(
            _sock_extended_err(ICMP_TIME_EXCEEDED, 0, ip))[2] == ip


# --- status mapping --------------------------------------------------------

def test_icmp_types_map_onto_the_shared_status_vocabulary():
    """Both backends have to speak the same statuses, or the GUI and the CSV
    would need to know which one produced a row."""
    assert icmp_posix._status_for(ICMP_TIME_EXCEEDED, 0) == icmp.IP_TTL_EXPIRED_TRANSIT
    assert icmp_posix._status_for(ICMP_DEST_UNREACH, 0) == icmp.IP_DEST_NET_UNREACHABLE
    assert icmp_posix._status_for(ICMP_DEST_UNREACH, 1) == icmp.IP_DEST_HOST_UNREACHABLE
    assert icmp_posix._status_for(ICMP_DEST_UNREACH, 3) == icmp.IP_DEST_PORT_UNREACHABLE


def test_an_unknown_unreachable_code_still_produces_a_status():
    assert icmp_posix._status_for(ICMP_DEST_UNREACH, 99) == icmp.IP_DEST_HOST_UNREACHABLE


def test_send_failures_become_statuses_not_exceptions():
    """One unroutable target must not abort the whole round."""
    assert icmp_posix._send_failure_status(
        OSError(errno.ENETUNREACH, "x")) == icmp.IP_DEST_NET_UNREACHABLE
    assert icmp_posix._send_failure_status(
        OSError(errno.EHOSTUNREACH, "x")) == icmp.IP_DEST_HOST_UNREACHABLE
    assert icmp_posix._send_failure_status(OSError(errno.EINVAL, "x")) == icmp.IP_REQ_TIMED_OUT


# --- availability ----------------------------------------------------------

def test_availability_is_answered_by_the_kernel_not_by_platform_name():
    """Linux gates SOCK_DGRAM ICMP on net.ipv4.ping_group_range, so 'this is
    Linux' does not imply the socket can be opened."""
    assert isinstance(icmp_posix.is_available(), bool)


def test_an_unavailable_backend_explains_itself():
    if icmp_posix.is_available():
        assert icmp_posix.unavailable_reason() == ""
    else:
        reason = icmp_posix.unavailable_reason()
        assert reason, "refusing without a reason leaves the user nowhere"
        if sys.platform.startswith("linux"):
            assert "ping_group_range" in reason, "the fix is one sysctl; say so"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows dispatch")
def test_windows_still_uses_the_native_backend():
    assert icmp.is_available() and icmp.unavailable_reason() == ""
    assert not icmp_posix.is_available()


def test_ping_refuses_a_platform_it_has_no_backend_for():
    if icmp_posix._SUPPORTED_PLATFORM:
        pytest.skip("this platform does have a POSIX backend")
    with pytest.raises(RuntimeError, match="no POSIX ICMP backend"):
        icmp_posix.ping("127.0.0.1")


# --- live, where the platform allows it ------------------------------------

LIVE = pytest.mark.skipif(
    not icmp_posix.is_available(),
    reason="needs an unprivileged ICMP socket (Linux: net.ipv4.ping_group_range)",
)


@LIVE
def test_loopback_answers():
    r = icmp_posix.ping("127.0.0.1", ttl=64, timeout_ms=2000)
    assert r.reached and r.status == icmp.IP_SUCCESS
    assert r.rtt_ms is not None and r.rtt_ms >= 0


@LIVE
def test_a_ttl_of_one_does_not_reach_a_distant_host():
    """Either a router answers (a hop) or nothing does (a timeout). What must
    not happen is 'reached', which would mean TTL is being ignored and every
    traceroute would be one hop long."""
    r = icmp_posix.ping("8.8.8.8", ttl=1, timeout_ms=2000)
    assert not r.reached


@LIVE
def test_the_engine_reaches_the_same_backend():
    """icmp.ping dispatches, so the whole app follows without knowing."""
    assert icmp.ping("127.0.0.1", ttl=64, timeout_ms=2000).reached


# --- the source-IP contract ------------------------------------------------
#
# The README makes an explicit promise about this setting: "An address the
# machine doesn't hold is rejected with ERROR_INVALID_NETNAME rather than
# silently falling back to the default route — a silent fallback is the
# dangerous outcome, because the numbers look fine and describe a path you
# didn't ask about."
#
# The Windows backend keeps it (tests/test_dscp_and_source.py). This backend
# called sock.bind((source_ip, 0)) with no guard, so the same input raised
# OSError(EADDRNOTAVAIL) straight out of ping() -- two different wrong
# behaviours depending on when it happened. During _trace the exception reaches
# _run's broad handler and the monitor stops with a raw "Monitor error:
# OSError(99, 'Cannot assign requested address')". During a monitoring round
# _gather_range swallows it into a timeout, so a pinned interface disappearing
# mid-run reports 100% packet loss on a path that is fine -- the
# silent-wrong-numbers failure the setting exists to prevent, one step removed.
#
# The socket is faked so these run on every platform including Windows, which
# is where this backend cannot execute at all and where it would otherwise go
# untested. The live pair below covers the real kernel.


class _BindRefusingSocket:
    def __init__(self, *_a, **_kw):
        self.closed = False

    def setsockopt(self, *_a):
        pass

    def bind(self, _addr):
        raise OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")

    def setblocking(self, _flag):
        raise AssertionError("reached setblocking: bind() failure was not handled")

    def close(self):
        self.closed = True


@pytest.fixture
def any_platform(monkeypatch):
    """ping() refuses to run off Linux/macOS. The bind guard is platform-neutral
    logic, so lift that gate rather than leaving it uncovered on Windows."""
    monkeypatch.setattr(icmp_posix, "_SUPPORTED_PLATFORM", True)


def test_a_source_the_machine_does_not_hold_is_named_not_raised(any_platform, monkeypatch):
    monkeypatch.setattr(icmp_posix.socket, "socket", _BindRefusingSocket)
    r = icmp_posix.ping("127.0.0.1", ttl=64, timeout_ms=200,
                        source_ip="203.0.113.7")
    assert r.status == icmp.ERROR_INVALID_NETNAME
    assert not r.reached and r.rtt_ms is None
    assert "not on this machine" in icmp.status_text(r.status)


def test_the_rejection_matches_what_the_windows_backend_returns(any_platform, monkeypatch):
    """One vocabulary across both backends: the engine and the GUI render the
    status, so a POSIX-only code would print as a bare number."""
    monkeypatch.setattr(icmp_posix.socket, "socket", _BindRefusingSocket)
    r = icmp_posix.ping("127.0.0.1", timeout_ms=200, source_ip="203.0.113.7")
    assert icmp.status_text(r.status) != str(r.status), "unmapped status code"
    assert r.status in icmp._STATUS_TEXT


def test_the_socket_is_closed_when_bind_fails(any_platform, monkeypatch):
    made = []
    monkeypatch.setattr(icmp_posix.socket, "socket",
                        lambda *a, **kw: made.append(_BindRefusingSocket()) or made[-1])
    icmp_posix.ping("127.0.0.1", timeout_ms=200, source_ip="203.0.113.7")
    assert made and made[0].closed, "leaked a socket on the rejection path"


def test_no_source_ip_never_touches_bind(any_platform, monkeypatch):
    """The guard must not have made the ordinary path pay for it."""
    class _NoBind(_BindRefusingSocket):
        def bind(self, _addr):
            raise AssertionError("bind() called without a source_ip")

        def setblocking(self, _flag):
            raise _Done()

    class _Done(Exception):
        pass

    monkeypatch.setattr(icmp_posix.socket, "socket", _NoBind)
    with pytest.raises(_Done):
        icmp_posix.ping("127.0.0.1", timeout_ms=200)


@LIVE
def test_a_source_the_machine_does_not_hold_is_named_not_raised_live():
    """The real thing, where a real kernel refuses a real address. 203.0.113.7
    is RFC 5737 documentation space -- no machine holds it."""
    r = icmp_posix.ping("127.0.0.1", ttl=64, timeout_ms=500,
                        source_ip="203.0.113.7")
    assert r.status == icmp.ERROR_INVALID_NETNAME
    assert not r.reached


@LIVE
def test_binding_to_an_address_the_machine_does_hold_still_works():
    """The other half: the guard must not have broken the working case."""
    r = icmp_posix.ping("127.0.0.1", ttl=64, timeout_ms=2000,
                        source_ip="127.0.0.1")
    assert r.reached and r.status == icmp.IP_SUCCESS
