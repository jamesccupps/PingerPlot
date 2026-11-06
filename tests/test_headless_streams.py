"""The headless runner's output can't stop it: no stdout at all (pythonw.exe,
the README's Task Scheduler line), or a redirected cp1252 stdout meeting a
character it can't encode."""
import io
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from pingerplot import headless

REPO = Path(__file__).resolve().parent.parent


def _target(name="bücher.例え.example", status="Monitoring"):
    mon = SimpleNamespace(summary=lambda: (3, 0.0, 12.0, 1.0, True), running=True,
                          status=status, events_after=lambda seq: ([], seq))
    return SimpleNamespace(monitor=mon, name=name, restarts=0, last_event=-1)


def test_no_stdout_is_not_an_error(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    headless._flush()
    headless.print_status([_target()])
    headless.print_events([_target()])


def test_an_unencodable_name_is_replaced_not_raised(monkeypatch):
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", out)
    headless._tolerant_streams()
    headless.print_status([_target()])
    out.flush()
    text = raw.getvalue().decode("cp1252")
    assert "b\xfccher" in text and "\\u4f8b" in text     # ü fits cp1252; 例 is escaped


@pytest.mark.skipif(sys.platform != "win32", reason="pythonw.exe is Windows'")
def test_the_runner_survives_pythonw(tmp_path):
    """The README's line: pythonw.exe -m pingerplot.headless config.json. It
    used to exit with AttributeError at the first status line."""
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        pytest.skip("no pythonw.exe beside this Python")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"status_interval": 0.3,
                               "defaults": {"interval": 0.5, "timeout_ms": 500},
                               "targets": [{"target": "127.0.0.1", "final_hop_only": True}]}))
    p = subprocess.Popen([str(pyw), "-m", "pingerplot.headless", str(cfg)], cwd=REPO)
    try:
        time.sleep(4)
        assert p.poll() is None, f"pythonw runner exited with {p.returncode}"
    finally:
        p.kill()
        p.wait(10)
