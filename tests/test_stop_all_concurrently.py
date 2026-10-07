"""Stopping many targets must not wait for them one at a time.

stop() clears `running` and joins the worker, and a worker in the middle of a
round finishes its in-flight probes first -- up to a reply timeout (3 s in TCP
mode). "Stop all", closing the window and Ctrl-C in headless all called
stop()/shutdown() on each monitor in turn, so the waits added up: ten targets
mid-round froze the GUI for up to ten timeouts. Signalling every monitor first
lets them wind down together, so the cost is the slowest one, not the sum.
"""
import time

import pytest

from pingerplot import headless
from pingerplot.monitor import Monitor

WIND_DOWN = 0.4    # an in-flight round finishing after stop is requested
N = 5


def _slow_run(self, gen):
    while self.running:
        time.sleep(0.01)
    time.sleep(WIND_DOWN)


@pytest.fixture
def busy_monitors(monkeypatch):
    monkeypatch.setattr(Monitor, "_run", _slow_run)
    mons = []
    for i in range(N):
        m = Monitor()
        m.start(f"192.0.2.{i + 1}", timeout_ms=1000)
        mons.append(m)
    yield mons
    for m in mons:
        m.shutdown()


def _timed(fn):
    t0 = time.monotonic()
    fn()
    return time.monotonic() - t0


def test_gui_stop_all_overlaps_the_waits(app, busy_monitors):
    for i, m in enumerate(busy_monitors):
        app._monitors[f"192.0.2.{i + 1}"] = m
    took = _timed(app._stop_all)
    assert not any(m.running for m in busy_monitors)
    assert took < WIND_DOWN * 2.5, f"{took:.2f}s: stopped one at a time ({N} x {WIND_DOWN}s)"


def test_headless_shutdown_overlaps_the_waits(busy_monitors):
    targets = [headless.Target(f"t{i}", m, {}) for i, m in enumerate(busy_monitors)]
    took = _timed(lambda: headless.shutdown_all(targets))
    assert took < WIND_DOWN * 2.5, f"{took:.2f}s: shut down one at a time"
    assert all(m._ping_pool is None for m in busy_monitors)
