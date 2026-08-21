"""TCP/UDP traceroute probes (tcptraceroute-style), for paths that block or
deprioritise ICMP, or to test reachability of a specific service port.

Technique
---------
Send a normal TCP SYN (or UDP datagram) to the destination with a chosen IP TTL
through an ordinary socket — Windows lets us set ``IP_TTL`` on a normal socket
without elevation, sidestepping the rule that raw sockets may not *send* TCP.

To see the routers along the way we must capture the ICMP "TTL exceeded" errors
they send back. On Windows those errors are consumed by the network stack and
are *not* delivered to a plain raw ICMP socket, so we open a raw capture socket
with ``SIO_RCVALL`` (receive-all) and correlate each ICMP error to our probe by
the source/destination ports echoed inside it. ``SIO_RCVALL`` requires
Administrator rights.

Reaching the destination is detected:
  * TCP  -> at the TCP layer: connect completes (SYN-ACK, port open) or is
            refused (RST, port closed). Either proves we reached the host — and
            this needs no capture socket, so TCP "final hop only" works without
            elevation.
  * UDP  -> via ICMP "port unreachable" from the destination (needs capture).
            An *open* UDP port stays silent and reads as a timeout — a known
            UDP-traceroute limitation; prefer TCP for a definitive "service up".
"""
from __future__ import annotations

import ctypes
import errno
import select
import socket
import sys
import time
from typing import Optional

from .icmp import (
    IP_REQ_TIMED_OUT,
    IP_SUCCESS,
    IP_TTL_EXPIRED_TRANSIT,
    PingResult,
)

ICMP_TYPE_DEST_UNREACH = 3
ICMP_TYPE_TTL_EXCEEDED = 11
IPPROTO_ICMP = 1
_PROTO = {"tcp": socket.IPPROTO_TCP, "udp": socket.IPPROTO_UDP}
# SO_ERROR values that still prove the destination host answered at the TCP layer
_CLEAN_REACH_ERRORS = {0, errno.ECONNREFUSED, 10061}  # 10061 = WSAECONNREFUSED

DEFAULT_PORTS = {"tcp": 443, "udp": 33434}


def _set_tos(sock: socket.socket, tos: int) -> None:
    """Mark the probe with an IP ToS byte, best effort.

    Caveat worth knowing before trusting a TCP/UDP QoS test: Windows ignores
    IP_TOS on an ordinary socket unless the DisableUserTOSSetting registry
    value is cleared, and it does so *silently* -- setsockopt succeeds either
    way. ICMP mode does not have this problem, because it marks through
    IP_OPTION_INFORMATION.Tos in the IP Helper API rather than through a
    socket. So: prefer ICMP mode for DSCP work on Windows, and confirm with a
    capture before drawing conclusions from a TCP/UDP run.
    """
    if not tos:
        return
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, int(tos) & 0xFF)
    except OSError:
        pass


def _tcp_reach(err: int, rtt: float, dest_ip: str) -> PingResult:
    """Classify a TCP probe socket's SO_ERROR. A clean result (connected, or
    refused = port closed) proves the destination answered; anything else is no
    usable reply. Always returns a definite result, so a probed TTL is never
    silently dropped from a round (the bug this replaces)."""
    if err in _CLEAN_REACH_ERRORS:
        return PingResult(IP_SUCCESS, rtt, dest_ip, True)
    return PingResult(IP_REQ_TIMED_OUT, None, None, False)


def is_admin() -> bool:
    """Best-effort check for the elevation raw capture needs on Windows."""
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def local_ip_for(dest_ip: str) -> str:
    """The source IP the OS would use to reach ``dest_ip`` (for binding the
    capture socket to the right interface)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((dest_ip, 9))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "0.0.0.0"


def _open_capture(local_ip: str) -> socket.socket:
    """Raw socket in receive-all mode so we see ICMP errors the stack would
    otherwise swallow. Raises OSError without Administrator rights, and
    likewise on any platform that has no SIO_RCVALL."""
    if not hasattr(socket, "SIO_RCVALL"):
        # SIO_RCVALL is a Windows ioctl. Every caller guards this with
        # `except OSError`, which does not catch the AttributeError that
        # referencing it elsewhere raises -- so it would sail past
        # capture_supported() and reach _run's broad handler as an obscure
        # "Monitor error: AttributeError(...)" instead of the clear "needs
        # Administrator" message. Unreachable on Linux, where the raw socket
        # itself fails with EPROTONOSUPPORT first, but not verified on macOS,
        # which has had an ICMP backend since 1.3.0.
        raise OSError("raw ICMP capture (SIO_RCVALL) is available only on Windows")
    s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
    s.bind((local_ip, 0))
    s.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)  # type: ignore[attr-defined]
    s.setblocking(False)
    return s


def _close_capture(s: socket.socket) -> None:
    try:
        s.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)  # type: ignore[attr-defined]
    except (OSError, AttributeError):   # AttributeError: not Windows
        pass
    try:
        s.close()
    except OSError:
        pass


def capture_supported(local_ip: str) -> bool:
    """Whether full TCP/UDP traceroute (raw ICMP capture) is available here."""
    try:
        s = _open_capture(local_ip)
    except OSError:
        return False
    _close_capture(s)
    return True


def probe(
    dest_ip: str,
    ip_ttl: int,
    timeout_ms: int,
    mode: str,
    port: int,
    local_ip: str,
    payload: bytes = b"",
    tos: int = 0,
) -> PingResult:
    """One TCP or UDP probe at the given IP TTL. ``mode`` is "tcp" or "udp".

    Without a capture socket (not elevated) TCP still detects reaching the
    destination, so TCP final-hop-only works; intermediate hops and UDP need it.
    """
    timeout = max(0.05, timeout_ms / 1000.0)
    try:
        cap: Optional[socket.socket] = _open_capture(local_ip)
    except OSError:
        cap = None

    probe_sock = None
    try:
        fam = socket.SOCK_STREAM if mode == "tcp" else socket.SOCK_DGRAM
        probe_sock = socket.socket(socket.AF_INET, fam)
        probe_sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, int(ip_ttl))
        _set_tos(probe_sock, tos)
        probe_sock.setblocking(False)
        try:
            probe_sock.bind((local_ip, 0))
        except OSError:
            pass

        start = time.perf_counter()
        if mode == "tcp":
            try:
                probe_sock.connect_ex((dest_ip, port))
            except OSError:
                pass
        else:
            try:
                probe_sock.sendto(payload or b"\x00", (dest_ip, port))
            except OSError:
                pass
        src_port = probe_sock.getsockname()[1]

        deadline = start + timeout
        result = PingResult(IP_REQ_TIMED_OUT, None, None, False)
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            rlist = [cap] if cap is not None else []
            wlist = [probe_sock] if mode == "tcp" else []
            if not rlist and not wlist:
                time.sleep(min(0.05, remaining))
                continue
            try:
                r, w, x = select.select(rlist, wlist, wlist, min(0.2, remaining))
            except OSError:
                break
            # Drain the capture socket BEFORE consulting the probe socket's
            # error. When an ICMP error aborts the connect, both become ready
            # in the same wakeup — and only the captured packet carries the
            # responder's address. Taking the socket error first threw that
            # away and recorded a bare timeout, losing exactly the answer the
            # probe exists to get: *which* box is refusing to forward.
            if r:
                try:
                    data, _addr = cap.recvfrom(2048)  # type: ignore[union-attr]
                except OSError:
                    data = None
                kind = _match(data, src_port, port, dest_ip, mode) if data else None
                if kind is not None:
                    responder = socket.inet_ntoa(data[12:16])  # source of the ICMP error
                    rtt = (time.perf_counter() - start) * 1000.0
                    if kind == "dest" and responder == dest_ip:
                        result = PingResult(IP_SUCCESS, rtt, responder, True)   # reached the target
                    else:
                        # TTL-exceeded router, or an unreachable from a mid-path
                        # firewall (not the target) — a responding hop, not the end.
                        result = PingResult(IP_TTL_EXPIRED_TRANSIT, rtt, responder, False)
                    break
            if mode == "tcp" and (w or x):
                err = probe_sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                result = _tcp_reach(err, (time.perf_counter() - start) * 1000.0, dest_ip)
                break
        return result
    finally:
        if probe_sock is not None:
            probe_sock.close()
        if cap is not None:
            _close_capture(cap)


def probe_path(
    dest_ip: str,
    ttls,
    timeout_ms: int,
    mode: str,
    port: int,
    local_ip: str,
    payload: bytes = b"",
    tos: int = 0,
) -> dict:
    """Probe all ``ttls`` in one round in parallel, sharing a single capture
    socket — so a round costs ~one timeout instead of the sum (much faster on
    paths with silent hops). Returns ``{ttl: PingResult}``. Falls back to serial
    single probes if the capture socket can't be opened (e.g. not elevated)."""
    ttls = list(ttls)
    try:
        cap = _open_capture(local_ip)
    except OSError:
        return {ttl: probe(dest_ip, ttl, timeout_ms, mode, port, local_ip, payload, tos)
                for ttl in ttls}

    timeout = max(0.05, timeout_ms / 1000.0)
    senders: dict = {}   # ttl -> [sock_or_None, start_time]
    key_ttl: dict = {}   # tcp: src_port -> ttl ; udp: dst_port -> ttl
    results: dict = {}
    try:
        for ttl in ttls:
            if mode == "tcp":
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, int(ttl))
                _set_tos(s, tos)
                s.setblocking(False)
                try:
                    s.bind((local_ip, 0))
                except OSError:
                    pass
                key = s.getsockname()[1]
                start = time.perf_counter()
                try:
                    s.connect_ex((dest_ip, port))
                except OSError:
                    pass
                senders[ttl] = [s, start]
            else:  # udp: a distinct destination port per hop correlates the reply
                # Wrapping to 0 makes sendto() fail with EINVAL, and that error
                # is caught and discarded below -- so the hop vanished from the
                # round with no trace. Keep the walk inside 1..65535.
                key = ((port + ttl - 1) % 65535) + 1
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, int(ttl))
                _set_tos(s, tos)
                start = time.perf_counter()
                try:
                    s.sendto(payload or b"\x00", (dest_ip, key))
                except OSError:
                    pass
                finally:
                    s.close()
                senders[ttl] = [None, start]
            key_ttl[key] = ttl

        pending = set(ttls)
        deadline = time.perf_counter() + timeout
        while pending and time.perf_counter() < deadline:
            wlist = [senders[t][0] for t in pending if senders[t][0] is not None]
            remaining = min(0.2, max(0.0, deadline - time.perf_counter()))
            try:
                r, w, x = select.select([cap], wlist, wlist, remaining)
            except OSError:
                break
            now = time.perf_counter()
            # Drain the capture socket first. An ICMP error that aborts a
            # connect makes both ready in the same wakeup, and only the
            # captured packet names the responder — reading the socket error
            # first would resolve that TTL as a bare timeout and the `not in
            # pending` guard below would then discard the router's identity.
            if cap in r:
                while True:
                    try:
                        data, _ = cap.recvfrom(2048)
                    except (BlockingIOError, OSError):
                        break
                    parsed = _parse(data)
                    if parsed is None:
                        continue
                    kind, proto, sp, dp, odst = parsed
                    # Require the error to echo OUR target — a receive-all socket
                    # sees every host's ICMP, and other monitors may share a port.
                    if proto != _PROTO[mode] or odst != dest_ip:
                        continue
                    ttl = key_ttl.get(sp if mode == "tcp" else dp)
                    if ttl is None or ttl not in pending:
                        continue
                    responder = socket.inet_ntoa(data[12:16])
                    rtt = (now - senders[ttl][1]) * 1000.0
                    if kind == "dest" and responder == dest_ip:
                        results[ttl] = PingResult(IP_SUCCESS, rtt, responder, True)
                    else:
                        results[ttl] = PingResult(IP_TTL_EXPIRED_TRANSIT, rtt, responder, False)
                    pending.discard(ttl)
            if mode == "tcp" and (w or x):
                for ttl in list(pending):
                    s = senders[ttl][0]
                    if s in w or s in x:
                        err = s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                        results[ttl] = _tcp_reach(err, (now - senders[ttl][1]) * 1000.0, dest_ip)
                        pending.discard(ttl)
        for ttl in pending:
            results[ttl] = PingResult(IP_REQ_TIMED_OUT, None, None, False)
        return results
    finally:
        for ttl in ttls:
            sock = senders.get(ttl, [None])[0]
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        _close_capture(cap)


def _parse(packet: bytes):
    """Parse an ICMP error datagram from the capture socket. Returns
    ``(kind, orig_proto, orig_src_port, orig_dst_port, orig_dst_ip)`` or None,
    where kind is "ttl" (TTL exceeded) or "dest" (destination unreachable). The
    echoed original *destination IP* lets a caller confirm the error pertains to
    its own probe — essential when a receive-all socket sees every host's ICMP
    (and several monitors run at once)."""
    if len(packet) < 28:
        return None
    if packet[9] != IPPROTO_ICMP:  # outer IP protocol must be ICMP
        return None
    ihl = (packet[0] & 0x0F) * 4
    icmp = packet[ihl:]
    if len(icmp) < 8:
        return None
    itype = icmp[0]
    if itype not in (ICMP_TYPE_TTL_EXCEEDED, ICMP_TYPE_DEST_UNREACH):
        return None
    orig = icmp[8:]  # the original IP packet that triggered the error
    if len(orig) < 20:
        return None
    orig_ihl = (orig[0] & 0x0F) * 4
    trans = orig[orig_ihl:]
    if len(trans) < 4:
        return None
    kind = "ttl" if itype == ICMP_TYPE_TTL_EXCEEDED else "dest"
    return (kind, orig[9], int.from_bytes(trans[0:2], "big"),
            int.from_bytes(trans[2:4], "big"), socket.inet_ntoa(orig[16:20]))


def _match(packet: bytes, src_port: int, dst_port: int, dest_ip: str, mode: str) -> Optional[str]:
    """If ``packet`` is the ICMP error for *our* single probe (matching ports,
    protocol, and the original destination IP), return "ttl"/"dest"; else None."""
    parsed = _parse(packet)
    if parsed is None:
        return None
    kind, proto, sp, dp, odst = parsed
    if proto != _PROTO[mode] or sp != src_port or dp != dst_port or odst != dest_ip:
        return None
    return kind
