"""Changes are saved a second after they're made, not only on hide or quit:
a Windows Update restart or a crash used to lose every target added or
edited with the window open."""
from test_background import FakeMonitor, _add


def _pending(app):
    return [a for a in app.root.tk.call("after", "info")]


def test_a_setting_change_is_saved_soon(app, isolated_settings):
    app.theme_var.set("light")
    assert app._save_job in _pending(app)
    app._save_now()
    assert isolated_settings.load()["theme"] == "light"


def test_many_changes_make_one_save(app):
    for key, value in (("interval", "1"), ("interval", "1.5"), ("timeout_ms", "900")):
        app.defaults[key].set(value)
    app.notify_var.set(False)
    assert len(_pending(app)) == 1


def test_removing_a_target_is_saved_soon(app, isolated_settings):
    _add(app, "192.0.2.1", FakeMonitor())
    _add(app, "192.0.2.2", FakeMonitor())
    app._save_now()
    assert isolated_settings.load()["targets"] == ["192.0.2.1", "192.0.2.2"]
    app._remove_named("192.0.2.1")
    assert app._save_job in _pending(app)
    app._save_now()
    assert isolated_settings.load()["targets"] == ["192.0.2.2"]


def test_adding_a_target_schedules_a_save(app, monkeypatch):
    monkeypatch.setattr(app, "_default_options", lambda: None)     # stops before probing
    app.target_var.set("192.0.2.9")
    app._start()
    assert app._save_job in _pending(app)


def test_windows_ending_the_session_saves(app):
    assert app.root.protocol("WM_SAVE_YOURSELF"), "no handler for the end of the session"


def test_quitting_cancels_the_pending_save(app, monkeypatch):
    app.theme_var.set("light")
    job = app._save_job
    saved = []
    monkeypatch.setattr(app, "_save_settings", lambda: saved.append(1))
    monkeypatch.setattr(app.root, "destroy", lambda: None)
    app._quit()
    assert saved == [1] and job not in _pending(app)
