"""The engine records what it measures: runs, probes, events and trouble."""
import json
import time

import pytest

from pingerplot import icmp, monitor as monitor_mod
from pingerplot.history import Reader, Recorder
from pingerplot.monitor import Monitor

needs_icmp = pytest.mark.skipif(not icmp.is_available(),
                                reason=f"needs a working ICMP backend: {icmp.unavailable_reason()}")

SECRET_HOOK = "https://hooks.example.com/services/T000/B000/not-a-real-secret"


def _wait(pred, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def rec(tmp_path):
    r = Recorder(tmp_path).start()
    yield r
    r.close()


@needs_icmp
def test_a_run_lands_in_the_history(tmp_path, rec):
    m = Monitor(recorder=rec)
    m.start("127.0.0.1", interval=0.3, timeout_ms=400, final_hop_only=True,
            webhook_url=SECRET_HOOK)
    try:
        assert _wait(lambda: len(m.samples_for(1)) >= 3, 10)
    finally:
        m.shutdown()
    rec.flush()

    r = Reader(tmp_path)
    (run,) = r.runs("127.0.0.1")
    assert run["target_ip"] == "127.0.0.1" and run["ended"] is not None
    options = json.loads(run["options"])
    assert options["final_hop_only"] is True and options["webhook_url"] is True
    probes = r.probes("127.0.0.1", 0, time.time() + 1)
    assert len(probes) >= 3 and all(p["addr"] == "127.0.0.1" for p in probes if p["rtt"] is not None)
    # interval 0.3 s under a 400 ms timeout: the note is an event, so it's recorded
    assert any("each round waits" in e["text"] for e in r.events(target="127.0.0.1"))
    r.close()


@needs_icmp
def test_the_webhook_secret_never_reaches_the_file(tmp_path, rec):
    m = Monitor(recorder=rec)
    m.start("127.0.0.1", interval=0.3, timeout_ms=400, final_hop_only=True,
            webhook_url=SECRET_HOOK)
    try:
        assert _wait(lambda: m.status.startswith("Monitoring"), 10)
    finally:
        m.shutdown()
    rec.close()
    for f in tmp_path.iterdir():
        assert b"not-a-real-secret" not in f.read_bytes(), f.name


def test_a_name_that_wont_resolve_is_recorded(tmp_path, rec, monkeypatch):
    def nxdomain(_name):
        raise OSError(11001, "No such host is known")
    monkeypatch.setattr(monitor_mod.socket, "gethostbyname", nxdomain)
    m = Monitor(recorder=rec)
    m.start("gone.example.invalid", interval=1, final_hop_only=True)
    assert _wait(lambda: not m.running)
    m.shutdown()
    rec.flush()
    r = Reader(tmp_path)
    (run,) = r.runs("gone.example.invalid")
    assert run["ended"] is not None and run["target_ip"] is None
    (ev,) = r.events(target="gone.example.invalid")
    assert ev["kind"] == "warn" and "Cannot resolve" in ev["text"]


def test_recording_trouble_shows_in_the_targets_events(tmp_path, monkeypatch):
    def nxdomain(_name):
        raise OSError(11001, "No such host is known")
    monkeypatch.setattr(monitor_mod.socket, "gethostbyname", nxdomain)
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    broken = Recorder(blocker / "history").start()
    try:
        broken.flush(timeout=5)              # let it try (and fail) to open
        m = Monitor(recorder=broken)
        m.start("gone.example.invalid", interval=1, final_hop_only=True)
        assert _wait(lambda: not m.running)
        texts = [e.text for e in m.events_after(-1)[0]]
        m.shutdown()
        assert any("History recording is off" in t for t in texts), texts
    finally:
        broken.close()


def test_a_restarted_target_closes_both_runs(tmp_path, rec, monkeypatch):
    """stop() joins the worker with a timeout, so a slow one can still be
    finishing after the next start(); it must close its own run, not the new
    one's."""
    def slow_nxdomain(_name):
        time.sleep(0.3)
        raise OSError(11001, "No such host is known")
    monkeypatch.setattr(monitor_mod.socket, "gethostbyname", slow_nxdomain)
    m = Monitor(recorder=rec)
    m.start("a.example.invalid", interval=1, final_hop_only=True)
    m.start("b.example.invalid", interval=1, final_hop_only=True)
    assert _wait(lambda: not m.running)
    m.shutdown()
    time.sleep(0.5)                          # the first worker's DNS call returns
    rec.flush()
    runs = Reader(tmp_path).runs()
    assert len(runs) == 2 and all(r["ended"] is not None for r in runs), [dict(r) for r in runs]


def test_without_a_recorder_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(monitor_mod.socket, "gethostbyname",
                        lambda _n: (_ for _ in ()).throw(OSError("no")))
    m = Monitor()
    m.start("x.example.invalid", interval=1, final_hop_only=True)
    assert _wait(lambda: not m.running)
    m.shutdown()
    assert not list(tmp_path.rglob("history.sqlite3"))
