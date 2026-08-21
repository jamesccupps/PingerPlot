"""print_comparison must not read a running monitor's hops directly.

Every other reader in the codebase goes through a lock-protected accessor --
snapshot(), summary(), all_samples(), samples_for(). This one line reached
straight into the private list:

    now = [_compare.stats_from_hop(h) for h in t.monitor._hops]

stats_from_hop calls hop.compute(), which iterates hop.samples, a deque.
Iterating a deque while another thread appends to it raises RuntimeError:
deque mutated during iteration.

run_report() stops waiting when the round count is reached but does NOT stop
the monitors, so that read genuinely races the probe thread. And the exception
is a RuntimeError, while main()'s guard is `except OSError` -- so it escaped:
the process died with a traceback after the report tables had been printed,
and --report-csv was never written. For the documented deployment (a scheduled
job branching on the exit status) that is a rare but real crash.

Note on how this is tested. The obvious regression test -- hammer the deques
from a background thread and call print_comparison in a loop -- was tried and
REJECTED: it took 30 seconds and still passed against the unfixed code,
because each iteration is dominated by parsing the baseline file and the
vulnerable window is tiny. A test that cannot fail is not a test. What is
asserted instead is the invariant itself, deterministically: the hop list may
only be iterated by a caller holding the lock.
"""
import threading

import pytest

from pingerplot import compare as _compare
from pingerplot.headless import Target, print_comparison
from pingerplot.model import Hop
from pingerplot.monitor import Monitor


class _GuardedHops(list):
    """A hop list that refuses to be read without the monitor's lock held.

    This is what makes the test deterministic. snapshot() iterates the same
    list under the lock and is fine; the old unlocked comprehension is not.
    """

    def __init__(self, items, lock):
        super().__init__(items)
        self._lock = lock
        self.unlocked_reads = 0

    def _check(self):
        if not self._lock._is_owned():
            self.unlocked_reads += 1
            raise RuntimeError("hop list read without the monitor lock held")

    def __iter__(self):
        self._check()
        return super().__iter__()

    def __getitem__(self, i):
        self._check()
        return super().__getitem__(i)


@pytest.fixture
def guarded_monitor():
    mon = Monitor()
    mon.target_input = mon.target_ip = "203.0.113.7"
    hops = []
    for ttl, addr in ((1, "203.0.113.1"), (2, "203.0.113.2"), (3, "203.0.113.7")):
        h = Hop(ttl)
        h.address = addr
        for i in range(30):
            h.record(1.0 + i * 0.01, addr, 0)
        hops.append(h)
    mon.route_len = 3
    mon._hops = _GuardedHops(hops, mon._lock)
    return mon


def _baseline(tmp_path):
    p = tmp_path / "baseline.json"
    p.write_text(
        '{"target_input": "203.0.113.7", "hops": ['
        '{"ttl": 1, "address": "203.0.113.1", "samples": [[0, 1.0], [1, 1.1]]},'
        '{"ttl": 2, "address": "203.0.113.2", "samples": [[0, 2.0], [1, 2.1]]}'
        ']}', encoding="utf-8")
    return str(p)


def test_the_guard_itself_works(guarded_monitor):
    """Prove the fixture can fail before trusting what it certifies."""
    with pytest.raises(RuntimeError):
        list(guarded_monitor._hops)
    with guarded_monitor._lock:
        assert len(list(guarded_monitor._hops)) == 3
    guarded_monitor._hops.unlocked_reads = 0


def test_comparison_never_reads_hops_without_the_lock(tmp_path, guarded_monitor):
    print_comparison([Target("203.0.113.7", guarded_monitor, {})],
                     _baseline(tmp_path), log=lambda *_a: None)
    assert guarded_monitor._hops.unlocked_reads == 0


def test_the_race_is_real(guarded_monitor):
    """The mechanism, in case the invariant above is ever weakened: iterating a
    hop's sample deque while another thread appends raises RuntimeError."""
    hop = Hop(1)
    for i in range(200):
        hop.record(1.0, "203.0.113.1", 0)
    stop = threading.Event()
    t = threading.Thread(
        target=lambda: [hop.record(1.0, "203.0.113.1", 0)
                        for _ in iter(lambda: not stop.is_set(), False)],
        daemon=True)
    t.start()
    try:
        hit = False
        for _ in range(20000):
            try:
                _compare.stats_from_hop(hop)
            except RuntimeError:
                hit = True
                break
        assert hit, "deque iteration is apparently safe now; revisit this fix"
    finally:
        stop.set()
        t.join(timeout=5)


def test_stats_from_views_matches_stats_from_hop(guarded_monitor):
    """The two constructions must agree, or the fix would change the reported
    numbers as well as making them safe."""
    views, _s, _ip, _in = guarded_monitor.snapshot()
    with guarded_monitor._lock:
        direct = [_compare.stats_from_hop(h) for h in guarded_monitor._hops]
    assert _compare.stats_from_views(views) == direct


def test_a_report_with_a_baseline_still_writes_its_csv(tmp_path, guarded_monitor):
    """What the crash actually cost: --report-csv came after the comparison, so
    the RuntimeError skipped it entirely."""
    from pingerplot import headless
    out = tmp_path / "report.csv"
    targets = [Target("203.0.113.7", guarded_monitor, {})]
    print_comparison(targets, _baseline(tmp_path), log=lambda *_a: None)
    headless.write_report_csv(str(out), targets)
    assert out.exists() and out.stat().st_size > 0
