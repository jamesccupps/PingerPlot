"""Headless mode must print the event log.

Every way the engine reports something worth knowing goes through the event
log: alert raised, alert cleared, route changed, "Probe logging DISABLED",
webhook delivery failed. The GUI shows it in the Events tab. Headless printed
only the periodic status table, so on the unattended box -- the one nobody is
watching, and the reason 1.3.2 moved the log-open failure into an event --
none of it was visible anywhere: not on the console, not in a redirected log.
"""
from pingerplot import headless
from pingerplot.monitor import Monitor


def _target(name="192.0.2.1"):
    m = Monitor()
    m.alert_sound = False
    return headless.Target(name, m, {})


def test_new_events_are_printed_once_each():
    t = _target()
    t.monitor._log_event("route", "Hop 3: path changed 192.0.2.5 -> 192.0.2.6")
    t.monitor._log_event("warn", "Probe logging DISABLED - cannot open x.csv")
    out = []
    headless.print_events([t], log=out.append)
    assert len(out) == 2
    assert "192.0.2.1" in out[0] and "ROUTE" in out[0] and "path changed" in out[0]
    assert "WARN" in out[1] and "DISABLED" in out[1]

    out.clear()
    headless.print_events([t], log=out.append)
    assert out == [], "an event was printed twice"
    t.monitor.shutdown()


def test_events_after_a_supervised_restart_are_not_lost():
    """start() clears the event log and restarts its numbering at 0, so a
    cursor left at the old run's last id would hide every new event."""
    t = _target()
    for i in range(5):
        t.monitor._log_event("info", f"old {i}")
    headless.print_events([t], log=lambda _l: None)

    t.monitor.start = lambda *a, **k: (t.monitor._events.clear(),
                                       setattr(t.monitor, "_event_seq", 0))
    t.retry_at = 1.0
    headless.supervise([t], now=2.0, log=lambda _l: None)   # performs the restart
    t.monitor._log_event("warn", "new run's first event")
    out = []
    headless.print_events([t], log=out.append)
    assert any("new run's first event" in line for line in out), out
    t.monitor.shutdown()


def test_an_unprintable_hostname_cannot_kill_the_service(monkeypatch):
    """A PTR record is whatever its operator says, and a Windows console is
    still cp1252/cp437: a UnicodeEncodeError from print() would end the run
    loop and with it every monitor."""
    import io
    import sys

    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp437"))
    t = _target()
    t.monitor._log_event("alert", "ALERT: Hop 4 (réseau-☃.example): 40% loss")
    headless.print_events([t])
    sys.stdout.flush()
    assert b"40% loss" in raw.getvalue()
    t.monitor.shutdown()
