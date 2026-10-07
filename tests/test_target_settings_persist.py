"""Each target must come back from a relaunch with its own settings.

Settings are per target while the app runs (Edit settings... shows and changes
one target's), and the README says they are remembered. But only the target
*names* were saved: _restore_targets put each name in the toolbar and called
_start(), which reads every engine and alert value from the toolbar -- i.e.
from whichever target was edited last. Reproduced: a target set to a 0.5 s
interval with logging came back at 7 s with no log, the other target's values.
"""
import pytest

from pingerplot.monitor import Monitor


@pytest.fixture
def no_probing(monkeypatch):
    # start() still records the whole configuration; only the worker is idle.
    monkeypatch.setattr(Monitor, "_run", lambda self, gen: None)


def _start(app, target, interval, log, loss):
    app.interval_var.set(interval)
    app.logpath_var.set(log)
    app.alert_loss_var.set(loss)
    app.target_var.set(target)
    app._start()


def _relaunch(app, isolated_settings, monkeypatch):
    app._save_settings()
    for name in list(app._monitors):
        app._monitors.pop(name).shutdown()
    restored = {}
    monkeypatch.setattr(Monitor, "start",
                        lambda self, target, **kw: restored.__setitem__(target, kw))
    app._settings = isolated_settings.load()
    app.resume_var.set(True)
    app._restore_targets()
    return restored


def test_each_target_comes_back_with_its_own_settings(app, isolated_settings, tmp_path,
                                                      monkeypatch, no_probing):
    _start(app, "192.0.2.1", "0.5", str(tmp_path / "probe.csv"), "5")
    _start(app, "192.0.2.2", "7", "", "40")

    restored = _relaunch(app, isolated_settings, monkeypatch)

    a, b = restored["192.0.2.1"], restored["192.0.2.2"]
    assert a["interval"] == 0.5 and a["alert_loss_pct"] == 5.0
    assert a["log_path"].endswith("probe_192.0.2.1.csv")
    assert b["interval"] == 7.0 and b["alert_loss_pct"] == 40.0 and b["log_path"] == ""


def test_a_target_saved_by_an_older_version_uses_the_toolbar(app, isolated_settings,
                                                            monkeypatch):
    """1.3.x settings files have names only; they must still restore."""
    isolated_settings.save({"targets": ["192.0.2.9"], "interval": "3"})
    restored = {}
    monkeypatch.setattr(Monitor, "start",
                        lambda self, target, **kw: restored.__setitem__(target, kw))
    app._settings = isolated_settings.load()
    app._apply_saved_settings()
    app.resume_var.set(True)
    app._restore_targets()
    assert restored["192.0.2.9"]["interval"] == 3.0


def test_a_corrupt_saved_entry_falls_back_instead_of_killing_startup(app, isolated_settings,
                                                                    monkeypatch, no_probing):
    """_restore_targets runs inside App.__init__: an exception there means the
    app does not open at all."""
    isolated_settings.save({
        "targets": ["192.0.2.1", "192.0.2.2"],
        "target_options": {
            "192.0.2.1": {"interval": "not a number", "bogus_key": 1},
            "192.0.2.2": ["not", "a", "dict"],
        },
    })
    app._settings = isolated_settings.load()
    app.resume_var.set(True)
    app._restore_targets()
    assert set(app._monitors) == {"192.0.2.1", "192.0.2.2"}
    for m in app._monitors.values():
        assert m.interval == float(app.interval_var.get())
