"""POSIX RTTs come from the kernel's receive timestamp, not from Python.

The Windows backend is immune to this: IcmpSendEcho's driver measures the
round trip. The POSIX backend read time.perf_counter() after recvfrom
returned -- and between the reply arriving and that line running, the probe
thread must win the GIL back, possibly several times (out of select, into
recv, out of recv). Anything else busy in the process counts as network
latency. Measured on a namespace lab: a 0.17 ms path read 11.9 ms average,
26 ms worst and 6.5 ms jitter with one busy Python thread alongside, and a
Timeline redraw holds the GIL for ~56 ms. Those numbers fed the latency alert
and MOS.

With SO_TIMESTAMPNS the kernel stamps each received packet -- the echo reply
and the error-queue TTL-exceeded alike -- and the RTT is that stamp minus a
time.time_ns() taken immediately before sendto.
"""
import socket
import statistics
import struct
import sys
import threading
import time

import pytest

from pingerplot import icmp, icmp_posix

TS = 35   # asm-generic SO_TIMESTAMPNS, forced on for the platform-neutral tests
_TIMESPEC = struct.Struct("@ll")


def _stamp(ns):
    return (socket.SOL_SOCKET, TS, _TIMESPEC.pack(ns // 1_000_000_000, ns % 1_000_000_000))


@pytest.fixture
def stamps_on(monkeypatch):
    monkeypatch.setattr(icmp_posix, "_SO_TIMESTAMPNS", TS)


def test_the_rtt_is_the_kernel_stamp_minus_the_send_time(stamps_on):
    sent = 1_700_000_000_000_000_000
    assert icmp_posix.kernel_rtt_ms([_stamp(sent + 1_500_000)], sent) == pytest.approx(1.5)


def test_other_control_messages_are_ignored(stamps_on):
    sent = 1_700_000_000_000_000_000
    anc = [(socket.IPPROTO_IP, icmp_posix.IP_RECVERR, b"\x00" * 24), _stamp(sent + 250_000)]
    assert icmp_posix.kernel_rtt_ms(anc, sent) == pytest.approx(0.25)


@pytest.mark.parametrize("delta_ns", [-5_000_000, 120 * 1_000_000_000])
def test_a_stamp_from_a_stepped_clock_is_not_trusted(stamps_on, delta_ns):
    """Both clocks are CLOCK_REALTIME, which NTP can step. A negative or
    absurd result means that happened; fall back rather than record it."""
    sent = 1_700_000_000_000_000_000
    assert icmp_posix.kernel_rtt_ms([_stamp(sent + delta_ns)], sent) is None


def test_no_stamp_means_no_kernel_rtt(stamps_on):
    assert icmp_posix.kernel_rtt_ms([], 1) is None
    assert icmp_posix.kernel_rtt_ms([(socket.SOL_SOCKET, TS, b"\x01")], 1) is None


def test_without_the_option_nothing_is_parsed(monkeypatch):
    monkeypatch.setattr(icmp_posix, "_SO_TIMESTAMPNS", None)
    assert icmp_posix.kernel_rtt_ms([_stamp(2_000_000)], 1_000_000) is None


class _SlowReader:
    """Delivers its message only after the reader has been held up -- the GIL
    stall in miniature. A user-space clock would read the hold-up as latency."""

    def __init__(self, data, ancdata, addr=("203.0.113.10", 0)):
        self.data, self.ancdata, self.addr = data, ancdata, addr

    def recvmsg(self, _n, _anc, flags=0):
        time.sleep(0.05)
        return self.data, self.ancdata, 0, self.addr


def test_an_echo_reply_is_timed_by_the_kernel_not_by_when_python_read_it(stamps_on, monkeypatch):
    monkeypatch.setattr(icmp_posix, "_LINUX", True)
    sent = time.time_ns()
    reply = struct.pack("!BBHHH", icmp_posix.ICMP_ECHO_REPLY, 0, 0, 1, 42) + b"x"
    r = icmp_posix._read_reply(_SlowReader(reply, [_stamp(sent + 800_000)]),
                               "203.0.113.10", time.perf_counter(), 1, 42, sent_ns=sent)
    assert r is not None and r.reached
    assert r.rtt_ms == pytest.approx(0.8), "RTT included the time Python took to read it"


def test_a_ttl_exceeded_is_timed_by_the_kernel_too(stamps_on, monkeypatch):
    monkeypatch.setattr(icmp_posix, "_LINUX", True)
    sent = time.time_ns()
    ee = struct.pack("=IBBBBII", 113, icmp_posix.SO_EE_ORIGIN_ICMP,
                     icmp_posix.ICMP_TIME_EXCEEDED, 0, 0, 0, 0)
    ee += struct.pack("!HH4s8x", socket.AF_INET, 0, socket.inet_aton("198.51.100.1"))
    anc = [(socket.IPPROTO_IP, icmp_posix.IP_RECVERR, ee), _stamp(sent + 3_000_000)]
    r = icmp_posix._read_errqueue(_SlowReader(b"", anc), time.perf_counter(), sent_ns=sent)
    assert r is not None and r.address == "198.51.100.1"
    assert r.rtt_ms == pytest.approx(3.0)


# --- live: the whole point, on a real kernel -----------------------------------

LIVE = pytest.mark.skipif(
    not (sys.platform.startswith("linux") and icmp_posix.is_available()),
    reason="needs Linux with an unprivileged ICMP socket")


@LIVE
def test_kernel_timestamps_are_available_on_this_linux():
    """Otherwise the next test proves nothing: it would pass or fail on the
    user-space clock."""
    assert icmp_posix._SO_TIMESTAMPNS is not None, \
        f"no SO_TIMESTAMPNS mapping for this architecture"


@LIVE
def test_a_busy_python_thread_does_not_show_up_as_latency():
    stop = threading.Event()

    def spin():
        n = 0
        while not stop.is_set():
            n += 1

    busy = threading.Thread(target=spin, daemon=True)
    busy.start()
    try:
        rtts = [icmp_posix.ping("127.0.0.1", ttl=64, timeout_ms=2000).rtt_ms for _ in range(15)]
    finally:
        stop.set()
        busy.join()
    assert all(r is not None for r in rtts), rtts
    # Loopback is tens of microseconds. GIL-inflated readings were ~10 ms.
    assert statistics.median(rtts) < 3.0, rtts
    assert icmp.ping("127.0.0.1", ttl=64, timeout_ms=2000).reached
