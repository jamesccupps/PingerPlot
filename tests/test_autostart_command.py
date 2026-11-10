"""Start with Windows runs this copy: main.py for a checkout, the module for a
pip install (whose package has no main.py beside it), and for a Scoop install
the path that survives ``scoop update``."""
import os
import subprocess
import sys

import pytest

from pingerplot import winplatform


def test_a_checkout_runs_main_py():
    cmd = winplatform.autostart_command()
    assert cmd.endswith('main.py" --minimized')


def test_a_pip_install_runs_the_module(tmp_path, monkeypatch):
    (tmp_path / "pingerplot").mkdir()
    monkeypatch.setattr(winplatform, "__file__", str(tmp_path / "pingerplot" / "winplatform.py"))
    cmd = winplatform.autostart_command()
    assert cmd.endswith('" -m pingerplot --minimized') and "main.py" not in cmd


def test_a_frozen_copy_runs_its_exe(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "PingerPlot.exe"))
    assert winplatform.autostart_command() == f'"{tmp_path / "PingerPlot.exe"}" --minimized'


def _link_dir(link, target):
    """A directory junction on Windows (what Scoop makes; no admin needed),
    a symlink elsewhere."""
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


@pytest.fixture
def scoop_app(tmp_path, monkeypatch):
    """apps/pingerplot/1.4.4 with a ``current`` junction to it, as Scoop lays
    an app out, and a frozen copy in it."""
    app = tmp_path / "apps" / "pingerplot"
    version = app / "1.4.4"
    version.mkdir(parents=True)
    (version / "PingerPlot.exe").write_bytes(b"")
    _link_dir(app / "current", version)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    yield app
    # Remove the link itself, before pytest's cleanup gets to it: a recursive
    # delete through a junction can empty the folder it points at.
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "rmdir", str(app / "current")], check=False)
    else:
        (app / "current").unlink()


@pytest.mark.parametrize("started_from", ["current", "1.4.4"])
def test_a_scoop_copy_starts_through_current(scoop_app, monkeypatch, started_from):
    """Start with Windows names the junction, not the version folder that
    ``scoop update`` leaves behind and ``scoop cleanup`` deletes -- however this
    copy was started (the Start menu shortcut goes through ``current``; an
    in-app update relaunches from the resolved folder)."""
    monkeypatch.setattr(sys, "executable", str(scoop_app / started_from / "PingerPlot.exe"))
    cmd = winplatform.autostart_command()
    assert cmd == f'"{scoop_app.resolve() / "current" / "PingerPlot.exe"}" --minimized'


def test_a_folder_named_current_is_just_a_folder(tmp_path, monkeypatch):
    """A copy in a real folder called ``current`` keeps its own path."""
    here = tmp_path / "current"
    here.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(here / "PingerPlot.exe"))
    assert winplatform.autostart_command() == f'"{here.resolve() / "PingerPlot.exe"}" --minimized'


def test_an_old_version_folder_still_starts_through_current(scoop_app, monkeypatch):
    """The in-app updater writes the new version into the folder it ran from,
    say 1.4.3, after which ``scoop update`` moves ``current`` on. That copy too
    names ``current``, or ``scoop cleanup`` would strand it."""
    old = scoop_app / "1.4.3"
    old.mkdir()
    (old / "PingerPlot.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(old / "PingerPlot.exe"))
    cmd = winplatform.autostart_command()
    assert cmd == f'"{scoop_app.resolve() / "current" / "PingerPlot.exe"}" --minimized'


def test_a_current_link_elsewhere_is_not_trusted(tmp_path, monkeypatch):
    """Outside Scoop's layout a ``current`` beside the folder could be
    anything: a link out of the parent folder, or to a folder without this
    exe, is ignored."""
    tools = tmp_path / "tools"
    mine = tools / "PingerPlot"
    mine.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "PingerPlot.exe").write_bytes(b"")
    unrelated = tools / "SomethingElse"
    unrelated.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(mine / "PingerPlot.exe"))
    for target in (elsewhere, unrelated):
        _link_dir(tools / "current", target)
        try:
            assert winplatform.autostart_command() == \
                f'"{mine.resolve() / "PingerPlot.exe"}" --minimized', target
        finally:
            if sys.platform == "win32":
                subprocess.run(["cmd", "/c", "rmdir", str(tools / "current")], check=False)
            else:
                (tools / "current").unlink()
