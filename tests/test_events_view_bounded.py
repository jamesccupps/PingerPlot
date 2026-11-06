"""The Events view keeps as many rows as the monitor keeps events: an ECMP
hop alternating each round used to add a route row per round, forever."""
from pingerplot import gui
from pingerplot.model import Event
from test_background import FakeMonitor, _add


def test_the_events_view_is_bounded(app):
    mon = _add(app, "192.0.2.1", FakeMonitor())
    app._active = "192.0.2.1"
    app._last_event_seq = -1
    mon._events = [Event(i, 0.0, "route", f"Hop 3: route change {i}") for i in range(1500)]
    app._update_events()
    rows = app.events_tree.get_children()
    assert len(rows) == gui.MAX_EVENT_ROWS
    assert app.events_tree.item(rows[-1], "values")[2] == "Hop 3: route change 1499"
    assert app.events_tree.item(rows[0], "values")[2] == "Hop 3: route change 500"
