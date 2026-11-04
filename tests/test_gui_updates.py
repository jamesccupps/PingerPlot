"""The window's side of updates (gui_updates.py): when it checks, what it
shows, what Install does, and what the app does when it's restarted by an
update. updater itself is faked here; tests/test_updater.py covers it."""
import time

import pytest

from pingerplot import gui_updates, updater


class FakeTray:
    def __init__(self):
        self.notes = []

    def set_state(self, state, tooltip=None):
        pass

    def notify(self, title, text, on_click=None, sound=False):
        self.notes.append((title, text))

    def stop(self):
        pass


def _update(kind="installed", version="9.9.9"):
    asset = None if kind == "source" else {"file": updater.INSTALLER_NAME, "size": 1, "sha256": "0" * 64}
    return updater.Update(version, "now", kind, asset, "here")


def _drain(app, want=1, timeout=5.0):
    """Run what worker threads posted for Tk's thread, as _drain_ui does."""
    end, ran = time.monotonic() + timeout, 0
    while ran < want and time.monotonic() < end:
        while not app._ui_jobs.empty():
            app._ui_jobs.get_nowait()()
            ran += 1
        time.sleep(0.01)
    return ran


def _texts(widget):
    out = []
    for w in widget.winfo_children():
        try:
            out.append(str(w.cget("text")))
        except Exception:
            pass
        out += _texts(w)
    return out


@pytest.fixture
def found(app, monkeypatch):
    """The app after a check that found 9.9.9."""
    monkeypatch.setattr(updater, "check", lambda: _update())
    app.updates.check()
    assert _drain(app) == 1
    return app


def test_the_choice_is_saved_and_restored(app, isolated_settings):
    app.updates.auto_var.set(False)
    app._save_settings()
    assert isolated_settings.load()["check_updates"] is False
    app.updates.auto_var.set(True)
    app.updates.load({"check_updates": False})
    assert app.updates.auto_var.get() is False
    app.updates.load({"check_updates": "yes"})       # hand-edited: ignored
    assert app.updates.auto_var.get() is False


def test_a_source_run_never_checks_by_itself(app):
    app.updates.start()
    assert app.updates._next is None


def test_one_automatic_check_is_pending_however_often_the_switch_flips(app, monkeypatch):
    monkeypatch.setattr(updater, "install_kind", lambda: "installed")
    app.updates.start()
    for on in (False, True, False, True, True):
        app.updates.auto_var.set(on)
        app.updates._auto_changed()
    pending = [a for a in app.root.tk.call("after", "info")]
    assert app.updates._next in pending
    assert len(pending) == 1
    app.updates.auto_var.set(False)
    app.updates._auto_changed()
    assert app.updates._next is None and not app.root.tk.call("after", "info")


def test_a_check_that_finds_a_version(found):
    u = found.updates
    assert u.available.version == "9.9.9" and not u.busy
    assert u.status == "Version 9.9.9 is available."
    shown = _texts(u._bar_holder)
    assert "PingerPlot 9.9.9 is available." in shown and "Install and restart" in shown


def test_the_tray_offers_the_install(found):
    from pingerplot import tray
    labels = [i.text for i in found.updates.tray_items(tray.MenuItem)]
    assert "Install version 9.9.9" in labels
    found.updates.busy = True
    assert found.updates.tray_items(tray.MenuItem) == []


def test_a_hidden_window_gets_a_notification(app, monkeypatch):
    app.tray = FakeTray()
    monkeypatch.setattr(updater, "check", lambda: _update())
    app.updates.check()
    _drain(app)
    assert app.tray.notes == [("PingerPlot 9.9.9 is available",
                               "Click to open PingerPlot and install it.")]
    app.updates.check()                               # the same version again: once is enough
    _drain(app)
    assert len(app.tray.notes) == 1


def test_up_to_date(app, monkeypatch):
    monkeypatch.setattr(updater, "check", lambda: None)
    app.updates.check()
    _drain(app)
    assert app.updates.status.startswith("You're up to date")
    assert app.updates.available is None and _texts(app.updates._bar_holder) == []


def test_a_failed_check_says_so_and_nothing_more(app, monkeypatch):
    def boom():
        raise OSError("no route to host")
    monkeypatch.setattr(updater, "check", boom)
    app.updates.check()
    _drain(app)
    assert app.updates.status == "Couldn't check: no route to host"
    assert not app.updates.busy


def test_a_source_copy_is_told_to_git_pull(app, monkeypatch):
    monkeypatch.setattr(updater, "check", lambda: _update("source"))
    app.updates.check()
    _drain(app)
    shown = _texts(app.updates._bar_holder)
    assert "PingerPlot 9.9.9 is out: update with git pull." in shown
    assert "Install and restart" not in shown


def test_dismissing_the_bar(found):
    found.updates.dismiss()
    assert _texts(found.updates._bar_holder) == []


def test_the_settings_section_follows_along(found):
    found._show_page("settings")
    u = found.updates
    assert u._status_lbl.cget("text") == "Version 9.9.9 is available."
    assert u._install_btn.winfo_manager() == "pack"
    assert str(u._install_btn.cget("text")) == "Install 9.9.9 and restart"
    assert gui_updates.KINDS["source"] in _texts(u._row)


def test_install_downloads_applies_and_quits(found, monkeypatch, tmp_path):
    calls, quit_ = [], []
    monkeypatch.setattr(updater, "download", lambda u, progress=None: (progress(1, 1), tmp_path)[1])
    monkeypatch.setattr(updater, "apply", lambda u, path: calls.append((u.version, path)))
    monkeypatch.setattr(found, "_quit", lambda: quit_.append(True))
    found.updates.install()
    end = time.monotonic() + 5
    while not quit_ and time.monotonic() < end:
        _drain(found, want=1, timeout=0.2)
    assert calls == [("9.9.9", tmp_path)] and quit_ == [True]


def test_a_failed_install_reports_and_lets_you_retry(found, monkeypatch):
    shown = []
    monkeypatch.setattr(gui_updates.messagebox, "showerror", lambda *a, **k: shown.append(a))

    def bad(u, progress=None):
        raise updater.UpdateError("PingerPlot-Setup.exe doesn't match latest.json")
    monkeypatch.setattr(updater, "download", bad)
    found.updates.install()
    end = time.monotonic() + 5
    while not shown and time.monotonic() < end:
        _drain(found, want=1, timeout=0.2)
    assert shown and "doesn't match" in shown[0][1]
    assert not found.updates.busy
    assert found.updates.status.startswith("The update didn't install")


def test_after_an_update_the_targets_come_back_even_if_resume_is_off(app, monkeypatch):
    started = []
    monkeypatch.setattr(app, "_start", lambda name, opts=None: started.append(name))
    app._settings["targets"] = ["dns.example.net"]
    app.resume_var.set(False)
    app._restore_targets()
    assert started == []
    app._restore_targets(force=True)
    assert started == ["dns.example.net"]
