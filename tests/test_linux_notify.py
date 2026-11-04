"""Alerts on Linux: a notify-send notification where there's no tray icon,
and Tk's bell where Windows' engine would beep (winsound)."""
import pytest

from pingerplot import winplatform, xdg
from pingerplot.model import Event
from test_background import FakeMonitor, _add


class FakeNotifier:
    can_click = False

    def __init__(self):
        self.notes = []

    def notify(self, title, text, on_click=None, sound=False):
        self.notes.append((title, text))


@pytest.fixture
def linux_app(app, monkeypatch):
    """The window as it is on Linux: no tray, notify-send for notifications."""
    n = FakeNotifier()
    app.tray = None
    app._desktop_notifier = n
    monkeypatch.setattr(winplatform, "IS_WINDOWS", False)
    rings = []
    monkeypatch.setattr(app.root, "bell", lambda *a, **k: rings.append(1))
    return app, n, rings


def _alerting(app, sound=True):
    mon = _add(app, "192.0.2.1", FakeMonitor())
    mon.alert_sound = sound
    mon._events = [Event(0, 0.0, "alert", "ALERT: Hop 3 (192.0.2.1): 40% packet loss")]
    return mon


def test_an_alert_notifies_without_a_tray(linux_app):
    app, n, rings = linux_app
    mon = _alerting(app)
    app._update_tray()
    assert n.notes == [("192.0.2.1: alert", "Hop 3 (192.0.2.1): 40% packet loss")]
    mon._events.append(Event(1, 0.0, "clear", "Cleared: Hop 3 (192.0.2.1): 40% packet loss"))
    app._update_tray()
    assert n.notes[-1][0] == "192.0.2.1: back to normal"


def test_an_alert_rings_the_bell_once_when_sound_is_on(linux_app):
    app, _n, rings = linux_app
    _alerting(app)
    app._update_tray()
    app._update_tray()
    assert rings == [1]


def test_no_bell_when_the_target_has_sound_off(linux_app):
    app, _n, rings = linux_app
    _alerting(app, sound=False)
    app._update_tray()
    assert rings == []


def test_turning_notifications_on_later_doesnt_replay_old_alerts(linux_app):
    app, n, _rings = linux_app
    app.notify_var.set(False)
    mon = _alerting(app)
    app._update_tray()
    assert n.notes == []
    app.notify_var.set(True)
    app._update_tray()
    assert n.notes == [], "an alert from while they were off came back"
    mon._events.append(Event(1, 0.0, "clear", "Cleared: Hop 3"))
    app._update_tray()
    assert len(n.notes) == 1


def test_notify_send_command_line(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(xdg.subprocess, "Popen", lambda args, **kw: ran.append((args, kw)))
    xdg.Notifier("/usr/bin/notify-send", tmp_path / "icon.png").notify("t: alert", "-40% loss")
    (args, kw), = ran
    assert args == ["/usr/bin/notify-send", "--app-name=PingerPlot", f"--icon={tmp_path / 'icon.png'}",
                    "--", "t: alert", "-40% loss"]
    assert kw["start_new_session"] and kw["stdout"] is xdg.subprocess.DEVNULL


def test_a_failing_notify_send_is_only_logged(monkeypatch):
    def boom(*a, **k):
        raise OSError("gone")
    monkeypatch.setattr(xdg.subprocess, "Popen", boom)
    xdg.Notifier("/usr/bin/notify-send").notify("t", "x")


def test_notifier_needs_linux_and_notify_send(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(xdg, "IS_LINUX", False)
    assert xdg.notifier() is None
    monkeypatch.setattr(xdg, "IS_LINUX", True)
    monkeypatch.setattr(xdg.shutil, "which", lambda name: None)
    assert xdg.notifier() is None
    monkeypatch.setattr(xdg.shutil, "which", lambda name: "/usr/bin/" + name)
    n = xdg.notifier()
    assert n.exe == "/usr/bin/notify-send" and n.icon.is_file()
    assert n.icon.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
