"""Starting the window without Tk says what to install, instead of a
ModuleNotFoundError traceback (most Linux distributions package tkinter
separately)."""
import sys
from pathlib import Path

import pytest

from pingerplot import launch

REPO = Path(__file__).resolve().parent.parent


def test_without_tk_it_names_the_package_and_exits_1(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "tkinter", None)      # import tkinter -> ImportError
    with pytest.raises(SystemExit) as e:
        launch.main()
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "sudo apt install python3-tk" in err and "sudo dnf install python3-tkinter" in err
    assert "pingerplot.headless" in err


def test_every_way_of_starting_the_window_goes_through_it():
    assert "from pingerplot.launch import main" in (REPO / "main.py").read_text(encoding="utf-8")
    assert "from .launch import main" in (REPO / "pingerplot" / "__main__.py").read_text(encoding="utf-8")
    assert 'pingerplot = "pingerplot.launch:main"' in (REPO / "pyproject.toml").read_text(encoding="utf-8")
