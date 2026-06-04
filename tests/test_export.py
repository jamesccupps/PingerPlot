"""Tests for run_summary() (export metadata), config restore on load, and the
CSV formula-injection defang used by the export."""
import pytest

from pingerplot.model import Hop, Sample, csv_safe
from pingerplot.monitor import Monitor


@pytest.mark.parametrize("evil", [
    "=cmd|'/c calc'!A1",          # classic DDE command execution
    "=1+1",
    "+1+1",
    "-1+1",
    "@SUM(1+1)",
    "\t=1+1",                      # leading tab — some apps still parse it
    "\r=1+1",                      # leading CR
])
def test_csv_safe_defangs_formula_triggers(evil):
    out = csv_safe(evil)
    assert out.startswith("'")
    assert out[1:] == evil          # value preserved, just neutralised
    assert out[0] not in "=+-@"     # no longer a live formula


@pytest.mark.parametrize("ok", [
    "",                             # empty passes through
    "8.8.8.8",                      # an IPv4 address
    "dns.google",                   # a normal hostname
    "host-1.example.net",           # internal hyphen is fine
    "router.isp",
])
def test_csv_safe_passes_plain_values(ok):
    assert csv_safe(ok) == ok


def test_run_summary_reports_conditions_and_window():
    m = Monitor()
    m.interval = 2.5
    m.timeout_ms = 1500
    m.packet_type = "tcp"
    m.port = 443
    m.packet_size = 64
    m.max_hops = 20
    h = Hop(1)
    h.address = "1.2.3.4"
    h.samples.append(Sample(1000.0, 5.0))
    h.samples.append(Sample(1010.0, 6.0))
    m._hops = [h]

    info = m.run_summary()
    assert info["interval"] == 2.5
    assert info["timeout_ms"] == 1500
    assert info["packet_type"] == "tcp"
    assert info["port"] == 443
    assert info["packet_size"] == 64
    assert info["max_hops"] == 20
    assert info["samples"] == 2
    assert info["window_start"] == 1000.0
    assert info["window_end"] == 1010.0


def test_run_summary_empty_is_safe():
    info = Monitor().run_summary()
    assert info["samples"] == 0
    assert info["window_start"] is None
    assert info["window_end"] is None


def test_load_dict_restores_engine_config():
    m = Monitor()
    data = {
        "target_input": "8.8.8.8", "target_ip": "8.8.8.8",
        "route_len": 1, "reached_target": True, "final_hop_only": False,
        "config": {"interval": 5.0, "timeout_ms": 2000, "max_hops": 15,
                   "packet_size": 48, "send_delay_ms": 10, "packet_type": "udp", "port": 33434},
        "hops": [{"ttl": 1, "address": "1.1.1.1", "hostname": "", "last_status": 11013,
                  "samples": [[1.0, 5.0], [2.0, None]]}],
        "events": [],
    }
    m.load_dict(data)
    assert m.interval == 5.0
    assert m.timeout_ms == 2000
    assert m.packet_type == "udp"
    assert m.port == 33434
    info = m.run_summary()
    assert info["packet_type"] == "udp"
    assert info["samples"] == 2
    m.shutdown()
