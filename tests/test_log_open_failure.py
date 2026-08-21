"""A probe log that cannot be opened must say so, not vanish.

_open_log caught OSError and reported it by assigning self.status. But
_open_log runs inside start(), before the worker thread launches, and the very
first thing _run does is set "Resolving <target>...". The message survived a
few milliseconds and was then overwritten -- with no Event logged either, so
the Events tab showed nothing at all.

The result was the exact failure supervise()'s own docstring rails against: a
run that looks entirely normal ("Monitoring 8.8.8.8 [...] via ICMP") while
writing nothing. Whoever asked for crash-safe CSV logging came back to an
empty file and no explanation.

Triggers on a missing directory, a read-only volume, a permission error, or a
path that is a directory.
"""
import os
import time

import pytest

from pingerplot import headless
from pingerplot.monitor import Monitor


def _wait(pred, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_an_unopenable_log_is_announced_in_the_events_tab(tmp_path):
    m = Monitor()
    bad = tmp_path / "no-such-dir" / "probe.csv"
    m.start("127.0.0.1", interval=0.3, timeout_ms=400, final_hop_only=True,
            log_path=str(bad))
    try:
        assert _wait(lambda: m.events_after(-1)[0]), "no events at all"
        texts = [e.text for e in m.events_after(-1)[0]]
        assert any("logging DISABLED" in t.upper() or "DISABLED" in t for t in texts), texts
        assert m._log_fh is None
        # And it is a warn, so it is styled and cannot beep or fire a webhook.
        kinds = {e.kind for e in m.events_after(-1)[0] if "DISABLED" in e.text}
        assert kinds == {"warn"}
    finally:
        m.shutdown()


def test_the_note_does_not_hijack_the_status_line(tmp_path):
    """It must not go back to being a status: the status is the one thing the
    worker overwrites on every round."""
    m = Monitor()
    m.start("127.0.0.1", interval=0.3, timeout_ms=400, final_hop_only=True,
            log_path=str(tmp_path / "nope" / "probe.csv"))
    try:
        assert _wait(lambda: m.status.startswith("Monitoring"), timeout=10.0), m.status
        assert "Log file error" not in m.status
        assert m.log_note                     # the note itself survives
    finally:
        m.shutdown()


def test_a_working_log_leaves_no_note(tmp_path):
    m = Monitor()
    good = tmp_path / "probe.csv"
    m.start("127.0.0.1", interval=0.3, timeout_ms=400, final_hop_only=True,
            log_path=str(good))
    try:
        assert _wait(lambda: good.exists())
        assert m.log_note == ""
        assert not [e for e in m.events_after(-1)[0] if "DISABLED" in e.text]
    finally:
        m.shutdown()


def test_reopening_clears_a_stale_note(tmp_path):
    m = Monitor()
    m.log_note = "left over from a previous start()"
    m.log_path = str(tmp_path / "probe.csv")
    m._open_log()
    try:
        assert m.log_note == ""
    finally:
        m._close_log()


def test_headless_creates_the_directory_for_an_absolute_log_path(tmp_path, monkeypatch):
    """The makedirs used to live inside the `not isabs` branch, so an absolute
    path under a directory that did not exist simply failed to open."""
    started = []
    monkeypatch.setattr(headless.Monitor, "start",
                        lambda self, target, **kw: started.append((target, kw)))
    dest = tmp_path / "logs" / "deep" / "hop.csv"
    cfg = {"targets": [{"target": "127.0.0.1", "log_path": str(dest)}]}
    headless._build_monitors(cfg, tmp_path)
    assert os.path.isdir(dest.parent), "absolute log_path's directory was not created"
    assert started and started[0][1]["log_path"] == str(dest)
