"""What a full TCP/UDP traceroute says when it can't capture: the fix that
exists on this platform, in the 1.4 window's words.

1.4 renamed the options ("Final hop only" is Destination only, the Engine
dialog is the Settings page) but the engine's messages kept the old names;
and on Linux "Re-run elevated" sent people to sudo for a capture that
doesn't exist there at any privilege."""
import time

import pytest

from pingerplot import monitor, tcpudp
from pingerplot.monitor import Monitor


def _refusal(monkeypatch, platform):
    monkeypatch.setattr(tcpudp, "capture_supported", lambda local_ip: False)
    monkeypatch.setattr(monitor.sys, "platform", platform)
    m = Monitor()
    m.start("127.0.0.1", interval=0.3, timeout_ms=400, packet_type="udp")
    try:
        end = time.monotonic() + 6
        while m.running and time.monotonic() < end:
            time.sleep(0.05)
        return m.status
    finally:
        m.shutdown()


def test_on_windows_it_says_run_as_administrator(monkeypatch):
    status = _refusal(monkeypatch, "win32")
    assert "needs Administrator" in status and "Run PingerPlot as administrator" in status
    assert "Destination only" in status


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_elsewhere_it_says_windows_only_not_elevate(monkeypatch, platform):
    status = _refusal(monkeypatch, platform)
    assert "Windows-only" in status and "Switch to ICMP" in status and "Destination only" in status
    assert "Administrator" not in status and "elevat" not in status


def test_no_message_names_a_1_3_option():
    src = (monitor.__file__)
    text = open(src, encoding="utf-8").read()
    for old in ("Engine options", "'Final hop only'", "Re-run elevated"):
        assert old not in text, f"monitor.py still says {old!r}"
