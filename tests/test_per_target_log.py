"""Two targets must never write into the same probe CSV.

The GUI's log path is one field in the Engine dialog, and every target started
from it -- and every target restored at launch -- was handed that same path.
The probe CSV has no target column, so their rows interleaved into one file
where the first few hops (the same local gateways for every target) could not
be told apart. Rotation broke too: one monitor renamed the file while the
others still held it open (on Windows the rename simply fails, so the size cap
stopped being enforced at all).

The fix is a file per target, derived from the configured path. Headless gets
the same treatment when a shared `defaults.log_path` puts several targets on
one file.
"""
import os

import pytest

from pingerplot import headless
from pingerplot.monitor import log_path_template, per_target_log_path


def test_the_target_goes_before_the_extension():
    assert per_target_log_path(os.path.join("logs", "probe.csv"), "8.8.8.8") == \
        os.path.join("logs", "probe_8.8.8.8.csv")


def test_deriving_twice_is_a_no_op():
    """Edit settings loads the derived path back into the field; pressing
    Add / Start again must not grow probe_x_x_x.csv."""
    once = per_target_log_path("probe.csv", "gw.example.net")
    assert per_target_log_path(once, "gw.example.net") == once


def test_unsafe_characters_cannot_escape_the_directory():
    out = per_target_log_path(os.path.join("logs", "p.csv"), "..\\..\\evil/x:y")
    # Dots inside a basename are inert; what matters is that no separator or
    # drive colon survives, so the file cannot land outside logs/.
    assert os.path.dirname(out) == "logs"
    assert os.path.dirname(os.path.normpath(out)) == "logs"
    for ch in '\\/:':
        assert ch not in os.path.basename(out)


def test_blank_path_stays_blank():
    assert per_target_log_path("", "8.8.8.8") == ""


def test_template_round_trips():
    derived = per_target_log_path("probe.csv", "8.8.8.8")
    assert log_path_template(derived, "8.8.8.8") == "probe.csv"
    assert log_path_template("other.csv", "8.8.8.8") == "other.csv"


def test_gui_gives_each_target_its_own_file(app, tmp_path, monkeypatch):
    started = {}

    def fake_start(self, target, **kw):
        started[target] = kw["log_path"]

    monkeypatch.setattr("pingerplot.gui.Monitor.start", fake_start)
    app.logpath_var.set(str(tmp_path / "probe.csv"))
    for t in ("192.0.2.1", "192.0.2.2"):
        app.target_var.set(t)
        app._start()
    assert started["192.0.2.1"] != started["192.0.2.2"]
    assert started["192.0.2.1"].endswith("probe_192.0.2.1.csv")


def test_headless_splits_a_shared_default_log_path(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(headless.Monitor, "start",
                        lambda self, target, **kw: started.append((target, kw["log_path"])))
    cfg = {"defaults": {"log_path": "logs/all.csv"},
           "targets": [{"target": "192.0.2.1"}, {"target": "192.0.2.2"},
                       {"target": "192.0.2.3", "log_path": "logs/own.csv"}]}
    headless._build_monitors(cfg, tmp_path)
    paths = dict(started)
    assert len(set(paths.values())) == 3
    assert paths["192.0.2.1"].endswith("all_192.0.2.1.csv")
    assert paths["192.0.2.3"].endswith("own.csv"), "an unshared path is left exactly as configured"


@pytest.mark.parametrize("target", ["192.0.2.1", "host.example.net"])
def test_headless_keeps_an_explicit_per_target_path(tmp_path, monkeypatch, target):
    started = []
    monkeypatch.setattr(headless.Monitor, "start",
                        lambda self, t, **kw: started.append(kw["log_path"]))
    headless._build_monitors({"targets": [{"target": target, "log_path": "x.csv"}]}, tmp_path)
    assert started == [str(tmp_path / "x.csv")]
