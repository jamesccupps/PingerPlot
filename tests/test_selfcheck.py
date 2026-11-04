"""Where settings live, and the self-test builds run (1.4).

The installer and the portable zip are verified by running the built program
with --self-test against a scratch data folder, so these are the two things
the packaging tests stand on: that PINGERPLOT_DATA_DIR really redirects
everything, that a portable copy is recognised by its marker file, and that
the self-test passes on a working build and says so in a file.
"""
import sys
from pathlib import Path

import pytest

from pingerplot import icmp, selfcheck, settings


def test_the_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGERPLOT_DATA_DIR", str(tmp_path))
    assert settings.config_dir() == tmp_path
    assert settings.save({"x": 1}) and (tmp_path / "settings.json").exists()


def test_source_runs_are_never_portable(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert settings.portable_dir() is None


def test_a_frozen_copy_with_the_marker_is_portable(tmp_path, monkeypatch):
    monkeypatch.delenv("PINGERPLOT_DATA_DIR", raising=False)
    exe = tmp_path / "PingerPlot.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert settings.portable_dir() is None, "no marker: not portable"
    (tmp_path / settings.PORTABLE_MARKER).write_text("", encoding="utf-8")
    assert settings.portable_dir() == tmp_path
    assert settings.config_dir() == tmp_path


@pytest.mark.skipif(not icmp.is_available(), reason=f"needs ICMP: {icmp.unavailable_reason()}")
def test_the_self_test_passes_and_writes_its_report(tmp_path, monkeypatch, capsys):
    pytest.importorskip("tkinter")
    import tkinter as tk
    try:
        tk.Tk().destroy()
    except tk.TclError as exc:
        pytest.skip(f"no display for Tk: {exc}")
    monkeypatch.setenv("PINGERPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "never"))
    assert selfcheck.run() == 0, capsys.readouterr().out
    report = (tmp_path / "self-test.txt").read_text(encoding="utf-8")
    assert report.rstrip().endswith("PASSED") and "FAIL" not in report
    assert not Path(tmp_path / "never").exists(), "the self-test wrote outside its data folder"


def test_a_failing_check_fails_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv("PINGERPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(icmp, "is_available", lambda: False)
    monkeypatch.setattr(icmp, "unavailable_reason", lambda: "simulated")
    monkeypatch.setitem(sys.modules, "tkinter", None)      # skip the window: it's not under test
    assert selfcheck.run() == 1
    report = (tmp_path / "self-test.txt").read_text(encoding="utf-8")
    assert "FAIL  icmp: OSError('simulated')" in report and report.rstrip().endswith("FAILED")
