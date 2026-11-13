"""The Recording section of the Settings page: shown, saved, and applied to
running targets without a restart."""
import json
import time

from pingerplot import gui_recording, history, monitor as monitor_mod
from pingerplot.history import Reader
from pingerplot.monitor import Monitor


def _texts(widget):
    out = []
    try:
        out.append(str(widget.cget("text")))
    except Exception:
        pass
    for child in widget.winfo_children():
        out.extend(_texts(child))
    return out


def _settings_file(tmp_path):
    for p in tmp_path.rglob("settings.json"):
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def test_recording_is_on_out_of_the_box(app):
    assert app.recorder is not None
    assert app.recording.options() == history.DEFAULTS


def test_the_settings_page_has_a_recording_section(app):
    app._show_page("settings")
    texts = _texts(app.settings_page)
    for wanted in ("Recording", "Record history", "Keep every probe for (days)",
                   "Size limit (MB)", "What to record", "Open folder", "Clear history…"):
        assert wanted in texts, wanted
    # The advanced switches are there but folded away until asked for.
    assert "Every probe" in texts


def test_the_options_are_saved(app, tmp_path):
    app.recording["probe_days"].set("30")
    app.recording["probes"].set(False)
    app._save_settings()
    saved = _settings_file(tmp_path)["recording"]
    assert saved["probe_days"] == 30 and saved["probes"] is False


def test_nonsense_in_a_number_box_falls_back_to_the_default(app):
    app.recording["max_mb"].set("lots")
    app.recording["event_days"].set("0")
    opts = app.recording.options()
    assert opts["max_mb"] == history.DEFAULTS["max_mb"]
    assert opts["event_days"] == 1


def test_turning_recording_off_and_on_applies_to_a_running_target(app, monkeypatch):
    monkeypatch.setattr(monitor_mod.socket, "gethostbyname", lambda _n: "203.0.113.9")
    mon = Monitor(recorder=app.recorder)
    mon.running = True
    mon.target_input = "example.com"
    mon.target_ip = "203.0.113.9"
    mon._generation = 1
    mon._begin_recording(1)
    app._monitors["example.com"] = mon

    app.recording["enabled"].set(False)
    app._apply_recording()
    assert app.recorder is None and mon.recorder is None and mon._rec_runs == {}

    app.recording["enabled"].set(True)
    app._apply_recording()
    assert app.recorder is not None and mon.recorder is app.recorder
    assert mon._rec_runs.get(1) is not None, "the running target records again at once"
    app.recorder.flush()
    runs = Reader(history.default_dir()).runs("example.com")
    assert len(runs) == 2 and runs[0]["ended"] is not None


def test_changed_retention_reaches_the_recorder(app):
    rec = app.recorder
    app.recording["summary_days"].set("90")
    app._apply_recording()
    assert app.recorder is rec and rec.options["summary_days"] == 90


def test_clear_history_asks_first(app, monkeypatch):
    rec = app.recorder
    rec.event(None, time.time(), "info", "something recorded")
    rec.flush()
    answers = iter([False, True])
    monkeypatch.setattr(gui_recording.messagebox, "askyesno", lambda *a, **k: next(answers))
    app.clear_history()                      # "No"
    rec.flush()
    assert Reader(history.default_dir()).events()
    app.clear_history()                      # "Yes"
    rec.flush()
    assert Reader(history.default_dir()).events() == []
