"""The history database: what goes in, what comes out, and what happens when
the disk or the file misbehaves."""
import sqlite3
import time

import pytest

from pingerplot import history
from pingerplot.history import Reader, Recorder

T0 = 1_700_000_040.0          # a minute boundary, so minute arithmetic is exact
ROUTER = "198.51.100.1"       # RFC 5737
DEST = "203.0.113.9"


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def _rec(tmp_path, clock=None, **opts):
    return Recorder(tmp_path, options=opts or None, clock=clock or Clock()).start()


# --- options -----------------------------------------------------------------

def test_options_default_to_recording_everything():
    o = history.normalize(None)
    assert o["enabled"] and o["probes"] and o["summaries"] and o["events"]
    assert o == history.DEFAULTS


@pytest.mark.parametrize("raw, key, expected", [
    ({"probe_days": 0}, "probe_days", 1),            # never "keep nothing"
    ({"probe_days": "x"}, "probe_days", 7),          # unusable -> default
    ({"max_mb": 10**12}, "max_mb", 100_000),
    ({"probes": "no"}, "probes", True),              # only a real bool counts
    ({"events": False}, "events", False),
    ({"summary_days": float("inf")}, "summary_days", 365),
])
def test_options_are_clamped_and_typed(raw, key, expected):
    assert history.normalize(raw)[key] == expected


# --- the round trip ----------------------------------------------------------

def test_a_run_is_recorded_and_read_back(tmp_path):
    clock = Clock()
    rec = _rec(tmp_path, clock)
    run = rec.begin_run("example.com", "icmp", None, {"interval": 2.5})
    rec.set_target_ip(run, DEST)
    for i, (ttl, addr, rtt) in enumerate([(1, ROUTER, 5.0), (2, DEST, 20.0),
                                          (1, ROUTER, 7.0), (2, None, None),
                                          (1, ROUTER, 6.0), (2, DEST, 24.0)]):
        rec.probe(run, T0 + i, i // 2 + 1, ttl, addr, rtt, 0 if rtt else 11010)
    rec.event(run, T0 + 3, "route", "Hop 2: path changed")
    rec.end_run(run)
    rec.close()

    r = Reader(tmp_path)
    (row,) = r.runs("example.com")
    assert row["target_ip"] == DEST and row["mode"] == "icmp" and row["ended"] is not None
    assert row["program"] == "gui"

    probes = r.probes("example.com", T0, T0 + 10)
    assert len(probes) == 6
    assert [p["addr"] for p in probes if p["ttl"] == 2] == [DEST, "", DEST]

    mins = {(m["ttl"], m["addr"]): m for m in r.minutes("example.com", T0, T0 + 10)}
    hop1 = mins[(1, ROUTER)]
    assert (hop1["sent"], hop1["recv"], hop1["rtt_min"], hop1["rtt_max"]) == (3, 3, 5.0, 7.0)
    assert hop1["rtt_sum"] == 18.0
    assert hop1["jit_sum"] == 3.0 and hop1["jit_n"] == 2      # |7-5| + |6-7|
    lost = mins[(2, "")]
    assert (lost["sent"], lost["recv"]) == (1, 0)
    assert mins[(2, DEST)]["recv"] == 2

    (ev,) = r.events(target="example.com")
    assert ev["kind"] == "route" and ev["target"] == "example.com"
    r.close()


def test_a_finished_minute_reaches_disk_without_waiting_for_the_run_to_end(tmp_path):
    """A crash mid-run must not cost every minute since the start."""
    clock = Clock()
    rec = _rec(tmp_path, clock)
    run = rec.begin_run("t", "icmp", None, {})
    rec.probe(run, T0 + 1, 1, 1, ROUTER, 5.0, 0)
    clock.t = T0 + 61            # the next minute has begun
    rec.probe(run, T0 + 61, 2, 1, ROUTER, 5.0, 0)
    assert rec.flush()
    minutes = [m["minute"] for m in Reader(tmp_path).minutes("t", T0, T0 + 120)]
    assert minutes == [int(T0 // 60)]
    rec.close()


def test_events_without_a_run_are_kept(tmp_path):
    rec = _rec(tmp_path)
    rec.event(None, T0, "warn", "app-level note")
    rec.close()
    (ev,) = Reader(tmp_path).events()
    assert ev["text"] == "app-level note" and ev["target"] is None


def test_event_search_treats_wildcards_literally(tmp_path):
    rec = _rec(tmp_path)
    run = rec.begin_run("t", "icmp", None, {})
    rec.event(run, T0, "info", "100% loss")
    rec.event(run, T0 + 1, "info", "1000 loss")
    rec.close()
    r = Reader(tmp_path)
    assert [e["text"] for e in r.events(text="100%")] == ["100% loss"]
    assert [e["text"] for e in r.events(kinds=["info"], text="loss")] == ["100% loss", "1000 loss"]


def test_reading_a_missing_database_is_empty(tmp_path):
    r = Reader(tmp_path / "nothing-here")
    assert r.targets() == [] and r.events() == [] and r.runs() == []
    assert not (tmp_path / "nothing-here").exists(), "reading must not create it"


# --- what to record ----------------------------------------------------------

def test_probes_off_still_keeps_the_minutes(tmp_path):
    rec = _rec(tmp_path, probes=False)
    run = rec.begin_run("t", "icmp", None, {})
    rec.probe(run, T0, 1, 1, ROUTER, 5.0, 0)
    rec.end_run(run)
    rec.close()
    r = Reader(tmp_path)
    assert r.probes("t", T0 - 1, T0 + 1) == []
    assert len(r.minutes("t", T0 - 1, T0 + 1)) == 1


def test_events_off_records_none(tmp_path):
    rec = _rec(tmp_path, events=False)
    rec.event(rec.begin_run("t", "icmp", None, {}), T0, "alert", "x")
    rec.close()
    assert Reader(tmp_path).events() == []


# --- retention and the size cap ----------------------------------------------

def test_retention_drops_each_kind_after_its_own_age(tmp_path):
    clock = Clock()
    rec = _rec(tmp_path, clock, probe_days=1, summary_days=30, event_days=10)
    run = rec.begin_run("t", "icmp", None, {})
    rec.probe(run, T0, 1, 1, ROUTER, 5.0, 0)
    rec.event(run, T0, "info", "old")
    rec.end_run(run)
    assert rec.flush()
    rec.close()

    later = Recorder(tmp_path, options={"probe_days": 1, "summary_days": 30, "event_days": 10},
                     clock=Clock(T0 + 5 * 86400))
    db = sqlite3.connect(later.path, isolation_level=None)
    later._maintain(db)
    counts = {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("probes", "minutes", "events")}
    db.close()
    assert counts == {"probes": 0, "minutes": 1, "events": 1}


def test_the_size_cap_gives_up_the_oldest_probes_first(tmp_path):
    clock = Clock()
    rec = _rec(tmp_path, clock)
    run = rec.begin_run("t", "icmp", None, {})
    for i in range(20_000):
        rec.probe(run, T0 + i * 0.1, i, 1 + i % 10, ROUTER, 5.0, 0)
    rec.event(run, T0, "info", "keep me")
    rec.end_run(run)
    rec.close()

    cap = Recorder(tmp_path, clock=clock)
    db = sqlite3.connect(cap.path, isolation_level=None)
    before = db.execute("SELECT count(*) FROM probes").fetchone()[0]
    cap._cap(db, Recorder.used_bytes(db) // 2)
    after = db.execute("SELECT count(*), min(t) FROM probes").fetchone()
    events = db.execute("SELECT count(*) FROM events").fetchone()[0]
    db.close()
    assert after[0] < before and after[1] > T0, "the oldest probes go first"
    assert events == 1, "events outlast probes"


# --- when things go wrong -----------------------------------------------------

def test_a_damaged_file_is_set_aside_and_recording_carries_on(tmp_path):
    (tmp_path / history.DB_NAME).write_bytes(b"this is not a database" * 100)
    rec = _rec(tmp_path)
    run = rec.begin_run("t", "icmp", None, {})
    rec.event(run, T0, "info", "after the damage")
    assert rec.flush()
    rec.close()
    assert "damaged" in rec.problem and rec.problem_seq == 1
    assert list(tmp_path.glob(history.DB_NAME + ".damaged-*")), "the old file was kept"
    assert [e["text"] for e in Reader(tmp_path).events()] == ["after the damage"]


def test_a_folder_that_cant_be_made_turns_recording_off_with_a_reason(tmp_path):
    blocker = tmp_path / "file-not-folder"
    blocker.write_text("x")
    rec = Recorder(blocker / "history", clock=Clock()).start()
    run = rec.begin_run("t", "icmp", None, {})
    rec.probe(run, T0, 1, 1, ROUTER, 5.0, 0)
    rec.flush(timeout=5)
    rec.close()
    assert "off" in rec.problem and "history" in rec.problem


def test_a_newer_schema_is_left_alone(tmp_path):
    rec = _rec(tmp_path)
    rec.close()
    db = sqlite3.connect(tmp_path / history.DB_NAME, isolation_level=None)
    db.execute("UPDATE meta SET value='99' WHERE key='schema'")
    db.close()
    rec = _rec(tmp_path)
    rec.close()
    assert "newer" in rec.problem


def test_two_programs_can_record_at_once(tmp_path):
    """The GUI and the headless runner share the file."""
    gui = Recorder(tmp_path, clock=Clock(), program="gui").start()
    head = Recorder(tmp_path, clock=Clock(), program="headless").start()
    a = gui.begin_run("a", "icmp", None, {})
    b = head.begin_run("b", "tcp", 443, {})
    for i in range(200):
        gui.probe(a, T0 + i, i, 1, ROUTER, 1.0, 0)
        head.probe(b, T0 + i, i, 1, ROUTER, 2.0, 0)
    gui.close()
    head.close()
    r = Reader(tmp_path)
    assert {row["target"]: row["program"] for row in r.runs()} == {"a": "gui", "b": "headless"}
    assert len(r.probes("a", T0, T0 + 300)) == len(r.probes("b", T0, T0 + 300)) == 200


def test_recording_keeps_up_with_a_busy_monitor(tmp_path):
    """Ten targets of 15 hops at 1 s is 150 probes a second; a minute of
    that must be written in well under a minute."""
    rec = _rec(tmp_path)
    runs = [rec.begin_run(f"t{i}", "icmp", None, {}) for i in range(10)]
    start = time.perf_counter()
    for second in range(60):
        for run in runs:
            for ttl in range(1, 16):
                rec.probe(run, T0 + second, second, ttl, f"198.51.100.{ttl}", 5.0, 0)
    assert rec.flush(timeout=60)
    elapsed = time.perf_counter() - start
    rec.close()
    assert elapsed < 15, f"9,000 probes took {elapsed:.1f} s"
