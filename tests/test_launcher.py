r"""launch.ps1: what it runs elevated is what it checked, and the elevated
logon task is retired.

-InstallAutostart used to register a task that ran the app ELEVATED at every
sign-in with no UAC prompt. The 2026-11-05 review showed a program running
unelevated as you could steer it: register a Python under HKCU for the py
launcher to pick (the setup's elevated pass asked the launcher, so that
Python ran elevated there and then), or edit settings.json so the elevated app
wrote and renamed files where it chose. It now only explains that it's
retired; -UninstallAutostart removes an old task.

The one-off elevated launch and the shortcuts resolve the real interpreter
unelevated and run it with -E -s, so a per-user PYTHONPATH and user
site-packages .pth files (both writable unelevated) don't run elevated. (-I
would also drop the script's folder from sys.path, breaking `import pingerplot`.)
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "launch.ps1"


def _src():
    return SCRIPT.read_text(encoding="utf-8")


def test_the_elevated_logon_task_is_retired():
    src = _src()
    assert "Register-ScheduledTask" not in src and "-RunLevel Highest" not in src
    assert "elseif ($InstallAutostart)   { Show-AutostartRetired }" in src
    assert "Unregister-ScheduledTask" in src, "an old task must still be removable"
    setup = (REPO / "Setup.cmd").read_text(encoding="utf-8")
    assert "-InstallAutostart" not in setup and "-UninstallAutostart" in setup


def test_the_elevated_launch_resolves_the_interpreter_unelevated():
    src = _src()
    start_app = src[src.index("function Start-App"):src.index("function New-Lnk")]
    elevated = start_app[start_app.index("if ($AsAdmin"):]
    assert "Resolve-RealPython" in elevated and "-Isolated" in elevated
    assert elevated.index("Resolve-RealPython") < elevated.index("-Verb RunAs")


def test_shortcuts_point_at_the_real_interpreter_and_there_is_no_admin_one():
    src = _src()
    shortcuts = src[src.index("function New-AppShortcuts"):src.index("function Show-AutostartRetired")]
    assert "Resolve-RealPython" in shortcuts and "-Isolated" in shortcuts
    assert "(Admin).lnk" not in shortcuts and "$true" not in shortcuts


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
