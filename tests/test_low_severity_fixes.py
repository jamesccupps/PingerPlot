"""The remaining low-severity audit findings: F7, F10, F11.

Each is small, none would hold a release, and each is the kind of thing that
becomes expensive to diagnose later precisely because it fails quietly.
"""
import json
import socket
import sys

import pytest

from pingerplot import geoip, tcpudp
from mock_router import MockCapture


# --- F7: the UDP port walk must not wrap to zero ---------------------------
#
# probe_path needs a per-TTL correlation key and, for UDP, uses a distinct
# destination port per hop -- textbook UDP traceroute and the right technique
# for a parallel round. But (port + ttl) & 0xFFFF wraps: with port=65535 the
# first hop computes 0, sendto(..., 0) raises EINVAL, and that error is caught
# and discarded -- so the hop vanished from the round with nothing to show for
# it.

class _PortSpy:
    """Record every destination port a UDP round sends to."""

    def __init__(self):
        self.ports = []

    def install(self, monkeypatch):
        spy = self
        real = socket.socket

        class _Sock:
            def __init__(self, *_a, **_kw):
                pass

            def setsockopt(self, *_a):
                pass

            def sendto(self, _data, addr):
                spy.ports.append(addr[1])
                return 1

            def close(self):
                pass

        monkeypatch.setattr(tcpudp.socket, "socket",
                            lambda f, t, *a: _Sock() if t == socket.SOCK_DGRAM
                            else real(f, t, *a))
        return self


def test_udp_rounds_walk_the_port_upward(capture_fixture, monkeypatch):
    spy = _PortSpy().install(monkeypatch)
    tcpudp.probe_path("203.0.113.9", [1, 2, 3], timeout_ms=100, mode="udp",
                      port=33434, local_ip="0.0.0.0")
    assert spy.ports == [33435, 33436, 33437]


def test_the_walk_never_produces_port_zero(capture_fixture, monkeypatch):
    """port=65535 is accepted by the Engine dialog's spinbox (from_=1,
    to=65535), so this is reachable from the UI, not a theoretical input."""
    spy = _PortSpy().install(monkeypatch)
    tcpudp.probe_path("203.0.113.9", [1, 2, 3], timeout_ms=100, mode="udp",
                      port=65535, local_ip="0.0.0.0")
    assert 0 not in spy.ports, "sendto(..., 0) raises EINVAL and loses the hop"
    assert all(1 <= p <= 65535 for p in spy.ports), spy.ports
    assert len(set(spy.ports)) == 3, "each hop still needs a distinct key"


def test_the_walk_stays_in_range_from_every_starting_port(capture_fixture, monkeypatch):
    for start in (1, 1024, 33434, 65000, 65534, 65535):
        spy = _PortSpy().install(monkeypatch)
        tcpudp.probe_path("203.0.113.9", range(1, 33), timeout_ms=50, mode="udp",
                          port=start, local_ip="0.0.0.0")
        assert all(1 <= p <= 65535 for p in spy.ports), (start, spy.ports)
        assert len(set(spy.ports)) == 32, (start, spy.ports)


# --- F10: the Windows-only ioctl must not escape as AttributeError ---------

@pytest.mark.skipif(sys.platform == "win32", reason="SIO_RCVALL exists here")
def test_capture_is_refused_as_an_oserror_off_windows():
    """Every caller guards _open_capture with `except OSError`, which does not
    catch AttributeError. Where a POSIX host CAN open a raw IPPROTO_IP socket,
    referencing socket.SIO_RCVALL would sail past capture_supported() and reach
    _run's broad handler as an obscure "Monitor error: AttributeError(...)"
    instead of the clear "needs Administrator" message.

    Unreachable on Linux -- the raw socket fails with EPROTONOSUPPORT first --
    but unverified on macOS, which has had an ICMP backend since 1.3.0."""
    with pytest.raises(OSError):
        tcpudp._open_capture("127.0.0.1")
    assert tcpudp.capture_supported("127.0.0.1") is False


def test_capture_is_refused_when_the_ioctl_is_absent(monkeypatch):
    """The platform-independent version: hide SIO_RCVALL and check the failure
    is an OSError, so the Windows CI leg covers this too.

    Asserting only on the exception type would pass vacuously on an unelevated
    Windows box, where socket(AF_INET, SOCK_RAW, IPPROTO_IP) fails with a
    permission OSError long before the ioctl is reached. So this also asserts
    the guard fired FIRST -- that no socket was opened at all."""
    opened = []
    real = tcpudp.socket.socket
    monkeypatch.setattr(tcpudp.socket, "socket",
                        lambda *a, **kw: opened.append(a) or real(*a, **kw))
    monkeypatch.delattr(tcpudp.socket, "SIO_RCVALL", raising=False)

    with pytest.raises(OSError) as exc:
        tcpudp._open_capture("127.0.0.1")
    assert opened == [], "the guard must refuse before opening a raw socket"
    assert "Windows" in str(exc.value)
    assert tcpudp.capture_supported("127.0.0.1") is False


def test_closing_a_capture_socket_survives_a_missing_ioctl(monkeypatch):
    """_close_capture runs in a finally: an AttributeError there would mask
    whatever the round was actually returning."""
    monkeypatch.delattr(tcpudp.socket, "SIO_RCVALL", raising=False)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tcpudp._close_capture(s)                       # must not raise
    assert s.fileno() == -1, "the socket should still have been closed"


# --- F11: the third-party response body must be capped ---------------------

def test_the_geo_response_is_read_with_a_bound(monkeypatch):
    asked = []

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False

        def read(self, n=None):
            asked.append(n)
            return json.dumps({"success": True, "latitude": 1.0,
                               "longitude": 2.0}).encode()

    monkeypatch.setattr(geoip.urllib.request, "urlopen", lambda req, timeout=0: _Resp())
    assert geoip.GeoResolver()._lookup("8.8.8.8") is not None
    assert asked == [geoip._MAX_BODY], f"read() called with {asked}"


def test_an_oversized_body_does_not_become_a_result(monkeypatch):
    """Truncated JSON does not parse, so the cap degrades to 'no data' rather
    than to a partly-decoded object."""
    payload = b'{"success": true, "latitude": 1.0, "longitude": 2.0, "pad": "'
    payload += b"x" * (geoip._MAX_BODY * 2) + b'"}'

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=None): return payload[:n] if n else payload

    monkeypatch.setattr(geoip.urllib.request, "urlopen", lambda req, timeout=0: _Resp())
    assert geoip.GeoResolver()._lookup("8.8.8.8") is None


def test_the_cap_is_generous_enough_for_a_real_reply():
    """A location object is a few hundred bytes. The cap must not be so tight
    that a legitimate reply with a long ISP name gets truncated."""
    realistic = json.dumps({
        "success": True, "latitude": 43.6591, "longitude": -70.2568,
        "city": "Portland", "region": "Maine", "country": "United States",
        "connection": {"isp": "A" * 200, "org": "B" * 200},
    }).encode()
    assert len(realistic) < geoip._MAX_BODY / 10


@pytest.fixture
def capture_fixture(monkeypatch):
    c = MockCapture()
    c.install(monkeypatch, tcpudp)
    yield c
    c.close()
