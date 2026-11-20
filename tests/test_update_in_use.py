"""An update while another PingerPlot program runs from the program folder
(the headless runner under Task Scheduler): Windows won't let a running
program be replaced, and the silent installer failed after the window had
quit, leaving nothing running. It's refused up front instead."""
import shutil
import subprocess
import sys
import time

import pytest

from pingerplot import updater


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file locking")
def test_a_running_program_in_the_folder_is_found(tmp_path):
    # A stand-in for the headless runner: ping.exe under its name, running.
    exe = tmp_path / updater.HEADLESS_EXE
    shutil.copy(r"C:\Windows\System32\PING.EXE", exe)
    (tmp_path / updater.GUI_EXE).write_bytes(b"not running")
    p = subprocess.Popen([str(exe), "-n", "30", "127.0.0.1"], stdout=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        time.sleep(0.5)
        assert updater.running_from(tmp_path) == [updater.HEADLESS_EXE]
    finally:
        p.kill()
        p.wait(10)
    assert updater.running_from(tmp_path) == []


def test_the_update_is_refused_while_one_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "program_dir", lambda: tmp_path)
    monkeypatch.setattr(updater, "running_from", lambda folder: [updater.HEADLESS_EXE])
    spawned = []
    monkeypatch.setattr(updater, "_spawn", lambda *a, **k: spawned.append(a))
    u = updater.Update("9.9.9", None, "installed", {"file": "PingerPlot-Setup.exe"}, "")
    with pytest.raises(updater.UpdateError, match="pingerplot-headless.exe is running from"):
        updater.apply(u, tmp_path / "PingerPlot-Setup.exe")
    assert spawned == [], "nothing started, so this copy keeps running"
