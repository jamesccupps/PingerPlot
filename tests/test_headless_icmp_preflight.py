"""Headless must say when the ICMP backend cannot probe.

gui.App.__init__ checks icmp.is_available() and shows the actionable
unavailable_reason() -- on Linux that is a one-line sysctl. The headless
runner, which is the mode most likely to land on a Linux box in the first
place, did not check at all.

Without the check every probe raises PermissionError inside icmp.ping, gets
swallowed by _gather_range, and is recorded as a timeout. The operator gets a
clean-looking report claiming the destination never answered (Sent: 0 -- the
engine knows nothing was ever sent) and exit status 1, pointing them at the
network instead of at a sysctl.
"""
import io

from pingerplot import headless
from pingerplot.headless import Target
from pingerplot.monitor import Monitor


def _target(name="8.8.8.8", packet_type="icmp"):
    m = Monitor()
    m.packet_type = packet_type
    return Target(name, m, {})


def _unavailable(monkeypatch, reason="ping_group_range is closed"):
    monkeypatch.setattr(headless.icmp, "is_available", lambda: False)
    monkeypatch.setattr(headless.icmp, "unavailable_reason", lambda: reason)


def test_an_unusable_backend_is_announced(monkeypatch):
    _unavailable(monkeypatch)
    buf = io.StringIO()
    assert headless._warn_if_icmp_unusable([_target()], out=buf) is True
    text = buf.getvalue()
    assert "ping_group_range is closed" in text
    assert "8.8.8.8" in text, "should name the targets that will report nothing"


def test_a_usable_backend_says_nothing(monkeypatch):
    monkeypatch.setattr(headless.icmp, "is_available", lambda: True)
    buf = io.StringIO()
    assert headless._warn_if_icmp_unusable([_target()], out=buf) is False
    assert buf.getvalue() == ""


def test_tcp_and_udp_configs_are_not_warned(monkeypatch):
    """Those modes do not touch the backend; warning about it would be noise."""
    _unavailable(monkeypatch)
    buf = io.StringIO()
    targets = [_target("a", "tcp"), _target("b", "udp")]
    assert headless._warn_if_icmp_unusable(targets, out=buf) is False
    assert buf.getvalue() == ""


def test_a_mixed_config_warns_only_about_the_icmp_targets(monkeypatch):
    _unavailable(monkeypatch)
    buf = io.StringIO()
    targets = [_target("tcp-one", "tcp"), _target("icmp-one", "icmp")]
    assert headless._warn_if_icmp_unusable(targets, out=buf) is True
    text = buf.getvalue()
    assert "icmp-one" in text and "tcp-one" not in text


def test_main_runs_the_preflight_and_it_reaches_stderr(monkeypatch, capsys, tmp_path):
    """End to end: the wiring, not just the helper."""
    _unavailable(monkeypatch, "no ICMP for you")
    cfg = tmp_path / "monitor.json"
    cfg.write_text('{"targets": [{"target": "127.0.0.1", "final_hop_only": true,'
                   ' "interval": 0.2, "timeout_ms": 200}]}', encoding="utf-8")
    headless.main([str(cfg), "--report", "1"])
    assert "no ICMP for you" in capsys.readouterr().err
