"""The long-term record: everything a run measures, kept on disk.

Until 1.5 the only durable record was an opt-in CSV per target, set up by
typing a path, and everything else (the event list, which router answered at
which hop, the route a reroute replaced) lived in memory for about 25 minutes.
"What did the path do at 3 a.m. Tuesday?" had no answer.

This keeps one SQLite database (standard library ``sqlite3``) in the settings
folder, shared by every target and by the GUI and headless runner alike:

* ``probes``  -- every probe: when, which hop, which router answered, the RTT.
  The heaviest table (about 25 MB a day for a 15-hop target at 2.5 s), so it
  has the shortest retention.
* ``minutes`` -- one row per minute per hop per responding router: sent,
  received, min/avg/max RTT and jitter. Small enough to keep for a year, and
  enough to answer "which router, which minute" long after the probes are gone.
* ``events``  -- every event the run logged: alerts, clears, route changes,
  warnings, notes.
* ``routes``  -- each distinct path to a target (the routers known at every
  hop), and ``segments``: when a run was on which route. A route that comes
  back is recognised as the same one, so "it flips between these two paths"
  shows as two routes taking turns.
* ``runs``    -- what was monitored, how, and when.

Writes go through one background thread that batches them into transactions,
so a probe never waits on the disk. The database runs in WAL mode, so a crash
loses at most the last batch and readers (the History view) never block the
writer. Trouble (a damaged file, a full disk, a folder that can't be written)
is reported through :attr:`Recorder.problem` for the monitors to put in their
Events, never raised into the probing.
"""
from __future__ import annotations

import itertools
import json
import os
import queue
import sqlite3
import stat
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .routes import compatible

SCHEMA_VERSION = 1
DB_NAME = "history.sqlite3"

DEFAULTS = {
    "enabled": True,
    "probes": True, "probe_days": 7,
    "summaries": True, "summary_days": 365,
    "events": True, "event_days": 365,
    "routes": True,
    "max_mb": 1024,
}
# (key, lowest, highest): the Settings page's own limits.
_LIMITS = {"probe_days": (1, 365), "summary_days": (1, 3650), "event_days": (1, 3650),
           "max_mb": (50, 100_000)}

_BATCH = 2000             # items per transaction at most
_MAINTAIN_EVERY_S = 3600  # retention and the size cap, hourly (and at open)
NO_REPLY = 0              # addrs id for a probe nobody answered

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(
    id INTEGER PRIMARY KEY,
    target TEXT NOT NULL,
    target_ip TEXT,
    mode TEXT NOT NULL,
    port INTEGER,
    options TEXT NOT NULL,
    started REAL NOT NULL,
    ended REAL,
    program TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_target ON runs(target, started);
CREATE TABLE IF NOT EXISTS addrs(id INTEGER PRIMARY KEY, addr TEXT NOT NULL UNIQUE, name TEXT);
CREATE TABLE IF NOT EXISTS probes(
    run INTEGER NOT NULL,
    t REAL NOT NULL,
    round INTEGER NOT NULL,
    ttl INTEGER NOT NULL,
    addr INTEGER NOT NULL,
    rtt REAL,
    status INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS probes_t ON probes(t);
CREATE TABLE IF NOT EXISTS minutes(
    run INTEGER NOT NULL,
    minute INTEGER NOT NULL,
    ttl INTEGER NOT NULL,
    addr INTEGER NOT NULL,
    sent INTEGER NOT NULL,
    recv INTEGER NOT NULL,
    rtt_sum REAL NOT NULL,
    rtt_min REAL,
    rtt_max REAL,
    jit_sum REAL NOT NULL,
    jit_n INTEGER NOT NULL,
    UNIQUE(run, minute, ttl, addr)
);
CREATE INDEX IF NOT EXISTS minutes_minute ON minutes(minute);
CREATE TABLE IF NOT EXISTS events(
    run INTEGER,
    t REAL NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_t ON events(t);
CREATE TABLE IF NOT EXISTS routes(
    id INTEGER PRIMARY KEY,
    target TEXT NOT NULL,
    dest TEXT,
    hops TEXT NOT NULL,
    first REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS routes_target ON routes(target);
CREATE TABLE IF NOT EXISTS segments(
    run INTEGER NOT NULL,
    route INTEGER NOT NULL,
    start REAL NOT NULL,
    end REAL
);
CREATE INDEX IF NOT EXISTS segments_start ON segments(start);
"""


def normalize(raw) -> dict:
    """Recording options from settings.json, every value present and in range.

    A hand-edited or older settings file must not be able to stop the app or
    turn retention into "delete everything": anything unusable falls back to
    its default."""
    out = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return out
    for key, default in DEFAULTS.items():
        value = raw.get(key, default)
        if isinstance(default, bool):
            out[key] = value if isinstance(value, bool) else default
            continue
        try:
            value = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        lo, hi = _LIMITS[key]
        out[key] = max(lo, min(value, hi))
    return out


def default_dir() -> Path:
    from . import settings
    return settings.config_dir() / "history"


def _is_link(path: Path) -> bool:
    """A symlink, or on Windows any reparse point (a junction too, which
    os.path.islink misses before Python 3.12)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def disk_bytes(folder: Optional[os.PathLike] = None) -> int:
    """What the history takes on disk, its write-ahead log included."""
    base = (Path(folder) if folder is not None else default_dir()) / DB_NAME
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.stat(str(base) + suffix).st_size
        except OSError:
            pass
    return total


def open_recorder(saved: dict, program: str) -> Optional["Recorder"]:
    """The recorder the GUI or headless runner should use, from its saved
    settings' ``recording`` section, or None when recording is turned off."""
    options = normalize(saved.get("recording") if isinstance(saved, dict) else None)
    if not options["enabled"]:
        return None
    return Recorder(options=options, program=program).start()


class _Minute:
    """Accumulates one (run, minute, ttl, responder) row."""
    __slots__ = ("sent", "recv", "rtt_sum", "rtt_min", "rtt_max", "jit_sum", "jit_n")

    def __init__(self) -> None:
        self.sent = self.recv = self.jit_n = 0
        self.rtt_sum = self.jit_sum = 0.0
        self.rtt_min: Optional[float] = None
        self.rtt_max: Optional[float] = None


class Recorder:
    """Owns the database and the one thread that writes to it.

    Every public method is safe to call from any thread and returns at once.
    If the database cannot be opened the recorder stays usable and records
    nothing; :attr:`problem` says why.
    """

    def __init__(self, folder: Optional[os.PathLike] = None, options: Optional[dict] = None,
                 program: str = "gui", clock=time.time) -> None:
        self.folder = Path(folder) if folder is not None else default_dir()
        self.path = self.folder / DB_NAME
        self.options = normalize(options)
        self.program = program
        self._clock = clock
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.problem = ""          # the latest trouble, for the monitors' Events
        self.problem_kind = "warn"  # its event kind ("info" for a recovery)
        self.problem_seq = 0       # bumped with each new problem
        self._closed = False
        # A run's handle is issued here, not by the database: waiting for a
        # row id would block the monitor whenever the writer is busy (an
        # hourly clean-up of a large file), and a run that gave up waiting
        # would go unrecorded without a word. The writer maps handles to ids.
        self._handles = itertools.count(1)

    # --- life cycle ---------------------------------------------------------
    def start(self) -> "Recorder":
        if self._thread is None:
            self._thread = threading.Thread(target=self._writer, name="history", daemon=True)
            self._thread.start()
        return self

    def close(self, timeout: float = 10.0) -> None:
        """Write everything queued, the open minutes included, then stop."""
        if self._thread is None or self._closed:
            return
        self._closed = True
        self._q.put(("close",))
        self._thread.join(timeout)

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait until everything queued so far is on disk (for tests and for
        reads that must see the latest data)."""
        if self._thread is None or self._closed:
            return False
        done = threading.Event()
        self._q.put(("flush", done))
        return done.wait(timeout)

    def _report(self, text: str, kind: str = "warn") -> None:
        with self._lock:
            self.problem = text
            self.problem_kind = kind
            self.problem_seq += 1

    # --- what monitors call -------------------------------------------------
    def set_options(self, options) -> None:
        """New recording options from the Settings page; they apply from the
        next write (and retention from the next clean-up)."""
        self.options = normalize(options)

    def begin_run(self, target: str, mode: str, port: Optional[int],
                  options: dict) -> Optional[int]:
        """Register a run and return its handle for the calls below, or None
        when nothing is being recorded."""
        if self._thread is None or self._closed:
            return None
        run = next(self._handles)
        self._q.put(("begin", run, target, mode, port, options, self._clock()))
        return run

    def set_target_ip(self, run: Optional[int], ip: Optional[str]) -> None:
        if run is not None:
            self._q.put(("target_ip", run, ip))

    def probe(self, run: Optional[int], t: float, round_no: int, ttl: int,
              addr: Optional[str], rtt: Optional[float], status: int) -> None:
        if run is not None:
            self._q.put(("probe", run, t, round_no, ttl, addr, rtt, status))

    def event(self, run: Optional[int], t: float, kind: str, text: str) -> None:
        if self.options["events"] and not self._closed:
            self._q.put(("event", run, t, kind, text))

    def route(self, run: Optional[int], t: float, hops: List[List[str]]) -> None:
        """The run is on this route from ``t`` (each hop's known routers)."""
        if run is not None and self.options["routes"]:
            self._q.put(("route", run, t, [list(h) for h in hops]))

    def clear(self) -> None:
        """Delete everything recorded (the file stays, empty). Runs in
        progress carry on into the emptied history."""
        if self._thread is not None and not self._closed:
            self._q.put(("clear",))

    def name(self, addr: Optional[str], name: Optional[str]) -> None:
        """A router's DNS name ("" for none), so the history can say what
        and where it was long after the run."""
        if addr and name is not None and self._thread is not None and not self._closed:
            self._q.put(("name", None, addr, name))

    def end_run(self, run: Optional[int]) -> None:
        if run is not None:
            self._q.put(("end", run, self._clock()))

    # --- the writer thread --------------------------------------------------
    def _open(self) -> Optional[sqlite3.Connection]:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._report(f"History recording is off: can't create {self.folder} ({exc})")
            return None
        # Never write through a link. Run as administrator (which full TCP/UDP
        # traceroute needs), PingerPlot would otherwise create and rewrite
        # files wherever a link that any program running as you can plant
        # here points: the reason the elevated app ignores a saved log path.
        for path in (self.folder, self.path):
            if _is_link(path):
                self._report(f"History recording is off: {path} is a link or junction, "
                             f"and PingerPlot won't write through one")
                return None
        for attempt in (1, 2):
            try:
                db = sqlite3.connect(self.path, timeout=10, isolation_level=None,
                                     check_same_thread=False)
                # No integrity check here: on a 1 GB file it takes seconds. A
                # file that isn't a database fails on the first statement
                # below; damage found later is handled where it's met.
                # auto_vacuum only takes effect on a database with no tables
                # yet, which is exactly when it's needed: retention deletes
                # then give pages back instead of the file only ever growing.
                # A database file is data, not code: no schema-defined
                # function with side effects may run on our behalf.
                db.execute("PRAGMA trusted_schema = OFF")
                db.execute("PRAGMA auto_vacuum = INCREMENTAL")
                db.execute("PRAGMA journal_mode = WAL")
                db.execute("PRAGMA synchronous = NORMAL")
                db.executescript(_SCHEMA)
                version = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                if version is None:
                    db.execute("INSERT INTO meta VALUES('schema', ?)", (str(SCHEMA_VERSION),))
                elif int(version[0]) > SCHEMA_VERSION:
                    db.close()
                    self._report(f"History recording is off: {self.path} was written by a newer "
                                 f"PingerPlot (schema {version[0]})")
                    return None
                db.execute("INSERT OR IGNORE INTO addrs(id, addr) VALUES(?, '')", (NO_REPLY,))
                # Router names came after the first files were made: add the
                # column to a file that doesn't have it.
                cols = {row[1] for row in db.execute("PRAGMA table_info(addrs)")}
                if "name" not in cols:
                    db.execute("ALTER TABLE addrs ADD COLUMN name TEXT")
                return db
            except sqlite3.DatabaseError as exc:
                try:
                    db.close()
                except Exception:
                    pass
                if attempt == 2 or not self.path.exists():
                    self._report(f"History recording is off: can't open {self.path} ({exc})")
                    return None
                # Damaged: keep it for whoever wants to recover it, start afresh.
                aside = self._set_aside()
                self._report(f"The history database was damaged ({exc}). It was kept as "
                             f"{aside.name} and a new one started.")
        return None

    def _set_aside(self) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        aside = self.path.with_name(f"{DB_NAME}.damaged-{stamp}")
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(self.path) + suffix)
            if src.exists():
                try:
                    os.replace(src, Path(str(aside) + suffix))
                except OSError:
                    pass
        return aside

    def _writer(self) -> None:
        db = self._open()
        addr_ids: Dict[str, int] = {"": NO_REPLY}
        run_ids: Dict[int, int] = {}      # handle -> runs.id
        open_seg: Dict[int, Tuple[int, int]] = {}   # runs.id -> (segment rowid, route id)
        minutes: Dict[Tuple[int, int, int, int], _Minute] = {}
        last_rtt: Dict[Tuple[int, int], float] = {}
        next_maint = 0.0
        write_failing = False

        def addr_id(addr: Optional[str]) -> int:
            if not addr:
                return NO_REPLY
            aid = addr_ids.get(addr)
            if aid is None:
                db.execute("INSERT OR IGNORE INTO addrs(addr) VALUES(?)", (addr,))
                aid = db.execute("SELECT id FROM addrs WHERE addr=?", (addr,)).fetchone()[0]
                addr_ids[addr] = aid
            return aid

        def flush_minutes(before: Optional[int] = None, run: Optional[int] = None) -> None:
            done = [k for k in minutes
                    if (before is None or k[1] < before) and (run is None or k[0] == run)]
            rows = []
            for k in done:
                m = minutes.pop(k)
                rows.append((*k, m.sent, m.recv, m.rtt_sum, m.rtt_min, m.rtt_max, m.jit_sum, m.jit_n))
            if rows and self.options["summaries"]:
                # Upsert: a minute flushed at end_run can be continued by a
                # late probe of the same minute; add rather than overwrite.
                db.executemany(
                    "INSERT INTO minutes VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(run, minute, ttl, addr) DO UPDATE SET "
                    "sent=sent+excluded.sent, recv=recv+excluded.recv, "
                    "rtt_sum=rtt_sum+excluded.rtt_sum, "
                    "rtt_min=min(coalesce(rtt_min, excluded.rtt_min), coalesce(excluded.rtt_min, rtt_min)), "
                    "rtt_max=max(coalesce(rtt_max, excluded.rtt_max), coalesce(excluded.rtt_max, rtt_max)), "
                    "jit_sum=jit_sum+excluded.jit_sum, jit_n=jit_n+excluded.jit_n", rows)

        def apply(item: tuple) -> None:
            op = item[0]
            if op == "begin":
                _, handle, target, mode, port, options, t = item
                cur = db.execute(
                    "INSERT INTO runs(target, mode, port, options, started, program) "
                    "VALUES(?,?,?,?,?,?)",
                    (target, mode, port, json.dumps(options, sort_keys=True, default=str),
                     t, self.program))
                run_ids[handle] = cur.lastrowid
                return
            if item[1] is not None and item[1] not in run_ids:
                return       # its run was never written (recording was failing)
            run = run_ids.get(item[1])
            if op == "probe":
                _, _, t, round_no, ttl, addr, rtt, status = item
                aid = addr_id(addr)
                if self.options["probes"]:
                    db.execute("INSERT INTO probes VALUES(?,?,?,?,?,?,?)",
                               (run, t, round_no, ttl, aid, rtt, status))
                minute = int(t // 60)
                m = minutes.get((run, minute, ttl, aid))
                if m is None:
                    m = minutes[(run, minute, ttl, aid)] = _Minute()
                m.sent += 1
                if rtt is not None:
                    m.recv += 1
                    m.rtt_sum += rtt
                    m.rtt_min = rtt if m.rtt_min is None else min(m.rtt_min, rtt)
                    m.rtt_max = rtt if m.rtt_max is None else max(m.rtt_max, rtt)
                    prev = last_rtt.get((run, ttl))
                    if prev is not None:
                        m.jit_sum += abs(rtt - prev)
                        m.jit_n += 1
                    last_rtt[(run, ttl)] = rtt
            elif op == "event":
                _, _, t, kind, text = item
                db.execute("INSERT INTO events VALUES(?,?,?,?)", (run, t, kind, text))
            elif op == "route":
                _, _, t, hops = item
                target, dest = db.execute("SELECT target, target_ip FROM runs WHERE id=?",
                                          (run,)).fetchone()
                route_id = None
                for rid, stored in db.execute(
                        "SELECT id, hops FROM routes WHERE target=? AND dest IS ? ORDER BY id",
                        (target, dest)).fetchall():
                    old = json.loads(stored)
                    if compatible(old, hops):
                        route_id = rid
                        merged = [sorted(set(a) | set(b)) for a, b in zip(old, hops)]
                        if merged != old:    # more load balancing seen: widen it
                            db.execute("UPDATE routes SET hops=? WHERE id=?",
                                       (json.dumps(merged), rid))
                        break
                if route_id is None:
                    route_id = db.execute(
                        "INSERT INTO routes(target, dest, hops, first) VALUES(?,?,?,?)",
                        (target, dest, json.dumps(hops), t)).lastrowid
                current = open_seg.get(run)
                if current is None or current[1] != route_id:
                    if current is not None:
                        db.execute("UPDATE segments SET end=? WHERE rowid=?", (t, current[0]))
                    seg = db.execute("INSERT INTO segments(run, route, start) VALUES(?,?,?)",
                                     (run, route_id, t)).lastrowid
                    open_seg[run] = (seg, route_id)
            elif op == "name":
                aid = addr_id(item[2])
                db.execute("UPDATE addrs SET name=? WHERE id=?", (item[3], aid))
            elif op == "target_ip":
                db.execute("UPDATE runs SET target_ip=? WHERE id=?", (item[2], run))
            elif op == "end":
                seg = open_seg.pop(run, None)
                if seg is not None:
                    db.execute("UPDATE segments SET end=? WHERE rowid=?", (item[2], seg[0]))
                flush_minutes(run=run)
                for key in [k for k in last_rtt if k[0] == run]:
                    del last_rtt[key]
                del run_ids[item[1]]
                db.execute("UPDATE runs SET ended=? WHERE id=?", (item[2], run))

        def release(items: Iterable[tuple]) -> None:
            # Nobody may be left waiting on a write that won't happen.
            for item in items:
                if item[0] == "flush":
                    item[1].set()

        closing = False
        while not closing:
            try:
                batch = [self._q.get(timeout=1.0)]
            except queue.Empty:
                batch = []
            while len(batch) < _BATCH:
                try:
                    batch.append(self._q.get_nowait())
                except queue.Empty:
                    break
            if db is None:
                release(batch)
                closing = any(item[0] == "close" for item in batch)
                continue
            waiting = []
            try:
                db.execute("BEGIN")
                for item in batch:
                    if item[0] == "close":
                        closing = True
                    elif item[0] == "clear":
                        # Everything goes except what keeps runs in progress
                        # recording: their run rows and the route each is on.
                        active = tuple(run_ids.values()) or (-1,)
                        marks = ",".join("?" * len(active))
                        for table in ("probes", "minutes", "events"):
                            db.execute(f"DELETE FROM {table}")
                        db.execute(f"DELETE FROM segments WHERE end IS NOT NULL "
                                   f"OR run NOT IN ({marks})", active)
                        db.execute("DELETE FROM routes WHERE id NOT IN (SELECT route FROM segments)")
                        db.execute(f"DELETE FROM runs WHERE id NOT IN ({marks})", active)
                        minutes.clear()
                        last_rtt.clear()
                    elif item[0] == "flush":
                        waiting.append(item[1])
                    else:
                        apply(item)
                # Minutes that have ended (by the clock, so a quiet target's
                # last minute isn't held forever) go to disk with the batch.
                flush_minutes(before=int(self._clock() // 60), run=None)
                if closing:
                    flush_minutes()
                db.execute("COMMIT")
                if write_failing:
                    write_failing = False
                    self._report("History recording has recovered.", kind="info")
            except sqlite3.Error as exc:
                try:
                    db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                release(batch)
                # Ids handed out inside the rolled-back transaction no longer
                # exist; nothing later may point at them.
                addr_ids.clear()
                addr_ids[""] = NO_REPLY
                open_seg.clear()
                try:
                    for handle in [h for h, rid in run_ids.items() if not db.execute(
                            "SELECT 1 FROM runs WHERE id=?", (rid,)).fetchone()]:
                        del run_ids[handle]
                except sqlite3.Error:
                    run_ids.clear()
                if type(exc) is sqlite3.DatabaseError:
                    # The file itself is damaged ("malformed", "not a
                    # database"): every batch would fail from now on. Keep it
                    # aside for recovery and carry on in a new one.
                    try:
                        db.close()
                    except sqlite3.Error:
                        pass
                    aside = self._set_aside()
                    self._report(f"The history database was damaged ({exc}). It was kept as "
                                 f"{aside.name} and a new one started.")
                    db = self._open()
                    run_ids.clear()
                    minutes.clear()
                    last_rtt.clear()
                    write_failing = False
                    continue
                if not write_failing:
                    write_failing = True
                    self._report(f"History recording failed ({exc}); what was measured in "
                                 f"the meantime isn't recorded. Retrying.")
            for done in waiting:
                done.set()
            now = time.monotonic()
            if now >= next_maint:
                next_maint = now + _MAINTAIN_EVERY_S
                self._maintain(db)
        if db is not None:
            try:
                db.close()
            except sqlite3.Error:
                pass

    # --- retention and the size cap ----------------------------------------
    def _maintain(self, db: sqlite3.Connection) -> None:
        o = self.options
        now = self._clock()
        try:
            db.execute("BEGIN")
            db.execute("DELETE FROM probes WHERE t < ?", (now - o["probe_days"] * 86400,))
            db.execute("DELETE FROM minutes WHERE minute < ?",
                       (int((now - o["summary_days"] * 86400) // 60),))
            db.execute("DELETE FROM events WHERE t < ?", (now - o["event_days"] * 86400,))
            # Route history is tiny; it lives as long as the events about it.
            db.execute("DELETE FROM segments WHERE end IS NOT NULL AND end < ?",
                       (now - o["event_days"] * 86400,))
            db.execute("DELETE FROM routes WHERE id NOT IN (SELECT route FROM segments)")
            keep = max(o["probe_days"], o["summary_days"], o["event_days"]) * 86400
            db.execute("DELETE FROM runs WHERE ended IS NOT NULL AND ended < ?", (now - keep,))
            db.execute("COMMIT")
            self._cap(db, o["max_mb"] * 1024 * 1024)
            db.execute("PRAGMA incremental_vacuum")
        except sqlite3.Error as exc:
            try:
                db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            self._report(f"History clean-up failed ({exc}); it will try again in an hour.")

    @staticmethod
    def used_bytes(db: sqlite3.Connection) -> int:
        pages = db.execute("PRAGMA page_count").fetchone()[0]
        free = db.execute("PRAGMA freelist_count").fetchone()[0]
        size = db.execute("PRAGMA page_size").fetchone()[0]
        return (pages - free) * size

    def _cap(self, db: sqlite3.Connection, limit: int) -> None:
        """Over the size limit, give up the oldest detail first: probes, then
        minutes, then events, a tenth of the oldest at a time."""
        for table, col in (("probes", "t"), ("minutes", "minute"), ("events", "t")):
            for _ in range(20):
                if self.used_bytes(db) <= limit:
                    return
                n = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                if n == 0:
                    break
                cut = db.execute(f"SELECT {col} FROM {table} ORDER BY {col} LIMIT 1 OFFSET ?",
                                 (max(1, n // 10),)).fetchone()
                if cut is None:
                    break
                db.execute("BEGIN")
                db.execute(f"DELETE FROM {table} WHERE {col} < ?", (cut[0],))
                db.execute("COMMIT")
                db.execute("PRAGMA incremental_vacuum")


# --- reading ----------------------------------------------------------------
class Reader:
    """Read-only queries for the History view, reports and exports.

    Opens its own connection (WAL lets it read while the recorder writes) and
    never creates the database: a missing file reads as empty."""

    def __init__(self, folder: Optional[os.PathLike] = None) -> None:
        self.path = (Path(folder) if folder is not None else default_dir()) / DB_NAME
        self._db: Optional[sqlite3.Connection] = None

    def _conn(self) -> Optional[sqlite3.Connection]:
        if self._db is None:
            if not self.path.exists():
                return None
            # Not mode=ro: a read-only connection to a WAL database fails when
            # no writer has left its -shm file behind. query_only refuses writes.
            self._db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            self._db.execute("PRAGMA query_only = ON")
            self._db.row_factory = sqlite3.Row
        return self._db

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def _rows(self, sql: str, args=()) -> List[sqlite3.Row]:
        db = self._conn()
        return [] if db is None else db.execute(sql, args).fetchall()

    def targets(self) -> List[sqlite3.Row]:
        """Every target ever recorded, most recently monitored first."""
        return self._rows("SELECT target, count(*) AS runs, min(started) AS first, "
                          "max(coalesce(ended, started)) AS last FROM runs "
                          "GROUP BY target ORDER BY last DESC")

    def runs(self, target: Optional[str] = None) -> List[sqlite3.Row]:
        if target is None:
            return self._rows("SELECT * FROM runs ORDER BY started")
        return self._rows("SELECT * FROM runs WHERE target=? ORDER BY started", (target,))

    def events(self, since: float = 0.0, until: float = float("inf"),
               target: Optional[str] = None, kinds: Optional[Iterable[str]] = None,
               text: str = "", limit: int = 100_000) -> List[sqlite3.Row]:
        sql = ("SELECT e.t, e.kind, e.text, r.target FROM events e LEFT JOIN runs r ON r.id = e.run "
               "WHERE e.t >= ? AND e.t <= ?")
        args: list = [since, until if until != float("inf") else 1e18]
        if target is not None:
            sql += " AND r.target = ?"
            args.append(target)
        kinds = list(kinds or [])
        if kinds:
            sql += f" AND e.kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        if text:
            sql += " AND e.text LIKE ? ESCAPE '\\'"
            args.append("%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        sql += " ORDER BY e.t LIMIT ?"
        args.append(limit)
        return self._rows(sql, args)

    def probes(self, target: str, since: float, until: float) -> List[sqlite3.Row]:
        return self._rows(
            "SELECT p.t, p.round, p.ttl, a.addr, p.rtt, p.status FROM probes p "
            "JOIN runs r ON r.id = p.run JOIN addrs a ON a.id = p.addr "
            "WHERE p.t >= ? AND p.t <= ? AND r.target = ? ORDER BY p.t, p.ttl",
            (since, until, target))

    def routes(self, target: str) -> List[sqlite3.Row]:
        """Every route recorded to ``target`` (``hops`` is JSON: each hop's
        routers), oldest first."""
        return self._rows("SELECT id, dest, hops, first FROM routes WHERE target=? ORDER BY id",
                          (target,))

    def segments(self, target: str, since: float = 0.0,
                 until: float = 1e18) -> List[sqlite3.Row]:
        """When the target was on which route: start, end (None = still on
        it, or the program stopped without closing it), route id."""
        return self._rows(
            "SELECT s.start, s.end, s.route, r.target_ip AS dest FROM segments s "
            "JOIN runs r ON r.id = s.run WHERE r.target = ? AND s.start <= ? "
            "AND coalesce(s.end, 1e18) >= ? ORDER BY s.start", (target, until, since))

    def names(self, addrs: Iterable[str]) -> Dict[str, str]:
        """The DNS names recorded for these routers (those that have one)."""
        addrs = [a for a in set(addrs) if a]
        out: Dict[str, str] = {}
        for i in range(0, len(addrs), 500):
            chunk = addrs[i:i + 500]
            for row in self._rows(f"SELECT addr, name FROM addrs WHERE name IS NOT NULL AND name != '' "
                                  f"AND addr IN ({','.join('?' * len(chunk))})", chunk):
                out[row["addr"]] = row["name"]
        return out

    def minutes(self, target: str, since: float, until: float) -> List[sqlite3.Row]:
        return self._rows(
            "SELECT m.minute, m.ttl, a.addr, m.sent, m.recv, m.rtt_sum, m.rtt_min, m.rtt_max, "
            "m.jit_sum, m.jit_n FROM minutes m JOIN runs r ON r.id = m.run "
            "JOIN addrs a ON a.id = m.addr "
            "WHERE m.minute >= ? AND m.minute <= ? AND r.target = ? ORDER BY m.minute, m.ttl",
            (int(since // 60), int(until // 60), target))
