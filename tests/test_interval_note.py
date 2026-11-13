"""An interval shorter than a round can last is explained, not silently ignored.

A round waits for every hop's answer, so one silent hop holds it to the reply
timeout and the next round starts straight after. In TCP mode the timeout is
raised to at least 3 s (TCP_REFUSAL_FLOOR_MS), so "probe every 1 s" ran every
3 s: a NetSentinel capture showed one target's TTL-1 probes exactly 3.0 s
apart. The setting did nothing and nothing said so.
"""
import time

import pytest

from pingerplot import icmp, monitor
from pingerplot.monitor import Monitor

needs_icmp = pytest.mark.skipif(not icmp.is_available(),
                                reason=f"needs a working ICMP backend: {icmp.unavailable_reason()}")


def _started(**kw):
    """Apply start()'s clamping without launching the worker thread."""
    m = Monitor()
    m._run = lambda gen: None
    m.start("127.0.0.1", **kw)
    m.stop()
    return m


def test_a_tcp_interval_under_the_floor_is_explained():
    m = _started(packet_type="tcp", port=80, interval=1.0, timeout_ms=1000)
    try:
        note = m.interval_note
        assert "every 1 s" in note
        assert f"{monitor.TCP_REFUSAL_FLOOR_MS} ms" in note, "should name the timeout it waits for"
        assert "about every 3 s" in note
    finally:
        m.shutdown()


def test_an_icmp_interval_under_its_own_timeout_is_explained_too():
    """ICMP rounds wait for every hop as well; only the defaults never meet."""
    m = _started(packet_type="icmp", interval=0.5, timeout_ms=1000)
    try:
        assert "every 0.5 s" in m.interval_note and "1000 ms" in m.interval_note
    finally:
        m.shutdown()


@pytest.mark.parametrize("kw", [
    {"packet_type": "icmp"},                                   # 2.5 s, 1000 ms
    {"packet_type": "tcp", "port": 80, "interval": 3.0},       # equal is fine
    {"packet_type": "tcp", "port": 80, "interval": 5.0},
])
def test_no_note_when_the_interval_can_be_kept(kw):
    m = _started(**kw)
    try:
        assert m.interval_note == ""
    finally:
        m.shutdown()


@needs_icmp
def test_the_note_goes_to_the_events_tab():
    m = Monitor()
    m.start("127.0.0.1", interval=0.3, timeout_ms=400, final_hop_only=True)
    try:
        end = time.monotonic() + 8.0
        found = []
        while time.monotonic() < end and not found:
            found = [e for e in m.events_after(-1)[0] if e.text == m.interval_note]
            time.sleep(0.02)
        assert found, "the note never reached the event log"
        assert {e.kind for e in found} == {"info"}, "a note must not beep or fire a webhook"
    finally:
        m.shutdown()
