"""Start with Windows runs this copy: main.py for a checkout, the module for a
pip install (whose package has no main.py beside it)."""
import sys

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
