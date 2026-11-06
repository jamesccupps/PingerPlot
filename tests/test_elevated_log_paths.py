"""Elevated (administrator, or root), the window takes no log path from
settings.json: that file is writable by anything running as you unelevated,
and a path in it made the elevated app create, append to and rename a file
wherever that program chose (the 2026-11-05 review, M1)."""
import pytest


def _build(tk_root, isolated_settings, monkeypatch, elevated):
    from pingerplot import gui
    isolated_settings.save({
        "resume_on_launch": True, "log_path": r"C:\Windows\System32\evil_x.csv",
        "targets": ["192.0.2.1"],
        "target_options": {"192.0.2.1": {"interval": 2.0, "log_path": r"C:\Windows\x_192.0.2.1.csv"}}})
    starts = []
    monkeypatch.setattr(gui.winplatform, "is_elevated", lambda: elevated)
    monkeypatch.setattr(gui.App, "_start", lambda self, name=None, options=None: starts.append(options))
    monkeypatch.setattr(gui.icmp, "is_available", lambda: True)
    app = gui.App(tk_root)
    for job in tk_root.tk.call("after", "info"):
        tk_root.after_cancel(job)
    app.geo.stop()
    return app, starts


def test_elevated_ignores_saved_log_paths(tk_root, isolated_settings, monkeypatch):
    app, starts = _build(tk_root, isolated_settings, monkeypatch, elevated=True)
    assert app.defaults["log_path"].get() == ""
    assert starts == [{"interval": 2.0}]


def test_unelevated_keeps_them(tk_root, isolated_settings, monkeypatch):
    app, starts = _build(tk_root, isolated_settings, monkeypatch, elevated=False)
    assert app.defaults["log_path"].get().endswith("evil_x.csv")
    assert starts[0]["log_path"].endswith("x_192.0.2.1.csv")


def test_is_elevated_answers(monkeypatch):
    from pingerplot import winplatform
    assert winplatform.is_elevated() in (True, False)
