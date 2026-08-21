"""A stand-in for the ``SIO_RCVALL`` capture socket, plus a builder for the
ICMP error datagrams routers actually put on the wire.

Why this exists
---------------
``probe()`` and ``probe_path()`` were the only completely unexercised code in
the package: 25% coverage on tcpudp.py, with every line of the round loop and
the reply correlation untested. That is the same shape of gap that let two
protocol scans ship broken in a sibling project — parser unit tests passed
while nothing had ever run a socket.

The real capture socket needs Administrator (``SIO_RCVALL``) and a live path.
A plain loopback UDP socket behaves identically for everything under test: it
is a real socket, so ``select()`` and ``recvfrom()`` are the genuine calls, and
whatever bytes we send it arrive exactly as written. Feed it router-shaped IPv4
datagrams and the correlation code cannot tell the difference.

Packet shapes follow RFC 792: outer IPv4 header, 8-byte ICMP header, then the
IP header of the offending datagram plus at least its first 8 transport bytes.
``ihl`` is a parameter on both headers because real gear does emit IP options
and a fixed-20-byte assumption would decode those as garbage.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

ICMP_TTL_EXCEEDED = 11
ICMP_DEST_UNREACH = 3
PROTO_ICMP = 1

DEST = "203.0.113.9"      # RFC 5737 TEST-NET-3: the target under test
LOCAL = "192.0.2.99"      # RFC 5737 TEST-NET-1: stands in for our own address


def ipv4(src: str, dst: str, proto: int, payload: bytes, ihl_words: int = 5) -> bytes:
    """One IPv4 datagram. ``ihl_words > 5`` means the caller has prefixed
    ``(ihl_words - 5) * 4`` bytes of IP options onto ``payload``."""
    header = struct.pack(
        "!BBHHHBBH4s4s",
        0x40 | ihl_words, 0, ihl_words * 4 + len(payload), 0x1234, 0,
        64, proto, 0, socket.inet_aton(src), socket.inet_aton(dst),
    )
    return header + payload


def icmp_error(itype: int, quoted: bytes) -> bytes:
    """ICMP header (type, code 0, checksum, 4 unused bytes) + the quoted
    original datagram."""
    return struct.pack("!BBHI", itype, 0, 0, 0) + quoted


def quoted_probe(proto: int, src_port: int, dst_port: int,
                 dst_ip: str = DEST, src_ip: str = LOCAL,
                 ihl_words: int = 5) -> bytes:
    """The original probe as a router quotes it back: its IP header plus the
    first 8 bytes of its transport header (ports are all the correlation
    needs)."""
    options = b"\x01" * ((ihl_words - 5) * 4)          # NOP padding
    transport = struct.pack("!HHI", src_port, dst_port, 0)
    return ipv4(src_ip, dst_ip, proto, options + transport, ihl_words)


def router_reply(router_ip: str, itype: int, proto: int, src_port: int,
                 dst_port: int, dst_ip: str = DEST, outer_ihl: int = 5,
                 inner_ihl: int = 5) -> bytes:
    """A complete ICMP error as it lands on the capture socket."""
    body = icmp_error(itype, quoted_probe(proto, src_port, dst_port,
                                          dst_ip, ihl_words=inner_ihl))
    options = b"\x01" * ((outer_ihl - 5) * 4)
    return ipv4(router_ip, LOCAL, PROTO_ICMP, options + body, outer_ihl)


class MockCapture:
    """A real loopback UDP socket posing as the raw capture socket.

    Install it with :meth:`install` so ``probe``/``probe_path`` receive it from
    ``_open_capture``; then :meth:`send_after` queues router replies. Nothing
    is faked below the socket layer — select, recvfrom and the timeouts are all
    the production code paths.
    """

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.setblocking(False)
        self.addr = self.sock.getsockname()
        self.closed = False
        self._threads: list[threading.Thread] = []

    def install(self, monkeypatch, module) -> "MockCapture":
        monkeypatch.setattr(module, "_open_capture", lambda _local_ip: self.sock)
        monkeypatch.setattr(module, "_close_capture", self._note_close)
        return self

    def deny(self, monkeypatch, module) -> None:
        """Make _open_capture fail the way it does without Administrator, so
        the serial fallback path is what runs."""
        def _refused(_local_ip):
            raise OSError(10013, "An attempt was made to access a socket in a "
                                 "way forbidden by its access permissions")
        monkeypatch.setattr(module, "_open_capture", _refused)

    def _note_close(self, _sock) -> None:
        # Record rather than actually close: the fixture owns the socket's
        # lifetime, and a test may run several rounds through it.
        self.closed = True

    def send_after(self, packets, delay: float = 0.02) -> None:
        """Deliver ``packets`` to the capture socket after a short delay, the
        way a router's reply arrives some milliseconds into the round."""
        def run():
            time.sleep(delay)
            tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                for p in packets:
                    tx.sendto(p, self.addr)
            finally:
                tx.close()
        t = threading.Thread(target=run, daemon=True)
        t.start()
        self._threads.append(t)

    def close(self) -> None:
        for t in self._threads:
            t.join(timeout=2)
        self.sock.close()


class LoopbackService:
    """A real TCP listener, for the branch where the connect itself proves the
    destination answered (which is how "TCP, final hop only" works with no
    elevation and no capture socket at all)."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (OSError, socket.timeout):
                continue
            conn.close()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self.sock.close()


def closed_tcp_port() -> int:
    """An ephemeral port with nothing listening: a connect there is refused
    with RST, which still proves the host answered."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
