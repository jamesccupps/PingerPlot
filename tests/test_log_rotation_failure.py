"""A probe-log rollover that fails must say so, and must not thrash.

_rotate_log_if_needed closed the log, tried to rename it to .1, swallowed any
error, and reopened. Two silent outcomes followed:

* Rename fails, reopen succeeds -- on Windows, whenever someone has the CSV
  open in Excel. The file is still over the cap, so the very next probe tries
  again: close, fail, reopen, on every probe, forever, and the size cap the
  rollover exists to enforce is never enforced. Nothing said so.
* Reopen fails. _open_log records log_note, but that note is only ever read at
  start-up, so logging stopped mid-run with no event and a normal-looking
  status line -- the failure 1.3.2 removed from the start-up path.

It also deleted the existing .1 backup *before* attempting the rename, so a
failed rename cost the backup as well.
"""
import builtins
import sys

import pytest

from pingerplot import icmp, monitor as monitor_mod
from pingerplot.monitor import Monitor


def _result():
    return icmp.PingResult(icmp.IP_SUCCESS, 1.0, "192.0.2.1", True)


def _monitor(tmp_path, monkeypatch, cap=200):
    monkeypatch.setattr(monitor_mod, "MAX_LOG_BYTES", cap)
    m = Monitor()
    m.log_path = str(tmp_path / "probe.csv")
    m._open_log()
    return m


def _warns(m):
    return [e.text for e in m.events_after(-1)[0] if e.kind == "warn"]


def test_a_failed_rename_warns_once_and_does_not_reopen_every_probe(tmp_path, monkeypatch):
    m = _monitor(tmp_path, monkeypatch)
    (tmp_path / "probe.1.csv").write_text("older backup\n", encoding="utf-8")

    def locked(*_a, **_k):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(monitor_mod.os, "replace", locked)
    opens = []
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open",
                        lambda *a, **k: (opens.append(a[0]), real_open(*a, **k))[1])
    try:
        for _ in range(40):
            m._log_probe(1, _result())
    finally:
        monkeypatch.setattr(builtins, "open", real_open)

    warns = _warns(m)
    assert len(warns) == 1 and "rollover failed" in warns[0], warns
    assert len(opens) <= 1, f"log reopened {len(opens)} times while the rename kept failing"
    assert m._log_fh is not None, "logging must carry on past the cap"
    assert (tmp_path / "probe.1.csv").read_text(encoding="utf-8") == "older backup\n", \
        "the existing backup was deleted by a rollover that never happened"
    m._close_log()


def test_a_failed_reopen_after_rollover_is_announced(tmp_path, monkeypatch):
    m = _monitor(tmp_path, monkeypatch)
    real_open_log = m._open_log

    def fail_open():
        real_open_log()
        m._close_log()
        m.log_note = f"Probe logging DISABLED - cannot open {m.log_path}: disk gone"

    m._open_log = fail_open
    for _ in range(10):
        m._log_probe(1, _result())
    warns = _warns(m)
    assert any("disk gone" in w for w in warns), warns
    assert m._log_fh is None


def test_a_successful_rollover_still_works(tmp_path, monkeypatch):
    m = _monitor(tmp_path, monkeypatch)
    for _ in range(10):
        m._log_probe(1, _result())
    assert (tmp_path / "probe.1.csv").exists()
    assert (tmp_path / "probe.csv").read_text(encoding="utf-8").startswith("epoch,")
    assert _warns(m) == []
    m._close_log()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file-sharing semantics")
def test_a_real_reader_holding_the_csv(tmp_path, monkeypatch):
    """No monkeypatched rename: a second handle on the file is enough to make
    Windows refuse it (WinError 32), exactly as Excel does."""
    m = _monitor(tmp_path, monkeypatch)
    with open(tmp_path / "probe.csv", "r", encoding="utf-8"):
        for _ in range(40):
            m._log_probe(1, _result())
    assert len(_warns(m)) == 1
    assert not (tmp_path / "probe.1.csv").exists()
    monkeypatch.setattr(m, "_rotate_retry_at", 0.0)     # retry window elapsed
    m._log_probe(1, _result())                           # reader has let go
    assert (tmp_path / "probe.1.csv").exists()
    assert any(e.kind == "info" and "rolled over" in e.text for e in m.events_after(-1)[0])
    m._close_log()
