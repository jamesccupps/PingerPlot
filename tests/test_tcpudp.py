"""Tests for the TCP/UDP ICMP-correlation parser. Builds synthetic ICMP error
packets, so no raw sockets or admin are needed."""
import socket

from pingerplot import tcpudp

TARGET = "203.0.113.9"   # the "our probe's destination" the error must echo


def _make_icmp(itype, orig_proto, o_src, o_dst, o_dst_ip=TARGET):
    """A raw IPv4 datagram as it arrives on the capture socket: outer IP header,
    ICMP header, then the echoed original IP (with its destination IP at [16:20])
    and transport ports."""
    outer = bytearray(20)
    outer[0] = 0x45
    outer[9] = 1                                  # outer IP protocol = ICMP
    icmp = bytes([itype, 0]) + b"\x00\x00" + b"\x00\x00\x00\x00"  # 8 bytes
    orig = bytearray(20)
    orig[0] = 0x45
    orig[9] = orig_proto                          # original IP protocol
    orig[16:20] = socket.inet_aton(o_dst_ip)      # original destination IP
    trans = o_src.to_bytes(2, "big") + o_dst.to_bytes(2, "big") + b"\x00" * 4
    return bytes(outer) + icmp + bytes(orig) + trans


def test_tcp_ttl_exceeded_matches():
    pkt = _make_icmp(11, 6, 50000, 443)
    assert tcpudp._match(pkt, 50000, 443, TARGET, "tcp") == "ttl"


def test_tcp_dest_unreachable_matches():
    pkt = _make_icmp(3, 6, 50000, 443)
    assert tcpudp._match(pkt, 50000, 443, TARGET, "tcp") == "dest"


def test_udp_ttl_exceeded_matches():
    pkt = _make_icmp(11, 17, 55555, 33434)
    assert tcpudp._match(pkt, 55555, 33434, TARGET, "udp") == "ttl"


def test_wrong_source_port_is_ignored():
    pkt = _make_icmp(11, 6, 50000, 443)
    assert tcpudp._match(pkt, 49999, 443, TARGET, "tcp") is None


def test_wrong_protocol_is_ignored():
    pkt = _make_icmp(11, 17, 50000, 443)  # UDP packet
    assert tcpudp._match(pkt, 50000, 443, TARGET, "tcp") is None  # but we asked for TCP


def test_different_target_is_rejected():
    # Same ports, but the error echoes a DIFFERENT destination — another
    # monitor's probe, not ours. Must be ignored (no cross-talk).
    pkt = _make_icmp(11, 6, 50000, 443, o_dst_ip="198.51.100.7")
    assert tcpudp._match(pkt, 50000, 443, TARGET, "tcp") is None
    assert tcpudp._match(pkt, 50000, 443, "198.51.100.7", "tcp") == "ttl"


def test_non_error_icmp_is_ignored():
    pkt = _make_icmp(0, 6, 50000, 443)  # echo reply, not an error
    assert tcpudp._match(pkt, 50000, 443, TARGET, "tcp") is None


def test_truncated_packet_is_ignored():
    assert tcpudp._match(b"\x45\x00\x00", 1, 2, TARGET, "tcp") is None


def test_parse_returns_kind_proto_ports_and_dest_ip():
    assert tcpudp._parse(_make_icmp(11, 6, 50000, 443)) == ("ttl", 6, 50000, 443, TARGET)
    assert tcpudp._parse(_make_icmp(3, 17, 12345, 33437, "198.51.100.7")) == \
        ("dest", 17, 12345, 33437, "198.51.100.7")


def test_parse_ignores_non_error_and_truncated():
    assert tcpudp._parse(_make_icmp(0, 6, 1, 2)) is None      # echo reply, not an error
    assert tcpudp._parse(b"\x45\x00\x00\x00") is None         # too short


def test_helpers_return_sane_types():
    assert isinstance(tcpudp.is_admin(), bool)
    ip = tcpudp.local_ip_for("8.8.8.8")
    assert isinstance(ip, str) and ip.count(".") == 3
    assert tcpudp.DEFAULT_PORTS["tcp"] == 443


def test_tcp_reach_always_returns_a_definite_result():
    import errno
    # clean errors (connected / refused = port closed) -> the host answered
    for e in (0, errno.ECONNREFUSED, 10061):
        r = tcpudp._tcp_reach(e, 5.0, TARGET)
        assert r.reached and r.status == tcpudp.IP_SUCCESS
    # anything else -> a definite timeout result, never a silently dropped ttl
    for e in (10065, 10051, 10060):   # host unreach, net unreach, timed out
        r = tcpudp._tcp_reach(e, 5.0, TARGET)
        assert (not r.reached) and r.status == tcpudp.IP_REQ_TIMED_OUT
