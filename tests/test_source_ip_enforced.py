"""A source IP the machine does not hold must be refused in every mode.

The Engine dialog promises an address the machine does not hold is "rejected
outright rather than falling back". ICMP keeps that promise (IcmpSendEcho2Ex
fails with ERROR_INVALID_NETNAME, the POSIX backend's bind does the same), but:

* TCP probes did ``try: bind(...) except OSError: pass`` and then connected via
  the default route. Verified on Windows: a TCP probe "from" 192.0.2.77
  reported the destination as reached.
* UDP traceroute never bound its probe sockets at all.
* Elevated on Windows, the capture socket's bind fails first and the user was
  told TCP/UDP "needs Administrator" -- wrong, and they are already admin.
* In ICMP mode every probe fails with 1214, so the trace finds nothing and the
  status says the "target may block ICMP or be down" -- also wrong.

So: one up-front check with an accurate message in every mode, and strict
binding in the TCP/UDP probes for an address that disappears mid-run.
"""
import socket

import pytest

from pingerplot import icmp, tcpudp
from pingerplot.monitor import Monitor
from mock_router import DEST, LoopbackService, MockCapture

NOT_HELD = "192.0.2.77"   # TEST-NET-1: never assigned to a local interface


def _wait(pred, timeout=6.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_source_problem_names_an_address_this_machine_does_not_hold():
    assert tcpudp.source_problem("127.0.0.1") is None
    assert tcpudp.source_problem(NOT_HELD)
    assert tcpudp.source_problem("not-an-address")


def test_a_strict_tcp_probe_from_a_foreign_source_does_not_fall_back():
    svc = LoopbackService()
    try:
        r = tcpudp.probe("127.0.0.1", 255, 3000, "tcp", svc.port, NOT_HELD,
                         strict_source=True)
        assert r.status == icmp.ERROR_INVALID_NETNAME and not r.reached
        # Control: the auto-detected source still falls back as before.
        r = tcpudp.probe("127.0.0.1", 255, 3000, "tcp", svc.port, "127.0.0.1")
        assert r.reached
    finally:
        svc.close()


def test_udp_round_binds_its_probe_sockets(monkeypatch):
    cap = MockCapture()
    try:
        cap.install(monkeypatch, tcpudp)
        bound = []
        real_bind = socket.socket.bind

        class Spy(socket.socket):
            def bind(self, addr):
                bound.append(addr)
                return real_bind(self, addr)

        monkeypatch.setattr(tcpudp.socket, "socket", Spy)
        tcpudp.probe_path(DEST, [1, 2], timeout_ms=200, mode="udp", port=33434,
                          local_ip="127.0.0.1")
        assert [a for a in bound if a[0] == "127.0.0.1"], "UDP probes were never bound"
    finally:
        cap.close()


def test_a_strict_udp_round_reports_an_unheld_source_per_hop(monkeypatch):
    cap = MockCapture()
    try:
        cap.install(monkeypatch, tcpudp)
        res = tcpudp.probe_path(DEST, [1, 2], timeout_ms=200, mode="udp", port=33434,
                                local_ip=NOT_HELD, strict_source=True)
        assert {r.status for r in res.values()} == {icmp.ERROR_INVALID_NETNAME}
    finally:
        cap.close()


@pytest.mark.parametrize("mode", ["icmp", "tcp", "udp"])
def test_monitor_refuses_an_unheld_source_up_front(mode):
    m = Monitor()
    m.start("127.0.0.1", interval=0.3, timeout_ms=400, packet_type=mode,
            final_hop_only=(mode != "udp"), source_ip=NOT_HELD)
    try:
        assert _wait(lambda: not m.running), m.status
        assert NOT_HELD in m.status and "not an address on this machine" in m.status, m.status
        assert "Administrator" not in m.status and "block ICMP" not in m.status
    finally:
        m.shutdown()
