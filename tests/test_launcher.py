r"""The auto-start task must run what it checked, isolated from per-user state.

launch.ps1 -InstallAutostart registers a logon task that runs the app
ELEVATED with no UAC prompt, and warns unless what it launches lives under an
admin-only path. Two holes made the "no warning" case still a UAC bypass:

* It preferred pyw.exe, which lives in C:\Windows and so passed the check --
  but the py launcher picks the interpreter at run time from HKCU\Software\
  Python, %LOCALAPPDATA%\py.ini and PY_PYTHON, all writable without elevation.
* Even a Program Files interpreter, run elevated as you, executes .pth files
  from your user site-packages (%APPDATA%\Python\PythonXY\site-packages) and
  honours a per-user PYTHONPATH. On the machine this was found on, user site
  was enabled and already populated.

The task now registers the interpreter the launcher resolves to, checks that
interpreter and its stdlib, and runs with -E -s. (-I would also drop the
script's folder from sys.path, breaking `import pingerplot`.)
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "launch.ps1"


def test_install_autostart_uses_the_resolved_isolated_interpreter():
    src = SCRIPT.read_text(encoding="utf-8")
    body = src[src.index("function Install-Autostart"):src.index("function Uninstall-Autostart")]
    assert "Resolve-RealPython" in body, "the task would register the py launcher"
    assert "-Isolated" in body, "the elevated task would load user site-packages"
    assert "$py.Prefix" in body, "the stdlib location is not checked"


def test_the_elevated_launch_paths_are_isolated_too():
    src = SCRIPT.read_text(encoding="utf-8")
    start_app = src[src.index("function Start-App"):src.index("function New-Lnk")]
    assert "-Verb RunAs" in start_app and "-Isolated" in start_app
    shortcuts = src[src.index("function New-AppShortcuts"):src.index("function Install-Autostart")]
    assert "$adminArgLine" in shortcuts.split("(Admin).lnk")[1].splitlines()[0]


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell launcher")
def test_resolution_and_arguments_live():
    cmd = (f". '{SCRIPT}'; $r = Resolve-RealPython (Find-PythonW); "
           "$r.Exe; $r.Prefix; Get-ArgLine $r -Isolated")
    out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-Command", cmd], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    exe, prefix, args = out.stdout.strip().splitlines()[-3:]
    assert Path(exe).name.lower() in ("pythonw.exe", "python.exe"), exe
    assert Path(exe).is_file() and Path(prefix).is_dir()
    assert args.startswith('-E -s "') and args.endswith('main.py"'), args


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell launcher")
def test_the_app_still_starts_under_the_isolation_flags():
    out = subprocess.run([sys.executable, "-E", "-s", str(REPO / "main.py"), "--version"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("PingerPlot ")
