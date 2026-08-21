"""A TCP probe must be given long enough to learn that a port is closed.

Windows does not hand the RST to a connecting socket the moment it arrives — it
finishes retransmitting the SYN first, and only then surfaces WSAECONNREFUSED.
Measured on Windows 11: about 2.0 s. Until then the socket is neither writable
nor in the exception set, so the probe simply times out.

The engine's default reply timeout is 1000 ms, which is under that window. The
consequence is the worst kind of wrong answer: a host that is up, answering,
and merely not running the service reads as *unreachable*. The README sells TCP
mode as the definitive "is this service up" check and says to prefer TCP for a
definitive answer — and at the shipped default it was definitively wrong in the
single most common diagnostic case.

Shortening the retransmit window with TCP_MAXRT does not help; it was measured
too. It aborts the connect early but replaces WSAECONNREFUSED (10061, "the host
answered") with WSAETIMEDOUT (10060, "nothing came back"), destroying the very
distinction the probe exists to make:

    default            except  SO_ERROR=10061  after 2044ms
    TCP_MAXRT=1        except  SO_ERROR=10060  after  508ms

So the only correct move is to wait it out, and to make sure the engine does.
"""
import select
import socket
import sys
import time

import pytest

from pingerplot import monitor, tcpudp
from pingerplot.monitor import Monitor
from mock_router import MockCapture, closed_tcp_port

WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32",
    reason="the RST-retransmit delay is Windows TCP behaviour; POSIX reports "
           "ECONNREFUSED on the first RST",
)


# --- the constant has to actually clear the window it exists for -----------

@WINDOWS_ONLY
def test_the_floor_exceeds_the_measured_refusal_delay():
    """Measure it rather than trusting the number. If a future Windows changes
    the retransmit schedule this fails loudly instead of silently going back to
    misreporting closed ports."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setblocking(False)
    port = closed_tcp_port()
    t0 = time.perf_counter()
    s.connect_ex(("127.0.0.1", port))
    err = 0
    while time.perf_counter() - t0 < 10.0:
        _r, w, x = select.select([], [s], [s], 0.1)
        if w or x:
            err = s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            break
    elapsed_ms = (time.perf_counter() - t0) * 1000
    s.close()

    assert err in tcpudp._CLEAN_REACH_ERRORS, (
        f"a refused connect reported {err}, which the code does not count as "
        f"proof the host answered"
    )
    assert monitor.TCP_REFUSAL_FLOOR_MS > elapsed_ms, (
        f"refusal took {elapsed_ms:.0f} ms but the floor is only "
        f"{monitor.TCP_REFUSAL_FLOOR_MS} ms"
    )


# --- what the engine does with the setting ---------------------------------

def _started(**kw):
    """Apply start()'s clamping without launching the worker thread."""
    m = Monitor()
    m._run = lambda gen: None
    m.start("127.0.0.1", **kw)
    m.stop()
    return m


def test_tcp_timeout_is_raised_to_the_floor():
    m = _started(packet_type="tcp", timeout_ms=1000, port=80)
    try:
        assert m.timeout_ms >= monitor.TCP_REFUSAL_FLOOR_MS
    finally:
        m.shutdown()


def test_a_generous_tcp_timeout_is_left_alone():
    m = _started(packet_type="tcp", timeout_ms=8000, port=80)
    try:
        assert m.timeout_ms == 8000
    finally:
        m.shutdown()


@pytest.mark.parametrize("ptype", ["icmp", "udp"])
def test_other_modes_keep_the_timeout_they_were_given(ptype):
    """ICMP gets its answer from IcmpSendEcho and UDP from the capture socket;
    neither waits on a TCP connect, so neither should be slowed down."""
    m = _started(packet_type=ptype, timeout_ms=500)
    try:
        assert m.timeout_ms == 500
    finally:
        m.shutdown()


def test_the_adjustment_is_visible_to_the_user():
    """Silently overriding a setting the user typed is its own bug. The status
    line has to say the timeout was raised and why."""
    m = _started(packet_type="tcp", timeout_ms=1000, port=80)
    try:
        assert m.timeout_note
        assert "1000" in m.timeout_note and str(m.timeout_ms) in m.timeout_note
    finally:
        m.shutdown()


def test_no_note_when_nothing_was_adjusted():
    m = _started(packet_type="tcp", timeout_ms=8000, port=80)
    try:
        assert m.timeout_note == ""
    finally:
        m.shutdown()


# --- the behaviour the floor exists to protect -----------------------------

@WINDOWS_ONLY
def test_a_closed_port_reads_as_reached_at_the_engine_default(monkeypatch):
    """The regression, end to end: the default reply timeout must be enough to
    tell "service down, host fine" from "host unreachable"."""
    MockCapture().deny(monkeypatch, tcpudp)
    m = _started(packet_type="tcp", timeout_ms=1000, port=closed_tcp_port())
    try:
        r = tcpudp.probe("127.0.0.1", 64, m.timeout_ms, "tcp", m.port, "127.0.0.1")
        assert r.reached, "a refused port still reads as an unreachable host"
        assert r.address == "127.0.0.1"
    finally:
        m.shutdown()


@WINDOWS_ONLY
def test_the_old_default_really_did_get_it_wrong():
    """Pins the defect itself, so the floor can never be quietly removed as
    'probably unnecessary'."""
    r = tcpudp.probe("127.0.0.1", 64, 1000, "tcp", closed_tcp_port(), "127.0.0.1")
    assert not r.reached, (
        "1000 ms now resolves a refused connect — re-measure the window and "
        "revisit whether the floor is still needed"
    )
