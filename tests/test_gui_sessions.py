"""Loading a session must not turn it into a target that gets resumed.

_load_session registers the loaded data in the same dict as live monitors --
reasonable, because that is what lets the detail tabs render it -- but under
the key "<target_input> (loaded)". _save_settings then persisted
list(self._monitors.keys()) wholesale, and _restore_targets called _start() on
every saved name at the next launch, including that pseudo-target.

The result: after loading any session once, every subsequent launch carried a
permanently-dead row in the Targets grid reading

    Cannot resolve 'example.net (loaded)': ...

and it was self-perpetuating, because each save rewrote it. Cosmetic, but it
sits in the first thing the user sees and it never goes away on its own.

These are the first tests to drive the real gui.App. See tests/gui_harness.py.
"""
from pathlib import Path

import pytest

from gui_harness import write_session


def test_a_loaded_session_is_registered_but_not_a_target(app, tmp_path, load_session):
    load_session(write_session(tmp_path / "s.json"))
    assert "example.net (loaded)" in app._monitors, "should still be viewable"
    assert app._loaded == {"example.net (loaded)"}


def test_a_loaded_session_is_not_saved_as_a_target(app, tmp_path, isolated_settings, load_session):
    load_session(write_session(tmp_path / "s.json"))
    app._save_settings()
    assert isolated_settings.load().get("targets") == []


def test_a_live_target_is_still_saved(app, isolated_settings):
    """The other half: the filter must not eat real targets."""
    app.target_var.set("127.0.0.1")
    app._start()
    try:
        app._save_settings()
        assert isolated_settings.load().get("targets") == ["127.0.0.1"]
    finally:
        for name in list(app._monitors):
            app._monitors[name].shutdown()


def test_a_mixed_state_saves_only_the_live_one(app, tmp_path, isolated_settings, load_session):
    app.target_var.set("127.0.0.1")
    app._start()
    try:
        load_session(write_session(tmp_path / "s.json"))
        app._save_settings()
        saved = isolated_settings.load().get("targets")
        assert saved == ["127.0.0.1"]
        assert len(app._monitors) == 2, "the loaded session is still open in the app"
    finally:
        for name in list(app._monitors):
            app._monitors[name].shutdown()


def test_removing_a_loaded_session_forgets_it(app, tmp_path, load_session):
    """_loaded must not leak: the same name could later be a real target."""
    name = "example.net (loaded)"
    load_session(write_session(tmp_path / "s.json"))
    app._activate(name)
    app._remove_named(name)
    assert name not in app._monitors
    assert name not in app._loaded


def test_reloading_the_same_session_does_not_double_register(app, tmp_path, load_session):
    path = write_session(tmp_path / "s.json")
    load_session(path)
    load_session(path)
    assert len(app._monitors) == 1
    assert len(app._loaded) == 1


def test_the_dead_row_this_prevents(app, tmp_path, isolated_settings, load_session):
    """The actual user-visible symptom, end to end: save after loading, then
    restore, and assert nothing tried to resolve the pseudo-target."""
    load_session(write_session(tmp_path / "s.json"))
    app._save_settings()

    started = []
    app._start = lambda: started.append(app.target_var.get())
    app._settings = isolated_settings.load()
    app.resume_var.set(True)
    app._restore_targets()
    assert started == [], f"tried to resume {started}"
