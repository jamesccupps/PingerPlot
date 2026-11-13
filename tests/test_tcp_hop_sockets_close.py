"""A TCP hop's socket closes as soon as the hop has answered.

Windows keeps resending the SYN of a connecting socket (+1 s, then +3 s) even
after a router has returned "time exceeded" for it, and in TCP mode a round
lasts until the last hop answers or the 3 s timeout floor runs out. With every
socket left open until the round ended, one silent hop made the answered ones
go out two or three times: a capture of three TCP targets counted 26 SYNs a
second where 12 would do, and the routers answered every copy.

The probe sockets here are stand-ins that record when they were closed, so no
SYN leaves the machine. The capture socket is the usual loopback stand-in.
"""
import select
import socket
import threading
import time
import types

import pytest

from pingerplot import icmp, tcpudp
from mock_router import DEST, ICMP_TTL_EXCEEDED, MockCapture, router_reply

ROUTER = "198.51.100.1"     # RFC 5737 TEST-NET-2
TIMEOUT_MS = 1500           # long enough that "closed at once" can't be luck


class _ProbeSock:
    """A connecting TCP socket that never completes unless told to."""

    def __init__(self, port, closes):
        self.port = port
        self._closes = closes
        self.so_error = None    # set to finish the connect

    def setsockopt(self, *_a):
        pass

    def setblocking(self, _flag):
        pass

    def bind(self, _addr):
        pass

    def getsockname(self):
        return ("0.0.0.0", self.port)

    def connect_ex(self, _addr):
        return 115              # EINPROGRESS

    def getsockopt(self, *_a):
        return self.so_error

    def close(self):
        self._closes.append((self.port, time.perf_counter()))


@pytest.fixture
def round_(monkeypatch):
    """Patch tcpudp to probe through _ProbeSock and the loopback capture.

    Only tcpudp's view of the socket module changes, so the capture's own
    sender thread still gets real sockets."""
    cap = MockCapture()
    cap.install(monkeypatch, tcpudp)
    closes, socks = [], []

    def _factory(_family, _type, *_a):
        s = _ProbeSock(41000 + len(socks) + 1, closes)
        socks.append(s)
        return s

    fake = types.SimpleNamespace(**{k: getattr(socket, k) for k in dir(socket)
                                    if not k.startswith("__")})
    fake.socket = _factory
    monkeypatch.setattr(tcpudp, "socket", fake)

    real_select = select.select

    def _select(rlist, wlist, xlist, timeout=None):
        # The capture socket is real; a probe socket is ready only once the
        # test has given it a result.
        ready = [s for s in wlist if s.so_error is not None]
        if ready:
            return [], ready, []
        r, _, _ = real_select([s for s in rlist if s is cap.sock], [], [], timeout)
        return r, [], []

    monkeypatch.setattr(tcpudp.select, "select", _select)
    yield types.SimpleNamespace(cap=cap, closes=closes, socks=socks)
    cap.close()


def _run(ttls):
    t0 = time.perf_counter()
    res = tcpudp.probe_path(DEST, ttls, timeout_ms=TIMEOUT_MS, mode="tcp",
                            port=443, local_ip="0.0.0.0")
    return t0, res


def test_a_hop_that_answered_closes_its_socket_at_once(round_):
    # TTL 1 is the first socket made, so port 41001; its router answers 50 ms
    # in. TTLs 2 and 3 stay silent and hold the round to its deadline.
    round_.cap.send_after([router_reply(ROUTER, ICMP_TTL_EXCEEDED,
                                        socket.IPPROTO_TCP, 41001, 443)], delay=0.05)
    t0, res = _run([1, 2, 3])

    assert res[1].address == ROUTER and res[1].status == icmp.IP_TTL_EXPIRED_TRANSIT
    assert res[2].status == res[3].status == icmp.IP_REQ_TIMED_OUT
    closed = dict(round_.closes)
    assert closed[41001] - t0 < 0.5, (
        f"TTL 1 answered at ~50 ms but its socket stayed open "
        f"{closed[41001] - t0:.2f} s, resending its SYN until the round ended"
    )
    assert min(closed[41002], closed[41003]) - t0 >= TIMEOUT_MS / 1000 - 0.05


def test_a_hop_that_connected_closes_its_socket_at_once(round_):
    """The other way a hop answers: the connect itself completes or is
    refused (the destination). Same rule."""
    def finish_ttl_2_soon():
        time.sleep(0.05)
        round_.socks[1].so_error = 10061       # refused: the host answered
    threading.Thread(target=finish_ttl_2_soon, daemon=True).start()

    t0, res = _run([1, 2])

    assert res[2].reached
    closed = dict(round_.closes)
    assert closed[41002] - t0 < 0.5
    assert closed[41001] - t0 >= TIMEOUT_MS / 1000 - 0.05


def test_every_socket_is_closed_exactly_once(round_):
    round_.cap.send_after([router_reply(ROUTER, ICMP_TTL_EXCEEDED,
                                        socket.IPPROTO_TCP, 41002, 443)], delay=0.02)
    _run([1, 2, 3, 4])
    ports = [p for p, _ in round_.closes]
    assert sorted(ports) == [41001, 41002, 41003, 41004]
