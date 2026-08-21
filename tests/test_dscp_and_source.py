"""DSCP marking and source-interface binding.

Two things a network engineer needs that the tool could not do:

*Measure a path as the traffic class you care about.* Every probe went out
best-effort, so on a link with QoS you were measuring the wrong queue. If voice
is marked EF and gets a priority queue, a best-effort probe tells you about the
scavenger queue instead — the opposite of the thing you are trying to check.
IP_OPTION_INFORMATION.Tos was already in the ctypes struct and hardcoded to 0.

*Ask what a path looks like from a particular interface.* On a multi-homed box
— a building with separate BAS and corporate VLANs is the obvious case — the
route table decides which NIC a probe leaves by, and you could not override it.
IcmpSendEcho has no source parameter at all; IcmpSendEcho2Ex does.

Verified on Windows 11 before writing this:

    src=0.0.0.0        (stack picks)      -> status 0, 11 ms
    src=<this NIC>                        -> status 0, 10 ms
    src=203.0.113.7    (not on this box)  -> n=0, GetLastError 1214

That 1214 is the proof the parameter is honoured rather than ignored: a source
the machine does not hold is rejected outright instead of silently falling back
to the default route. A silent fallback would be the dangerous outcome, because
the results would look fine and be from the wrong interface.

NOT verified here, and flagged as such in the docs: that the ToS byte reaches
the wire. Every DSCP value is accepted without error, but confirming the byte
needs a packet capture and this session was not elevated.
"""
import socket
import sys

import pytest

from pingerplot import icmp, tcpudp
from pingerplot.monitor import Monitor

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32",
                                  reason="Win32 IP Helper API")


# --- the DSCP -> ToS conversion --------------------------------------------

@pytest.mark.parametrize("dscp,tos", [
    (0, 0x00),      # CS0, best effort
    (8, 0x20),      # CS1
    (26, 0x68),     # AF31, signalling
    (34, 0x88),     # AF41, video
    (46, 0xB8),     # EF, voice — the one people actually check
    (48, 0xC0),     # CS6, network control
    (63, 0xFC),     # the top of the range
])
def test_known_code_points(dscp, tos):
    """DSCP occupies the top 6 bits of the ToS byte. Getting the shift wrong
    would put a 46 meant as EF on the wire as DSCP 11."""
    assert icmp.dscp_to_tos(dscp) == tos


@pytest.mark.parametrize("bad", [-1, -100, 64, 255, 1000])
def test_out_of_range_values_are_clamped(bad):
    got = icmp.dscp_to_tos(bad)
    assert 0 <= got <= 0xFC
    assert got & 0x03 == 0, "the two ECN bits are not ours to set"


def test_the_ecn_bits_are_never_touched():
    """The low two bits of the ToS byte are ECN and belong to the stack.
    Writing them would be claiming congestion state we know nothing about."""
    for dscp in range(64):
        assert icmp.dscp_to_tos(dscp) & 0x03 == 0


def test_the_presets_are_real_code_points():
    assert icmp.DSCP_PRESETS["EF (voice)"] == 46
    assert icmp.DSCP_PRESETS["AF41 (video)"] == 34
    assert all(0 <= v <= 63 for v in icmp.DSCP_PRESETS.values())


# --- engine plumbing -------------------------------------------------------

def _started(**kw):
    m = Monitor()
    m._run = lambda gen: None
    m.start("127.0.0.1", **kw)
    m.stop()
    return m


@pytest.mark.parametrize("given,dscp,tos", [(46, 46, 0xB8), (0, 0, 0), (99, 63, 0xFC), (-3, 0, 0)])
def test_start_clamps_dscp_and_derives_the_tos_byte(given, dscp, tos):
    m = _started(dscp=given)
    try:
        assert m.dscp == dscp and m._tos == tos
    finally:
        m.shutdown()


def test_a_source_ip_pins_the_local_address():
    """_run derives _local_ip from the route by default; an explicit source
    must override it, or the setting would do nothing for TCP/UDP."""
    m = _started(source_ip="  10.1.2.3  ")
    try:
        assert m.source_ip == "10.1.2.3"
    finally:
        m.shutdown()


def test_a_blank_source_leaves_the_stack_to_choose():
    m = _started(source_ip="")
    try:
        assert m.source_ip == ""
    finally:
        m.shutdown()


def test_the_status_line_says_when_a_run_is_marked_or_pinned():
    """A DSCP-marked run must never be mistaken for a plain one after the fact
    — the numbers are only comparable against another run of the same class."""
    m = _started(dscp=46, source_ip="10.1.2.3")
    try:
        line = m._monitor_status(3)
        assert "DSCP 46" in line and "from 10.1.2.3" in line
    finally:
        m.shutdown()


def test_an_unmarked_run_says_nothing_extra():
    m = _started()
    try:
        assert "DSCP" not in m._monitor_status(3)
    finally:
        m.shutdown()


def test_the_export_header_records_them():
    """Two exports are only comparable if each says which class it measured."""
    m = _started(dscp=46, source_ip="10.1.2.3")
    try:
        info = m.run_summary()
        assert info["dscp"] == 46 and info["source_ip"] == "10.1.2.3"
    finally:
        m.shutdown()


def test_a_saved_session_round_trips_them():
    m = _started(dscp=34, source_ip="10.9.9.9")
    try:
        data = m.to_dict()
        other = Monitor()
        other.load_dict(data)
        assert other.dscp == 34 and other.source_ip == "10.9.9.9"
        other.shutdown()
    finally:
        m.shutdown()


def test_headless_config_accepts_both():
    from pingerplot import headless
    _t, kw = headless._target_options(
        {}, {"target": "8.8.8.8", "dscp": 46, "source_ip": "10.1.2.3"})
    assert kw["dscp"] == 46 and kw["source_ip"] == "10.1.2.3"


# --- the socket path -------------------------------------------------------

def test_setting_tos_on_a_socket_never_raises():
    """Windows ignores IP_TOS on an ordinary socket unless a registry value is
    cleared, and some stacks refuse it outright. Either way a probe must go out
    unmarked rather than not at all."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        tcpudp._set_tos(s, 0xB8)
        tcpudp._set_tos(s, 0)
        s.sendto(b"x", ("127.0.0.1", 9))     # still usable afterwards
    finally:
        s.close()


def test_zero_tos_skips_the_call_entirely():
    class Refuses(socket.socket):
        def setsockopt(self, *a):
            raise AssertionError("should not be called for an unmarked probe")
    s = Refuses(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        tcpudp._set_tos(s, 0)
    finally:
        s.close()


# --- against the real stack ------------------------------------------------

@WINDOWS_ONLY
def test_the_api_accepts_every_preset():
    """Not a claim that the byte reaches the wire — that needs a capture. Only
    that IcmpSendEcho does not reject the value, so a marked run is not
    silently a run of nothing."""
    for dscp in icmp.DSCP_PRESETS.values():
        r = icmp.ping("127.0.0.1", ttl=1, timeout_ms=300, tos=icmp.dscp_to_tos(dscp))
        assert r.status != icmp.IP_BAD_OPTION, f"DSCP {dscp} rejected"


@WINDOWS_ONLY
def test_a_source_the_machine_does_not_hold_is_rejected():
    """The important half: it must fail loudly rather than quietly probing from
    the wrong interface, which would produce plausible numbers about a path
    nobody asked about."""
    r = icmp.ping("127.0.0.1", timeout_ms=300, source_ip="203.0.113.7")
    assert not r.reached
    assert r.status == icmp.ERROR_INVALID_NETNAME
    assert "not on this machine" in icmp.status_text(r.status)


@WINDOWS_ONLY
def test_binding_to_loopback_reaches_loopback():
    r = icmp.ping("127.0.0.1", timeout_ms=1000, source_ip="127.0.0.1")
    assert r.reached and r.address == "127.0.0.1"
