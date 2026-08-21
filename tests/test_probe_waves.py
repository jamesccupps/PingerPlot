"""A monitoring round must probe the whole route in ONE wave.

The engine's central performance promise is that a round costs about one reply
timeout rather than the sum of them — that is the entire reason probes fan out
across a pool instead of running serially. The pool was fixed at 16 workers
while ``max_hops`` defaults to 30 and is allowed up to 64, so any route longer
than 16 hops quietly split into two or more waves, and a wave always costs a
full timeout because silent routers never answer at all.

Measured before the fix, with a 1 s reply timeout:

    16 hops -> 1.0 x timeout      30 hops -> 2.0 x timeout
    20 hops -> 2.0 x timeout      64 hops -> 4.0 x timeout

That is not just slow. With a 2 s timeout and the default 2.5 s interval, a
30-hop round takes 4 s; ``_interruptible_sleep`` clamps the negative remainder
to zero, so the configured interval is silently replaced by the round duration
with nothing shown in the UI.

These tests assert concurrency rather than elapsed time — a barrier that only
releases when every probe is in flight at once is deterministic, where a timing
threshold would be flaky on a loaded CI runner.
"""
import threading

import pytest

from pingerplot import icmp
from pingerplot.monitor import Monitor

BARRIER_TIMEOUT = 10  # generous: a passing run releases immediately


def _concurrent_gather(monitor: Monitor, route_len: int):
    """Run one round in which every probe blocks until all of them are running.

    If the pool cannot host ``route_len`` probes at once, the ones that did
    start wait out ``BARRIER_TIMEOUT`` and the barrier breaks, which surfaces as
    timed-out probes — exactly how the serialisation would show up in practice.
    """
    barrier = threading.Barrier(route_len, timeout=BARRIER_TIMEOUT)

    def probe(ttl, ip_ttl=None):
        barrier.wait()
        return icmp.PingResult(icmp.IP_SUCCESS, 1.0, "1.2.3.4", True)

    monitor._do_probe = probe
    return monitor._gather_range(1, route_len)


def _ready(max_hops: int) -> Monitor:
    m = Monitor()
    m.max_hops = max_hops
    m.target_ip = "1.2.3.4"
    m.running = True
    m._generation = 1
    m._ensure_pools()
    return m


@pytest.mark.parametrize("route_len", [8, 17, 30, 64])
def test_a_full_route_is_probed_in_one_wave(route_len):
    """17 is the first length the old 16-worker pool could not fit; 30 is the
    default max_hops and 64 the configurable ceiling."""
    m = _ready(route_len)
    try:
        results = _concurrent_gather(m, route_len)
    finally:
        m.shutdown()
    assert len(results) == route_len
    assert all(r.reached for r in results.values()), (
        "some probes did not run concurrently — the round split into waves"
    )


def test_pool_is_sized_to_max_hops_not_a_fixed_constant():
    m = _ready(48)
    try:
        assert m._ping_pool._max_workers >= 48
    finally:
        m.shutdown()


def test_pool_grows_when_a_later_start_raises_max_hops():
    """start() reuses an existing pool, so a Monitor first started with a short
    max_hops must not stay capped at that width for the rest of its life."""
    m = _ready(4)
    try:
        assert m._ping_pool._max_workers == 4
        m.max_hops = 40
        m._ensure_pools()
        assert m._ping_pool._max_workers >= 40
        results = _concurrent_gather(m, 40)
        assert all(r.reached for r in results.values())
    finally:
        m.shutdown()


def test_pool_is_not_rebuilt_when_max_hops_shrinks():
    """Shrinking is harmless — an oversized pool costs nothing, because
    ThreadPoolExecutor creates its threads lazily as work arrives. Rebuilding
    on every start() would throw away warm threads for no gain."""
    m = _ready(40)
    try:
        pool = m._ping_pool
        m.max_hops = 4
        m._ensure_pools()
        assert m._ping_pool is pool
    finally:
        m.shutdown()


def test_short_route_does_not_spin_up_the_whole_pool():
    """The pool is sized to the ceiling but must only cost what it uses, or
    raising max_hops would mean 64 idle threads per target."""
    m = _ready(64)
    try:
        before = threading.active_count()
        m._do_probe = lambda ttl, ip_ttl=None: icmp.PingResult(
            icmp.IP_SUCCESS, 1.0, "1.2.3.4", True)
        m._gather_range(1, 3)
        assert threading.active_count() - before <= 3
    finally:
        m.shutdown()
