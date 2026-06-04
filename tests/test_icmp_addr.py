"""Address round-trip tests for the ICMP helpers.

These exercise the dotted-quad <-> IPAddr conversions, which are pure and
platform-independent (the ctypes/Win32 layer is only touched by ping())."""
import pytest

from pingerplot import icmp


@pytest.mark.parametrize("ip", ["8.8.8.8", "192.168.1.1", "203.0.113.11", "255.255.255.255", "0.0.0.0", "127.0.0.1"])
def test_ip_uint_roundtrip(ip):
    n = icmp._ip_to_uint32(ip)
    assert 0 <= n <= 0xFFFFFFFF
    assert icmp._uint32_to_ip(n) == ip


def test_known_encoding():
    # 8.8.8.8 has identical octets, so byte order is unambiguous.
    assert icmp._ip_to_uint32("8.8.8.8") == 0x08080808
    # First octet lands in the low byte (network order stored little-endian).
    assert icmp._ip_to_uint32("1.0.0.0") == 0x00000001
    assert icmp._ip_to_uint32("0.0.0.2") == 0x02000000


def test_status_text_known_and_unknown():
    assert icmp.status_text(icmp.IP_SUCCESS) == "ok"
    assert icmp.status_text(icmp.IP_TTL_EXPIRED_TRANSIT) == "ttl expired (hop)"
    assert "99999" in icmp.status_text(99999)


def test_pingresult_responded_flag():
    ok = icmp.PingResult(icmp.IP_SUCCESS, 12.0, "8.8.8.8", True)
    hop = icmp.PingResult(icmp.IP_TTL_EXPIRED_TRANSIT, 5.0, "10.0.0.1", False)
    timeout = icmp.PingResult(icmp.IP_REQ_TIMED_OUT, None, None, False)
    assert ok.responded and ok.reached
    assert hop.responded and not hop.reached
    assert not timeout.responded
