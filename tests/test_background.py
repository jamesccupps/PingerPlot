"""Running in the background (1.4): the notification-area icon, closing to it,
alert notifications, and Start with Windows.

The App's side is tested against a fake tray, so nothing here touches the real
notification area; the real TrayIcon gets one Windows-only lifecycle test that
never shows a notification.
"""
import sys

import pytest

from pingerplot import appicon, tray, winplatform
from pingerplot.model import Event


class FakeTray:
    def __init__(self):
        self.states, self.notes = [], []

    def set_state(self, state, tooltip=None):
        self.states.append((state, tooltip))

    def notify(self, title, text, on_click=None, sound=False):
        self.notes.append((title, text, on_click))

    def stop(self):
        pass


@pytest.fixture
def bg(app, monkeypatch):
    t = FakeTray()
    app.tray = t
    return app, t


class FakeMonitor:
    """Just what the window reads."""
    packet_type, port, interval, route_len = "icmp", 443, 1.0, 3
    final_hop_only, dscp, source_ip = False, 0, ""

    def __init__(self, running=True, alerts=()):
        self.running, self.paused, self._alerts = running, False, list(alerts)
        self._events, self.reached_target = [], True

    def samples_for(self, ttl):
        return []

    def all_samples(self):
        return {}

    def active_alerts(self):
        return list(self._alerts)

    def events_after(self, seq):
        new = [e for e in self._events if e.seq > seq]
        return new, (self._events[-1].seq if self._events else seq)

    def summary(self):
        return (3, 0.0, 10.0, 1.0, True)

    def snapshot(self):
        return [], "Monitoring", "192.0.2.9", "x"

    def stop(self):
        self.running = False

    def request_stop(self):
        self.running = False

    def shutdown(self):
        self.running = False

    def start_options(self):
        return {}


def _add(app, name, mon):
    app._monitors[name] = mon
    return mon


# --- the tray icon's state -------------------------------------------------------

def test_the_icon_follows_the_targets(bg):
    app, t = bg
    app._update_tray()
    assert t.states[-1][0] == "idle"
    mon = _add(app, "192.0.2.1", FakeMonitor())
    app._update_tray()
    assert t.states[-1][0] == "normal" and "monitoring 1" in t.states[-1][1]
    mon._alerts = ["Hop 3: 40% loss"]
    app._update_tray()
    assert t.states[-1][0] == "alert" and "1 alerting" in t.states[-1][1]
    n = len(t.states)
    app._update_tray()
    assert len(t.states) == n, "an unchanged state was sent to the tray again"


# --- notifications -----------------------------------------------------------------

def test_an_alert_and_its_clear_each_notify_once(bg):
    app, t = bg
    mon = _add(app, "192.0.2.1", FakeMonitor())
    mon._events = [Event(0, 0.0, "route", "Hop 2: path changed"),
                   Event(1, 0.0, "alert", "ALERT: Hop 3 (192.0.2.1): 40% packet loss")]
    app._update_tray()
    assert len(t.notes) == 1
    title, text, on_click = t.notes[0]
    assert "192.0.2.1" in title and "alert" in title and text.startswith("Hop 3")
    app._update_tray()
    assert len(t.notes) == 1, "notified twice"
    mon._events.append(Event(2, 0.0, "clear", "Cleared: Hop 3 (192.0.2.1): 40% packet loss"))
    app._update_tray()
    assert len(t.notes) == 2 and "back to normal" in t.notes[1][0]


def test_notifications_can_be_turned_off(bg):
    app, t = bg
    app.notify_var.set(False)
    mon = _add(app, "192.0.2.1", FakeMonitor())
    mon._events = [Event(0, 0.0, "alert", "ALERT: Hop 3: loss")]
    app._update_tray()
    assert t.notes == []


def test_clicking_a_notification_opens_its_target(bg):
    app, t = bg
    mon = _add(app, "192.0.2.1", FakeMonitor())
    _add(app, "192.0.2.2", FakeMonitor())
    app._active = "192.0.2.2"
    mon._events = [Event(0, 0.0, "alert", "ALERT: Hop 3: loss")]
    app._update_tray()
    t.notes[0][2]()                  # the click, on the tray thread: it only posts
    assert app._active == "192.0.2.2"
    while not app._ui_jobs.empty():  # what _drain_ui does on Tk's thread
        app._ui_jobs.get_nowait()()
    assert app._active == "192.0.2.1"


# --- closing ---------------------------------------------------------------------------

def test_closing_hides_to_the_tray_and_says_so_once(bg, monkeypatch):
    app, t = bg
    quits = []
    monkeypatch.setattr(app, "_quit", lambda: quits.append(1))
    app._on_close()
    assert app.root.state() == "withdrawn" and quits == []
    assert len(t.notes) == 1 and "still running" in t.notes[0][0]
    app.root.deiconify()
    app._on_close()
    assert len(t.notes) == 1, "the 'still running' note is a one-time thing"


def test_closing_quits_when_that_is_what_you_chose(bg, monkeypatch):
    app, t = bg
    app.keep_tray_var.set(False)
    quits = []
    monkeypatch.setattr(app, "_quit", lambda: quits.append(1))
    app._on_close()
    assert quits == [1]


def test_without_a_tray_closing_quits(app, monkeypatch):
    quits = []
    monkeypatch.setattr(app, "_quit", lambda: quits.append(1))
    app._on_close()
    assert quits == [1]


# --- the tray menu ----------------------------------------------------------------------

def test_the_menu_only_posts_work_to_tk(bg):
    app, _t = bg
    _add(app, "192.0.2.1", FakeMonitor())
    items = {i.text: i for i in app._tray_menu() if i.text}
    assert items["Open PingerPlot"].default
    assert items["Stop all"].enabled
    items["Stop all"].action()
    assert app._monitors["192.0.2.1"].running, "the tray thread touched the app directly"
    while not app._ui_jobs.empty():
        app._ui_jobs.get_nowait()()
    assert not app._monitors["192.0.2.1"].running


# --- Start with Windows -------------------------------------------------------------------

def test_turning_on_start_with_windows_also_resumes_targets(app, monkeypatch):
    calls = []
    monkeypatch.setattr(winplatform, "set_autostart", lambda on: calls.append(on) or True)
    monkeypatch.setattr(winplatform, "autostart_enabled", lambda: bool(calls and calls[-1]))
    app.resume_var.set(False)
    app.autostart_var.set(True)
    app._toggle_autostart()
    assert calls == [True] and app.resume_var.get() is True


def test_settings_keep_the_background_choices(app, isolated_settings):
    app.keep_tray_var.set(False)
    app.notify_var.set(False)
    app._save_settings()
    saved = isolated_settings.load()
    assert saved["keep_in_tray"] is False and saved["notify"] is False


# --- the real thing (Windows) ----------------------------------------------------------------

@pytest.mark.skipif(not tray.AVAILABLE, reason="the Windows notification area")
@pytest.mark.parametrize("state", list(appicon.STATES))
def test_every_tray_icon_can_be_made(state):
    for size in (16, 32):
        h = tray.hicon_from_png(appicon.png_bytes(size, state), size)
        assert h, f"no icon for {state} at {size}px"
        tray._u32.DestroyIcon(h)


@pytest.mark.skipif(not tray.AVAILABLE, reason="the Windows notification area")
def test_the_tray_icon_starts_and_stops():
    t = tray.TrayIcon(lambda st, sz: appicon.png_bytes(sz, st), "PingerPlot test", lambda: [])
    ok = t.start()
    try:
        if ok:                       # a CI desktop may have no notification area
            t.set_state("alert", "PingerPlot test · alerting")
    finally:
        t.stop()
    assert t.hwnd is None, "the tray thread didn't wind down"
