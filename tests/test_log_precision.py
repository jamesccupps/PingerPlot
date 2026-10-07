"""The probe CSV must keep sub-millisecond RTTs.

_log_probe wrote ``f"{rtt:.0f}"``. The Windows ICMP API reports whole
milliseconds, so nothing was lost there -- but the POSIX backend and the
TCP/UDP modes time in Python at sub-millisecond resolution, and a LAN hop at
0.17 ms logged as "0". Whole-millisecond values must still log exactly as
before, so existing Windows logs do not change shape.
"""
from pingerplot import icmp
from pingerplot.monitor import Monitor


def _logged(tmp_path, rtt):
    m = Monitor()
    m.log_path = str(tmp_path / "probe.csv")
    m._open_log()
    m._log_probe(1, icmp.PingResult(icmp.IP_SUCCESS, rtt, "192.0.2.1", True))
    m._close_log()
    row = (tmp_path / "probe.csv").read_text(encoding="utf-8").splitlines()[1]
    return row.split(",")[5]


def test_sub_millisecond_rtt_is_kept(tmp_path):
    assert _logged(tmp_path, 0.1734) == "0.173"


def test_whole_milliseconds_log_as_before(tmp_path):
    assert _logged(tmp_path, 12.0) == "12"


def test_zero_is_zero(tmp_path):
    assert _logged(tmp_path, 0.0) == "0"
