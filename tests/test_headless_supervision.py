"""Headless mode has to notice when a target dies, and bring it back.

Monitor._run sets running = False and returns on a name it cannot resolve, or
on a TCP/UDP traceroute refused for want of Administrator. The headless run
loop never looked at .running, so a target whose DNS failed at start-up stayed
dead for the life of the process — printing hops=0 forever with no indication
anything was wrong.

That is the normal case, not an exotic one: the documented way to run this is a
Task Scheduler job at logon or boot, which routinely starts before DNS is up.
An unattended monitor that silently monitors nothing is worse than one that
never started, because the CSV it is supposed to be filling stays empty and
nobody finds out until they go looking for the data.

No sockets or threads here — a stand-in Monitor drives the supervisor directly.
"""
import pytest

from pingerplot import headless


class FakeMonitor:
    """Enough Monitor surface for the supervisor: the two flags it reads, and
    a start() that records the call."""

    def __init__(self, starts_ok=True):
        self.running = True
        self.route_len = 1
        self.status = "Monitoring"
        self.starts = []
        self.shutdowns = 0
        self._starts_ok = starts_ok

    def die(self, status="Cannot resolve 'x': getaddrinfo failed"):
        self.running = False
        self.route_len = 0
        self.status = status

    def start(self, target, **kwargs):
        self.starts.append((target, kwargs))
        self.running = True
        self.route_len = 1 if self._starts_ok else 0
        if not self._starts_ok:
            self.running = False

    def summary(self):
        return (self.route_len, 0.0, 1.0, 0.0, True)

    def shutdown(self):
        self.shutdowns += 1


def _target(mon, name="8.8.8.8"):
    return headless.Target(name, mon, {"interval": 2.5})


def _quiet(*_a, **_k):
    pass


# --- the core behaviour ----------------------------------------------------

def test_a_healthy_target_is_left_alone():
    t = _target(FakeMonitor())
    headless.supervise([t], now=1000.0, log=_quiet)
    assert t.monitor.starts == []


def test_a_dead_target_is_restarted_after_the_delay():
    mon = FakeMonitor()
    t = _target(mon)
    mon.die()

    headless.supervise([t], now=1000.0, log=_quiet)
    assert mon.starts == [], "must not hammer a restart the instant it notices"

    headless.supervise([t], now=1000.0 + headless.RESTART_DELAY_S - 1, log=_quiet)
    assert mon.starts == []

    headless.supervise([t], now=1000.0 + headless.RESTART_DELAY_S, log=_quiet)
    assert len(mon.starts) == 1
    assert mon.starts[0][0] == "8.8.8.8"


def test_the_restart_reuses_the_configured_options():
    """A restart with default options would silently drop the log path, the
    alert thresholds and the probe mode from the config file."""
    mon = FakeMonitor()
    kwargs = {"interval": 1.0, "log_path": "logs/a.csv", "alert_loss_pct": 5.0,
              "packet_type": "tcp", "port": 8080}
    t = headless.Target("host", mon, kwargs)
    mon.die()
    headless.supervise([t], now=0.0, log=_quiet)
    headless.supervise([t], now=headless.RESTART_DELAY_S, log=_quiet)
    assert mon.starts[0][1] == kwargs


def test_a_repeatedly_failing_target_backs_off():
    """A permanently bad hostname must not resolve twice a minute forever."""
    mon = FakeMonitor(starts_ok=False)
    t = _target(mon)
    mon.die()

    now, delays = 0.0, []
    for _ in range(5):
        headless.supervise([t], now=now, log=_quiet)   # notices / schedules
        now = t.retry_at
        delays.append(t.retry_delay)
        headless.supervise([t], now=now, log=_quiet)   # performs the restart

    assert delays == sorted(delays), f"backoff went backwards: {delays}"
    assert delays[-1] > delays[0]
    assert max(delays) <= headless.RESTART_DELAY_MAX_S


def test_backoff_resets_once_the_target_is_actually_working():
    """A flaky link that recovers must not inherit a five-minute retry delay
    from the last outage."""
    mon = FakeMonitor()
    t = _target(mon)
    t.retry_delay = headless.RESTART_DELAY_MAX_S
    t.retry_at = 999.0
    headless.supervise([t], now=1.0, log=_quiet)
    assert t.retry_delay == headless.RESTART_DELAY_S
    assert t.retry_at == 0.0


def test_a_still_starting_target_is_not_treated_as_dead():
    """Between start() and the trace finishing, running is True but route_len
    is still 0. That is normal start-up, not a failure to restart."""
    mon = FakeMonitor()
    mon.route_len = 0
    t = _target(mon)
    headless.supervise([t], now=0.0, log=_quiet)
    headless.supervise([t], now=10_000.0, log=_quiet)
    assert mon.starts == []


def test_a_failing_restart_does_not_stop_the_other_targets():
    class Exploding(FakeMonitor):
        def start(self, target, **kwargs):
            raise OSError("network is down")

    bad, good = Exploding(), FakeMonitor()
    tb, tg = headless.Target("bad", bad, {}), headless.Target("good", good, {})
    bad.die()
    good.die()
    headless.supervise([tb, tg], now=0.0, log=_quiet)
    headless.supervise([tb, tg], now=headless.RESTART_DELAY_S, log=_quiet)
    assert len(good.starts) == 1


def test_restarts_are_reported():
    """Silent self-healing is its own problem: a target flapping every few
    minutes should be visible in the log the service writes."""
    lines = []
    mon = FakeMonitor()
    t = _target(mon)
    mon.die("Cannot resolve 'nope.invalid': getaddrinfo failed")
    headless.supervise([t], now=0.0, log=lines.append)
    headless.supervise([t], now=headless.RESTART_DELAY_S, log=lines.append)
    joined = "\n".join(lines)
    assert "8.8.8.8" in joined
    assert "getaddrinfo" in joined, "the reason it died has to survive to the log"
    assert t.restarts == 1


# --- the stop flag ---------------------------------------------------------

def test_the_stop_flag_is_per_run_not_process_wide():
    """_STOP was a module global set by the signal handler and never cleared,
    so a second main() in the same process returned immediately."""
    headless.request_stop()
    assert headless.should_stop() is True
    headless.reset_stop()
    assert headless.should_stop() is False


def test_status_line_covers_every_target():
    out = []
    mons = [FakeMonitor(), FakeMonitor()]
    targets = [headless.Target(f"h{i}", m, {}) for i, m in enumerate(mons)]
    targets[1].monitor.die("Cannot resolve")
    headless.print_status(targets, log=out.append)
    joined = "\n".join(out)
    assert "h0" in joined and "h1" in joined
    assert "stopped" in joined.lower(), "a dead target must be visibly dead"
