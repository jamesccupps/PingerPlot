"""Restart as administrator, from inside the app, and the two halves of review
M3 it depends on (an elevated copy's lock and window must still be found by
an ordinary one). The Windows calls are faked: nothing is elevated here."""
import ctypes
import sys
import types
from pathlib import Path

import pytest

from pingerplot import monitor as monitor_mod, winplatform
from pingerplot.monitor import Monitor

WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows APIs")


def _fn(result, calls=None):
    """A stand-in for a ctypes function: callable, with argtypes/restype."""
    def f(*args):
        if calls is not None:
            calls.append(args)
        return result() if callable(result) else result
    return f


# --- relaunching ----------------------------------------------------------------

@WINDOWS
@pytest.mark.parametrize("rc, says", [(42, None), (5, "answered No"), (2, "error 2")])
def test_relaunch_asks_windows_to_run_this_copy_elevated(monkeypatch, rc, says):
    calls = []
    monkeypatch.setattr(ctypes.windll, "shell32", types.SimpleNamespace(ShellExecuteW=_fn(rc, calls)))
    exe = Path(r"C:\Users\someone\AppData\Local\Programs\PingerPlot\PingerPlot.exe")
    monkeypatch.setattr(winplatform, "_frozen_exe", lambda: exe)
    why = winplatform.relaunch_elevated()
    (args,) = calls
    assert args[1] == "runas" and args[2] == str(exe) and args[3] == winplatform.RESTARTED
    assert args[4] == str(exe.parent)
    if says is None:
        assert why is None
    else:
        assert says in why


def test_can_elevate_only_a_built_unelevated_windows_copy(monkeypatch):
    monkeypatch.setattr(winplatform, "IS_WINDOWS", True)
    monkeypatch.setattr(winplatform, "is_elevated", lambda: False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert winplatform.can_elevate()
    monkeypatch.setattr(winplatform, "is_elevated", lambda: True)
    assert not winplatform.can_elevate()
    monkeypatch.setattr(winplatform, "is_elevated", lambda: False)
    monkeypatch.delattr(sys, "frozen")
    assert not winplatform.can_elevate(), "from source, launch.ps1 -Elevated does it"


# --- the lock and the window (review M3) ------------------------------------------

@WINDOWS
def test_access_denied_on_the_lock_means_a_copy_is_running(monkeypatch):
    """An elevated copy's mutex can't be opened by an ordinary process."""
    fake = types.SimpleNamespace(CreateMutexW=_fn(None), GetLastError=_fn(5), CloseHandle=_fn(1))
    monkeypatch.setattr(ctypes.windll, "kernel32", fake)
    assert winplatform.SingleInstance().already_running


def test_a_restarted_copy_waits_for_the_old_one_to_let_go(monkeypatch):
    states = iter([True, True, False])
    released = []

    class Fake:
        def __init__(self):
            self.already_running = next(states)

        def release(self):
            released.append(self)
    monkeypatch.setattr(winplatform, "SingleInstance", Fake)
    inst = winplatform.wait_for_instance(timeout=5, poll=0)
    assert not inst.already_running and len(released) == 2, "the handle is let go between tries"


def test_the_wait_gives_up_after_its_timeout(monkeypatch):
    class Busy:
        already_running = True

        def release(self):
            pass
    monkeypatch.setattr(winplatform, "SingleInstance", Busy)
    assert winplatform.wait_for_instance(timeout=0, poll=0).already_running


# --- in the window ------------------------------------------------------------------

def test_a_restart_that_starts_quits_this_copy(app, monkeypatch):
    quit_called = []
    monkeypatch.setattr(winplatform, "relaunch_elevated", lambda: None)
    monkeypatch.setattr(app, "_quit", lambda: quit_called.append(True))
    app._restart_elevated()
    assert quit_called


def test_a_restart_that_doesnt_says_why_and_keeps_running(app, monkeypatch):
    from pingerplot import gui
    shown, quit_called = [], []
    monkeypatch.setattr(winplatform, "relaunch_elevated",
                        lambda: "Windows didn't start it as administrator: the prompt was answered No.")
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda *a, **k: shown.append(a[1]))
    monkeypatch.setattr(app, "_quit", lambda: quit_called.append(True))
    app._restart_elevated()
    assert shown and "answered No" in shown[0] and not quit_called


def test_the_button_shows_for_a_target_that_needs_it(app, monkeypatch):
    monkeypatch.setattr(winplatform, "can_elevate", lambda: True)
    app._empty.needs_admin = True
    app._update_elevate_button()
    assert app._elevate_shown
    app._empty.needs_admin = False
    app._update_elevate_button()
    assert not app._elevate_shown


def test_settings_and_tray_offer_it(app, monkeypatch):
    monkeypatch.setattr(winplatform, "can_elevate", lambda: True)
    monkeypatch.setattr(winplatform, "IS_WINDOWS", True)
    monkeypatch.setattr(winplatform, "autostart_enabled", lambda: False)
    labels = [getattr(i, "text", None) or getattr(i, "label", None) for i in app._tray_menu()]
    assert "Restart as administrator" in labels
    app._show_page("settings")

    def texts(w):
        out = []
        try:
            out.append(str(w.cget("text")))
        except Exception:
            pass
        for c in w.winfo_children():
            out.extend(texts(c))
        return out
    assert "Run as administrator" in texts(app.settings_page)


# --- the engine ------------------------------------------------------------------------

@WINDOWS
def test_the_engine_flags_a_target_that_needs_administrator(monkeypatch):
    monkeypatch.setattr(monitor_mod.socket, "gethostbyname", lambda _n: "203.0.113.9")
    monkeypatch.setattr(monitor_mod.tcpudp, "capture_supported", lambda _ip: False)
    m = Monitor()
    m.start("svc.example.com", packet_type="tcp", port=443)
    import time
    deadline = time.monotonic() + 5
    while m.running and time.monotonic() < deadline:
        time.sleep(0.02)
    m.shutdown()
    assert m.needs_admin and "needs Administrator" in m.status
