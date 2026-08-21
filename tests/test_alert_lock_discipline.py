"""Alert logging must happen outside the monitor's lock.

_retire_alerts states the rule in its own docstring: "The lock is released
between the pop and the log, because _log_event beeps and fires the webhook and
neither should happen holding it." _set_alert's clear branch did the opposite
-- it called _log_event inside `with self._lock`.

Not a deadlock: _lock is an RLock, and the work done (a deque append and a
thread start) does not block. The cost is future drift. _log_event beeps and
starts a webhook thread today; the next person to add something slower to it
will reasonably assume the documented rule holds everywhere, and it did not.

The webhook is the concrete reason to care -- it is an outbound HTTPS POST
started from inside the critical section of the object the UI thread reads
every 700 ms.
"""
import threading

import pytest

from pingerplot.monitor import Monitor


@pytest.fixture
def lock_watching_monitor(monkeypatch):
    """A monitor that records whether the lock was held at each _log_event."""
    m = Monitor()
    held = []
    real = m._log_event

    def _spy(kind, text):
        held.append((kind, m._lock._is_owned()))
        return real(kind, text)

    monkeypatch.setattr(m, "_log_event", _spy)
    m._held = held
    return m


def test_the_spy_can_actually_observe_a_held_lock(lock_watching_monitor):
    """Check the instrument before trusting it: _is_owned must report True
    when the lock really is held by this thread."""
    m = lock_watching_monitor
    with m._lock:
        m._log_event("info", "under the lock")
    assert m._held == [("info", True)]


def test_raising_an_alert_logs_outside_the_lock(lock_watching_monitor):
    m = lock_watching_monitor
    m._set_alert((1, "loss"), True, "hop 1 losing packets")
    assert m._held == [("alert", False)]


def test_clearing_an_alert_logs_outside_the_lock(lock_watching_monitor):
    """The branch that broke the rule."""
    m = lock_watching_monitor
    m._set_alert((1, "loss"), True, "hop 1 losing packets")
    m._held.clear()
    m._set_alert((1, "loss"), False, "")
    assert m._held == [("clear", False)]


def test_retiring_alerts_logs_outside_the_lock(lock_watching_monitor):
    """The path that already followed the rule, pinned so it stays that way."""
    m = lock_watching_monitor
    m._set_alert((1, "loss"), True, "a")
    m._set_alert((2, "lat"), True, "b")
    m._held.clear()
    m._retire_alerts(keep_ttl=None)
    assert m._held and not any(h for _k, h in m._held)


def test_no_webhook_thread_is_started_under_the_lock(monkeypatch):
    """The concrete consequence: an outbound HTTPS POST kicked off from inside
    the critical section that the UI thread reads every 700 ms."""
    m = Monitor()
    m.webhook_url = "https://example.invalid/hook"
    spawned = []
    real_thread = threading.Thread

    def _watch(*args, **kwargs):
        spawned.append(m._lock._is_owned())
        return real_thread(*args, **kwargs)

    monkeypatch.setattr("pingerplot.monitor.threading.Thread", _watch)
    m._set_alert((1, "loss"), True, "raise")
    m._set_alert((1, "loss"), False, "clear")
    assert spawned, "no webhook was attempted; the test proves nothing"
    assert not any(spawned), "a webhook thread was started holding the lock"


def test_the_behaviour_is_unchanged(lock_watching_monitor):
    """Restructuring four branches into three must not alter what fires."""
    m = lock_watching_monitor
    m._set_alert((1, "loss"), True, "first")          # raise -> alert
    m._set_alert((1, "loss"), True, "refreshed")      # refresh -> nothing
    m._set_alert((1, "loss"), False, "")              # clear -> clear
    m._set_alert((1, "loss"), False, "")              # absent -> nothing
    assert [k for k, _h in m._held] == ["alert", "clear"]
    # And the refresh really did replace the stored text.
    m._set_alert((2, "lat"), True, "one")
    m._set_alert((2, "lat"), True, "two")
    assert m._active_alerts[(2, "lat")] == "two"
