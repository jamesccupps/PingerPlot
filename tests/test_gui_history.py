"""The History page: loads what was recorded, fills its tables, filters and
exports. Data goes into the test's own settings folder (gui_harness points
APPDATA there), through a real Recorder."""
import csv
import time

import pytest

from pingerplot import gui_history, history
from pingerplot.gui_history import HistoryPage, export, nice_max, route_colour

DEST, R1, R2 = "203.0.113.9", "198.51.100.1", "198.51.100.2"


def _record(now, folder=None):
    """Twenty minutes ending a minute before ``now``: route 1, then route 2
    (slower, lossy), with a reroute and an alert."""
    t0 = (int(now // 60) - 21) * 60.0
    clock = [t0]
    rec = history.Recorder(folder, clock=lambda: clock[0]).start()
    run = rec.begin_run("svc.example.com", "icmp", None, {})
    rec.set_target_ip(run, DEST)
    rec.route(run, t0, [[R1], [DEST]])
    for i in range(0, 1200, 2):
        t = t0 + i
        clock[0] = t
        if i == 600:
            rec.route(run, t, [[R2], [DEST]])
            rec.event(run, t, "route", f"Route changed at hop 1: {R1} -> {R2}")
            rec.event(run, t + 1, "alert", "Hop 2 (svc.example.com): 25% packet loss")
            rec.event(run, t + 2, "info", "=HYPERLINK(\"http://x\") a note")
        second = i >= 600
        rec.probe(run, t, i, 1, R2 if second else R1, 2.0, 0)
        lost = second and i % 8 == 0
        rec.probe(run, t, i, 2, None if lost else DEST, None if lost else (40.0 if second else 10.0),
                  11010 if lost else 0)
    clock[0] = now
    rec.end_run(run)
    rec.close()
    return t0


def _drain(app):
    """Run what the query thread posted (the fixture stops Tk's own loop)."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        while not app._ui_jobs.empty():
            app._ui_jobs.get()()
        if app.history_page is not None and app.history_page.data is not None:
            app.root.update()
            return
        time.sleep(0.02)
    raise AssertionError("the history never loaded")


@pytest.fixture
def page(app):
    now = time.time()
    _record(now)
    app._show_page("history", "svc.example.com")
    _drain(app)
    p = app.history_page
    p.range_var.set("Last hour")
    p.data = None
    _drain(app)
    return p


def test_the_page_shows_the_target_and_its_routes(page):
    assert page.target_var.get() == "svc.example.com"
    assert "svc.example.com" in page.summary.cget("text")
    assert "2 route(s)" in page.detail.cget("text") and "1 alert(s)" in page.detail.cget("text")
    rows = [page.routes_tree.item(i) for i in page.routes_tree.get_children()]
    assert [r["text"].strip() for r in rows] == ["1", "2"]
    good, bad = (r["values"] for r in rows)
    assert good[2] == "10 ms" and bad[2] == "40 ms"
    assert bad[3] != "0.0%", "the second route was the lossy one"


def test_hops_list_both_routers_at_hop_1(page):
    tree = page.hops_tree
    first = tree.get_children()[0]
    assert tree.item(first, "text") == "Hop 1"
    routers = [tree.item(c, "values")[0] for c in tree.get_children(first)]
    assert sorted(routers) == [R1, R2]


def test_events_filter_and_search(page):
    def shown():
        return [page.events_tree.item(i, "values")[1] for i in page.events_tree.get_children()]
    assert sorted(shown()) == ["alert", "info", "route"]
    page.kind_vars["alert"].set(False)
    page._fill_events()
    assert sorted(shown()) == ["info", "route"]
    page.search_var.set("changed")
    assert shown() == ["route"]


def test_earlier_steps_back_and_later_comes_forward(page, app):
    assert page.later_btn.instate(["disabled"]), "can't go later than now"
    page.data = None                         # wait for the new range, not the old one
    page._step(-1)
    _drain(app)
    assert page.until is not None
    assert not page.later_btn.instate(["disabled"])
    page._step(1)
    assert page.until is None


def test_picking_a_hop_graphs_it(page, app):
    first = page.hops_tree.get_children()[0]
    page.hops_tree.selection_set(first)
    page._pick_hop()
    assert page.hop == 1
    page.data = None
    _drain(app)
    assert all(p.avg == 2.0 for p in page.data["series"] if p.avg is not None)


def test_an_empty_history_says_so(app):
    app._show_page("history")
    _drain(app)
    assert app.history_page.summary.cget("text") == "Nothing recorded yet."


def test_export_writes_each_kind_and_defangs_formulas(tmp_path):
    folder = tmp_path / "h"
    now = time.time()
    t0 = _record(now, folder)
    out = tmp_path / "events.csv"
    assert export("events", "svc.example.com", t0, now, str(out), folder=folder) == 3
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert rows[0][0] == "# PingerPlot history" and rows[1][:3] == ["time", "iso_time", "kind"]
    assert any(r[3].startswith("'=HYPERLINK") for r in rows[2:]), "a formula must not run in Excel"
    n = export("minutes", "svc.example.com", t0, now, str(tmp_path / "m.csv"), folder=folder)
    assert n >= 20
    n = export("probes", "svc.example.com", t0, now, str(tmp_path / "p.csv"), folder=folder)
    assert n == 1200
    with pytest.raises(ValueError):
        export("everything", "svc.example.com", t0, now, str(tmp_path / "x.csv"), folder=folder)


@pytest.mark.parametrize("v, top", [(7, 8), (11, 12), (62, 80), (100, 100), (101, 120), (249, 250)])
def test_the_axis_top_is_a_round_number(v, top):
    assert nice_max(v) == pytest.approx(top)


def test_route_colours_are_stable_and_fold_to_grey():
    assert route_colour(0, False) == gui_history.ROUTE_COLOURS[False][0]
    assert route_colour(5, True) == gui_history.ROUTE_COLOURS[True][5]
    assert route_colour(6, False) == gui_history.OTHER_ROUTE[False]
    assert route_colour(-1, True) == gui_history.OTHER_ROUTE[True]


def test_the_history_tab_is_in_the_header(app):
    assert "history" in app.nav._labels
    assert isinstance(HistoryPage, type)
