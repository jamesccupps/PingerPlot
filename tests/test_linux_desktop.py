"""Linux: the applications-menu entry and starting at sign-in, as freedesktop
.desktop files. Every test points the XDG folders at tmp_path, so nothing
is written to a real home directory."""
import sys

import pytest

from pingerplot import xdg


@pytest.fixture
def home(tmp_path, monkeypatch):
    for var, sub in (("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"),
                     ("XDG_CACHE_HOME", "cache")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setattr(xdg, "IS_LINUX", True)
    return tmp_path


def _keys(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "[Desktop Entry]"
    return dict(line.split("=", 1) for line in lines[1:] if line)


@pytest.mark.parametrize("arg,quoted", [
    ("/usr/bin/python3", "/usr/bin/python3"),
    ("/home/j/My Apps/main.py", '"/home/j/My Apps/main.py"'),
    ('a"b', '"a\\\\"b"'),             # file text: "a\\"b"
    ("50%", "50%%"),
    ("$HOME", '"\\\\$HOME"'),          # file text: "\\$HOME"
    ("C:\\x", '"C:\\\\\\\\x"'),         # a literal backslash is four
    ("--minimized", "--minimized"),
])
def test_exec_quoting(arg, quoted):
    assert xdg._exec_quote(arg) == quoted


def test_a_checkout_runs_its_main_py():
    args = xdg.launch_args()
    assert args[0] == sys.executable and args[1].endswith("main.py")


def test_a_pip_install_runs_the_module(tmp_path, monkeypatch):
    (tmp_path / "pingerplot").mkdir()
    monkeypatch.setattr(xdg, "__file__", str(tmp_path / "pingerplot" / "xdg.py"))
    assert xdg.launch_args() == [sys.executable, "-m", "pingerplot"]


def test_the_menu_entry(home):
    assert not xdg.menu_entry_enabled()
    assert xdg.set_menu_entry(True) and xdg.menu_entry_enabled()
    k = _keys(xdg.menu_entry_path())
    assert k["Name"] == "PingerPlot" and k["Type"] == "Application" and k["Terminal"] == "false"
    assert k["Exec"] == xdg.exec_line() and "--minimized" not in k["Exec"]
    assert k["StartupWMClass"] == xdg.WM_CLASS
    assert k["Categories"].endswith(";") and "Network" in k["Categories"]
    assert k["Icon"].startswith(str(home / "cache")) and k["Icon"].endswith(".png")
    assert xdg.set_menu_entry(False) and not xdg.menu_entry_enabled()


def test_the_autostart_entry_starts_minimized(home):
    assert xdg.set_autostart(True) and xdg.autostart_enabled()
    assert xdg.autostart_path() == home / "config" / "autostart" / "pingerplot.desktop"
    k = _keys(xdg.autostart_path())
    assert k["Exec"].endswith(" --minimized") and k["X-GNOME-Autostart-enabled"] == "true"
    assert xdg.set_autostart(False) and not xdg.autostart_enabled()


def test_entries_follow_the_copy_when_it_moves(home, monkeypatch):
    xdg.set_menu_entry(True)
    xdg.set_autostart(True)
    monkeypatch.setattr(xdg, "launch_args", lambda: ["/opt/new/python3", "-m", "pingerplot"])
    xdg.refresh_entries()
    assert _keys(xdg.menu_entry_path())["Exec"] == "/opt/new/python3 -m pingerplot"
    assert _keys(xdg.autostart_path())["Exec"] == "/opt/new/python3 -m pingerplot --minimized"


def test_refresh_adds_nothing_that_wasnt_there(home):
    xdg.refresh_entries()
    assert not xdg.menu_entry_enabled() and not xdg.autostart_enabled()


def test_not_linux_means_nothing_written(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(xdg, "IS_LINUX", False)
    assert xdg.set_menu_entry(True) is False and xdg.set_autostart(True) is False
    assert not list(tmp_path.rglob("*.desktop"))


def test_the_window_toggles_the_menu_entry(app, home):
    app.menu_entry_var.set(True)
    app._toggle_menu_entry()
    assert xdg.menu_entry_enabled() and app.menu_entry_var.get() is True
    app.menu_entry_var.set(False)
    app._toggle_menu_entry()
    assert not xdg.menu_entry_enabled()


@pytest.mark.parametrize("arg", ["/home/j/My Apps/main.py", '/opt/a"b/main.py', "/srv/$HOME/x",
                                 "/srv/back\\slash/x", "/srv/tick`x/y"])
def test_exec_quoting_reads_back_as_the_spec_says(arg):
    """Unescape the way a desktop does (Desktop Entry spec: string level,
    then quoting) and get the argument back."""
    field = xdg._exec_quote(arg)
    string_level = field.replace("\\\\", "\x00").replace("\x00", "\\")
    assert string_level.startswith('"') and string_level.endswith('"')
    inner, out, i = string_level[1:-1], "", 0
    while i < len(inner):
        if inner[i] == "\\" and i + 1 < len(inner) and inner[i + 1] in '"`$\\':
            out += inner[i + 1]
            i += 2
        else:
            out += inner[i]
            i += 1
    assert out == arg
