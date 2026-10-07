"""ICMP echo on Linux and macOS, without root — the ``mtr`` technique.

The Windows backend (``icmp.py``) gets both halves of a traceroute from one
call: ``IcmpSendEcho`` returns the echo reply *or* the router's TTL-exceeded,
already decoded. POSIX has no such call, so this module assembles the same
thing from two different mechanisms:

* **The probe socket** is ``SOCK_DGRAM``/``IPPROTO_ICMP``, not ``SOCK_RAW``.
  That is the whole reason no root is needed — on Linux the kernel owns the
  identifier and the checksum and will only deliver replies to echoes *you*
  sent, so it can safely hand the socket to an unprivileged process. Linux
  gates it on ``net.ipv4.ping_group_range``; macOS allows it outright but
  makes no such delivery guarantee, so every reply is matched to its probe
  (:func:`reply_is_ours`) rather than trusted.

* **The router's TTL-exceeded** does not arrive on that socket on Linux,
  because it is an *error* relating to the datagram we sent, not a message
  addressed to us. It goes to the socket's error queue, read with
  ``recvmsg(MSG_ERRQUEUE)`` after enabling ``IP_RECVERR``. macOS has neither;
  there the error is delivered as an ordinary readable ICMP message. Both
  paths are handled, because the alternative is a backend that traces on one
  of the two platforms and silently reports every hop as ``*`` on the other.

Same signature and same :class:`PingResult` as the Windows backend, so the
engine above does not know or care which one it is talking to.

Status: the packet building, the error-queue decoding and the reply matching
are unit-tested against synthetic bytes, and the live path is exercised
wherever the platform actually permits an unprivileged ICMP socket. It has NOT
been run against real hardware on a real multi-hop path — smoke-test it on a
Linux box before trusting a trace from it.
"""
from __future__ import annotations

import errno
import itertools
import os
import platform
import select
import socket
import struct
import sys
import threading
import time
from typing import Optional, Tuple

from .icmp import (
    ERROR_INVALID_NETNAME,
    IP_DEST_HOST_UNREACHABLE,
    IP_DEST_NET_UNREACHABLE,
    IP_DEST_PORT_UNREACHABLE,
    IP_DEST_PROT_UNREACHABLE,
    IP_REQ_TIMED_OUT,
    IP_SUCCESS,
    IP_TTL_EXPIRED_TRANSIT,
    PingResult,
)

ICMP_ECHO_REQUEST = 8
ICMP_ECHO_REPLY = 0
ICMP_TIME_EXCEEDED = 11
ICMP_DEST_UNREACH = 3

# Linux, from <linux/errqueue.h> and <linux/in.h>. Absent from the socket
# module on some builds, so they are spelled out rather than imported.
IP_RECVERR = 11
SO_EE_ORIGIN_ICMP = 2
MSG_ERRQUEUE = getattr(socket, "MSG_ERRQUEUE", 0x2000)

_LINUX = sys.platform.startswith("linux")
_SUPPORTED_PLATFORM = _LINUX or sys.platform == "darwin"

# SO_TIMESTAMPNS: the kernel stamps each received packet, so an RTT does not
# include the time this thread spent waiting for the GIL before it could read
# the reply -- which, with anything else busy in the process (a Timeline
# redraw holds it for tens of ms), was being reported as network latency.
# Python's socket module does not export it. 35 is the asm-generic value;
# alpha, mips, parisc and sparc number it differently and are left on the
# user-space clock rather than guessed at. macOS has only the microsecond
# SO_TIMESTAMP and is likewise left as it was.
_SO_TIMESTAMPNS = getattr(socket, "SO_TIMESTAMPNS", None)
if _SO_TIMESTAMPNS is None and _LINUX and platform.machine().lower() in {
        "x86_64", "amd64", "i386", "i686", "aarch64", "arm64", "armv6l", "armv7l",
        "armv8l", "riscv64", "ppc64le", "ppc64", "s390x", "loongarch64"}:
    _SO_TIMESTAMPNS = 35
_TIMESPEC = struct.Struct("@ll")      # struct timespec: native long tv_sec, tv_nsec
_MAX_KERNEL_RTT_NS = 60 * 1_000_000_000

# ICMP dest-unreachable codes -> the IP_STATUS the rest of the app speaks, so
# one vocabulary covers both backends.
_UNREACH_STATUS = {
    0: IP_DEST_NET_UNREACHABLE,
    1: IP_DEST_HOST_UNREACHABLE,
    2: IP_DEST_PROT_UNREACHABLE,
    3: IP_DEST_PORT_UNREACHABLE,
}


# Sequence numbers are the one thing a reply provably echoes back unchanged,
# so each probe gets its own. Random start: another PingerPlot process on a
# macOS box shares the ICMP namespace with this one.
_seq_counter = itertools.count(int.from_bytes(os.urandom(2), "big"))
_seq_lock = threading.Lock()


def next_seq() -> int:
    """A sequence number no other in-flight probe from this process holds. It
    used to be the millisecond clock, which every probe submitted in the same
    millisecond -- most of a 30-hop round -- shared."""
    with _seq_lock:
        return next(_seq_counter) & 0xFFFF


def checksum(data: bytes) -> int:
    """The internet checksum (RFC 1071) over ``data``.

    The kernel recomputes this for a SOCK_DGRAM ICMP socket, so it is not
    load-bearing on the send path — but a correct one costs nothing and the
    function is what verifies a *received* packet.
    """
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def build_echo(ident: int, seq: int, payload: bytes) -> bytes:
    """An ICMP echo request. The kernel overwrites ``ident`` (and the checksum)
    on a SOCK_DGRAM socket — that substitution is precisely what makes the
    unprivileged socket safe — so nothing may depend on it coming back."""
    header = struct.pack("!BBHHH", ICMP_ECHO_REQUEST, 0, 0,
                         ident & 0xFFFF, seq & 0xFFFF)
    csum = checksum(header + payload)
    return struct.pack("!BBHHH", ICMP_ECHO_REQUEST, 0, csum,
                       ident & 0xFFFF, seq & 0xFFFF) + payload


def strip_ip_header(data: bytes) -> bytes:
    """Return the ICMP portion of ``data``.

    Linux hands a SOCK_DGRAM ICMP socket the ICMP message alone; some BSD-derived
    stacks include the IPv4 header. Rather than branch on platform — which would
    be a guess on any stack not tested here — detect it: an IPv4 header starts
    with version 4 and an IHL of at least 5, giving a first byte of 0x45-0x4F,
    while an ICMP message starts with its type. None of the types this module
    cares about (0, 3, 11) fall in that range, so the two cannot be confused.
    """
    if data and 0x45 <= data[0] <= 0x4F:
        ihl = (data[0] & 0x0F) * 4
        if len(data) > ihl:
            return data[ihl:]
    return data


def parse_icmp(data: bytes) -> Optional[Tuple[int, int, bytes]]:
    """``(type, code, rest)`` from an ICMP message, or None if it is too short."""
    body = strip_ip_header(data)
    if len(body) < 8:
        return None
    return body[0], body[1], body[8:]


def reply_is_ours(data: bytes, dest_ip: str, ident: int, seq: int,
                  check_ident: bool) -> bool:
    """Whether a message read from the probe socket answers *this* probe.

    Linux already guarantees it: a ping socket only receives replies carrying
    its kernel-assigned identifier, and router errors go to its own error
    queue. macOS does not. Its ICMP datagram socket behaves like a raw one, so
    with a round's probes in flight each could take another's reply -- or a
    TTL-exceeded meant for another probe -- as its own.

    * Echo reply: our sequence number, and our identifier when the kernel has
      not rewritten it (``check_ident``; Linux rewrites it, so not there).
    * TTL exceeded / unreachable: the quoted original must be an ICMP echo to
      ``dest_ip`` with our sequence number. The quoted identifier is not
      checked, because a NAT may rewrite it on the way back.
    """
    body = strip_ip_header(data)
    if len(body) < 8:
        return False
    itype = body[0]
    if itype == ICMP_ECHO_REPLY:
        rid, rseq = struct.unpack_from("!HH", body, 4)
        return rseq == seq and (not check_ident or rid == ident)
    if itype in (ICMP_TIME_EXCEEDED, ICMP_DEST_UNREACH):
        quoted = body[8:]
        if len(quoted) < 20 or quoted[0] >> 4 != 4 or quoted[9] != socket.IPPROTO_ICMP:
            return False
        inner = quoted[(quoted[0] & 0x0F) * 4:]
        if len(inner) < 8 or inner[0] != ICMP_ECHO_REQUEST:
            return False
        if socket.inet_ntoa(quoted[16:20]) != dest_ip:
            return False
        return struct.unpack_from("!H", inner, 6)[0] == seq
    return False


def kernel_rtt_ms(ancdata, sent_ns: int) -> Optional[float]:
    """RTT from a received packet's SO_TIMESTAMPNS control message, or None.

    ``sent_ns`` is ``time.time_ns()`` taken just before sendto; the kernel's
    stamp is the same clock (CLOCK_REALTIME). That clock can be stepped by NTP
    mid-probe, so a negative or absurd difference is discarded and the caller
    falls back to its own measurement.
    """
    if _SO_TIMESTAMPNS is None:
        return None
    for level, ctype, cdata in ancdata:
        if level != socket.SOL_SOCKET or ctype != _SO_TIMESTAMPNS or len(cdata) < _TIMESPEC.size:
            continue
        sec, nsec = _TIMESPEC.unpack_from(cdata)
        delta = sec * 1_000_000_000 + nsec - sent_ns
        if 0 <= delta < _MAX_KERNEL_RTT_NS:
            return delta / 1_000_000.0
    return None


def parse_errqueue(cmsg_data: bytes) -> Optional[Tuple[int, int, Optional[str]]]:
    """Decode a Linux ``IP_RECVERR`` control message.

    Returns ``(icmp_type, icmp_code, offender_ip)``. The layout is a
    ``struct sock_extended_err`` (16 bytes) followed by the offending host's
    ``sockaddr_in`` — the router that sent the error, which is the one piece of
    information a traceroute actually wants:

        u32 ee_errno; u8 ee_origin; u8 ee_type; u8 ee_code; u8 ee_pad;
        u32 ee_info;  u32 ee_data;
        struct sockaddr_in {u16 family; u16 port; u8 addr[4]; u8 zero[8];}

    ``ee_origin`` must be SO_EE_ORIGIN_ICMP; a local error (origin 1) carries no
    offender and is not a hop.
    """
    if len(cmsg_data) < 16:
        return None
    # Native byte order: this is a kernel structure, not a wire format.
    _errno, origin, ee_type, ee_code = struct.unpack_from("=IBBB", cmsg_data, 0)
    if origin != SO_EE_ORIGIN_ICMP:
        return None
    offender = None
    if len(cmsg_data) >= 24:
        # sin_addr sits 4 bytes into the sockaddr_in, which starts at offset 16.
        offender = socket.inet_ntoa(cmsg_data[20:24])
    return ee_type, ee_code, offender


def _status_for(icmp_type: int, icmp_code: int) -> int:
    if icmp_type == ICMP_TIME_EXCEEDED:
        return IP_TTL_EXPIRED_TRANSIT
    if icmp_type == ICMP_DEST_UNREACH:
        return _UNREACH_STATUS.get(icmp_code, IP_DEST_HOST_UNREACHABLE)
    return IP_REQ_TIMED_OUT


def is_available() -> bool:
    """True when an unprivileged ICMP socket can actually be opened here.

    Linux gates SOCK_DGRAM/IPPROTO_ICMP on net.ipv4.ping_group_range, so the
    platform being right is not enough — a distro that has not opened it up
    gives EACCES. Ask the kernel rather than guess.
    """
    if not _SUPPORTED_PLATFORM:
        return False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except (OSError, AttributeError):
        return False
    s.close()
    return True


def unavailable_reason() -> str:
    """Why is_available() said no — worth surfacing, because the Linux case has
    a one-line fix and 'ICMP not available' does not hint at it."""
    if not _SUPPORTED_PLATFORM:
        return f"no unprivileged ICMP backend for {sys.platform}"
    try:
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP).close()
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM):
            return ("unprivileged ICMP sockets are not permitted for this user. "
                    "On Linux: sysctl -w net.ipv4.ping_group_range=\"0 2147483647\" "
                    f"(or add this process's group, {os.getgid() if hasattr(os, 'getgid') else '?'}"
                    ", to the range)")
        return f"cannot open an ICMP socket: {exc}"
    return ""


def ping(
    dest_ip: str,
    ttl: int = 128,
    timeout_ms: int = 1000,
    payload: bytes = b"PingerPlot-probe..",
    tos: int = 0,
    source_ip: Optional[str] = None,
) -> PingResult:
    """One ICMP echo, matching :func:`pingerplot.icmp.ping`'s contract."""
    if not _SUPPORTED_PLATFORM:
        raise RuntimeError(f"no POSIX ICMP backend for {sys.platform}")

    timeout = max(0.001, timeout_ms / 1000.0)
    ident = os.getpid() & 0xFFFF
    seq = next_seq()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, max(1, min(int(ttl), 255)))
        if tos:
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, int(tos) & 0xFF)
            except OSError:
                pass          # marking is best effort, the probe still goes
        if _LINUX:
            try:
                sock.setsockopt(socket.IPPROTO_IP, IP_RECVERR, 1)
            except OSError:
                pass          # without it intermediate hops read as timeouts
        stamped = False
        if _LINUX and _SO_TIMESTAMPNS is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, _SO_TIMESTAMPNS, 1)
                stamped = True
            except OSError:
                pass          # falls back to timing in user space
        if source_ip:
            try:
                sock.bind((source_ip, 0))
            except OSError:
                # Same contract as the Windows backend, which fails an address
                # the machine does not hold with ERROR_INVALID_NETNAME rather
                # than falling back to the default route. Raising instead would
                # abort the trace with a bare errno 99, and inside a monitoring
                # round _gather_range would swallow it into a timeout -- so a
                # pinned interface going away mid-run would report 100% loss on
                # a path that is fine. That silent-wrong-numbers outcome is the
                # exact thing the setting exists to prevent.
                return PingResult(ERROR_INVALID_NETNAME, None, None, False)
        sock.setblocking(False)

        packet = build_echo(ident, seq, payload)
        start = time.perf_counter()
        # Last thing before the send: from here to the syscall this thread
        # holds the GIL throughout, so the stamp is not delayed by anyone.
        sent_ns = time.time_ns() if stamped else None
        try:
            sock.sendto(packet, (dest_ip, 0))
        except OSError as exc:
            return PingResult(_send_failure_status(exc), None, None, False)

        deadline = start + timeout
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return PingResult(IP_REQ_TIMED_OUT, None, None, False)

            # Error queue first: on Linux the router's TTL-exceeded is here, and
            # it is the only place the router's address exists.
            hop = _read_errqueue(sock, start, sent_ns)
            if hop is not None:
                return hop

            try:
                select.select([sock], [], [sock], min(0.05, remaining))
            except OSError:
                return PingResult(IP_REQ_TIMED_OUT, None, None, False)

            reply = _read_reply(sock, dest_ip, start, ident, seq, sent_ns)
            if reply is not None:
                return reply
    finally:
        sock.close()


def _send_failure_status(exc: OSError) -> int:
    """A send that fails locally still has to become a PingResult, not an
    exception — a single unroutable target must not abort the round."""
    if exc.errno == errno.ENETUNREACH:
        return IP_DEST_NET_UNREACHABLE
    if exc.errno in (errno.EHOSTUNREACH, errno.EHOSTDOWN):
        return IP_DEST_HOST_UNREACHABLE
    return IP_REQ_TIMED_OUT


def _rtt_ms(ancdata, sent_ns: Optional[int], start: float) -> float:
    """The kernel's figure when there is one, else the user-space clock."""
    if sent_ns is not None:
        rtt = kernel_rtt_ms(ancdata, sent_ns)
        if rtt is not None:
            return rtt
    return (time.perf_counter() - start) * 1000.0


def _read_errqueue(sock: socket.socket, start: float,
                   sent_ns: Optional[int] = None) -> Optional[PingResult]:
    if not _LINUX:
        return None
    try:
        _data, ancdata, _flags, _addr = sock.recvmsg(512, 1024, MSG_ERRQUEUE)
    except (BlockingIOError, InterruptedError):
        return None
    except OSError:
        return None
    for level, ctype, cdata in ancdata:
        if level != socket.IPPROTO_IP or ctype != IP_RECVERR:
            continue
        parsed = parse_errqueue(cdata)
        if parsed is None:
            continue
        icmp_type, icmp_code, offender = parsed
        rtt = _rtt_ms(ancdata, sent_ns, start)
        if icmp_type == ICMP_TIME_EXCEEDED:
            return PingResult(IP_TTL_EXPIRED_TRANSIT, rtt, offender, False)
        # An unreachable is a hop that answered, but not the destination. No
        # RTT: the number would describe an error path, not a round trip.
        return PingResult(_status_for(icmp_type, icmp_code), None, offender, False)
    return None


def _read_reply(sock: socket.socket, dest_ip: str, start: float,
                ident: int, seq: int, sent_ns: Optional[int] = None) -> Optional[PingResult]:
    ancdata = []
    try:
        if sent_ns is not None:       # timestamps on: they arrive as ancillary data
            data, ancdata, _flags, addr = sock.recvmsg(2048, 1024)
        else:
            data, addr = sock.recvfrom(2048)
    except (BlockingIOError, InterruptedError):
        return None
    except OSError:
        return None
    parsed = parse_icmp(data)
    if parsed is None:
        return None
    if not reply_is_ours(data, dest_ip, ident, seq, check_ident=not _LINUX):
        return None             # another probe's reply; keep waiting for ours
    icmp_type, icmp_code, _rest = parsed
    responder = addr[0] if addr else None
    rtt = _rtt_ms(ancdata, sent_ns, start)
    if icmp_type == ICMP_ECHO_REPLY:
        return PingResult(IP_SUCCESS, rtt, responder or dest_ip, True)
    if icmp_type == ICMP_TIME_EXCEEDED:
        # The macOS path: the error arrives as an ordinary readable message
        # rather than through an error queue.
        return PingResult(IP_TTL_EXPIRED_TRANSIT, rtt, responder, False)
    if icmp_type == ICMP_DEST_UNREACH:
        return PingResult(_status_for(icmp_type, icmp_code), None, responder, False)
    return None
