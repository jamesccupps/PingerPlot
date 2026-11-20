"""The Map view: routers placed by name first, the trouble table under it,
and earlier routes from the history."""
import time
import tkinter as tk
from types import SimpleNamespace as V

from pingerplot import gui_charts, locating
from pingerplot.geoip import GeoInfo, GeoResolver
from pingerplot.gui_charts import Ink


def _views():
    return [
        V(ttl=1, address="192.168.1.1", hostname="gw.lan", loss_pct=0.0, avg=1.0),
        V(ttl=2, address="198.51.100.1", hostname="ae1.cr1.bos1.example.net", loss_pct=0.0, avg=8.0),
        V(ttl=3, address="198.51.100.2", hostname="ae2.cr2.chcgil09.example.net", loss_pct=6.0, avg=38.0),
        V(ttl=4, address="203.0.113.9", hostname="svc.example.com", loss_pct=6.0, avg=40.0),
    ]


def test_a_router_is_placed_by_its_name_before_the_ip_database():
    g = GeoResolver()
    g.cache["198.51.100.2"] = GeoInfo(39.7, -105.0, "Denver", "", "US", "")
    where = locating.Locator(g)
    w = where.where("198.51.100.2", "ae2.cr2.chcgil09.example.net")
    assert (w.label, w.source) == ("Chicago", "name") and round(w.point[0]) == 42
    w = where.where("198.51.100.2", "svc.example.com")
    assert (w.label, w.source) == ("Denver", "ip")
    assert where.where("198.51.100.7", None).point is None


def test_draw_map_counts_how_hops_were_placed_and_draws_earlier_routes(tk_root):
    c = tk.Canvas(tk_root, width=600, height=400)
    # The test root is never shown, so the canvas would report 1 x 1.
    c.winfo_width, c.winfo_height = (lambda: 600), (lambda: 400)
    ink = Ink({"grid": "#ccc", "line": "#0a0", "graph_bg": "#fff", "axis_text": "#888", "title": "#000",
               "fg_bad": "#c00", "fg_warn": "#a60", "fg_dest": "#060", "fg_ok": "#000"},
              lambda *a, **k: ("Segoe UI", 9), lambda v: int(v))
    past = [[(41.88, -87.63), (39.74, -104.99)]]
    caption = gui_charts.draw_map(c, ink, _views(), GeoResolver(), past=past)
    assert "2 by the router's name" in caption and "1 earlier route(s) dashed" in caption
    assert "left off" not in caption
    dashed = [i for i in c.find_all() if c.type(i) == "line" and c.itemcget(i, "dash")]
    assert dashed, "the earlier route wasn't drawn"
    c.destroy()


def test_the_trouble_table_in_the_window(app):
    app._fill_trouble(_views())
    rows = [app.trouble_tree.item(i, "values") for i in app.trouble_tree.get_children()]
    assert rows[0][0] == "Chicago, US" and rows[0][3] == "6%"
    assert "starts in Chicago, US (hop 3, 6%)" in app.trouble_label.cget("text")


def test_earlier_routes_come_from_the_history(app):
    now = time.time()
    rec = app.recorder
    run = rec.begin_run("svc.example.com", "icmp", None, {})
    rec.set_target_ip(run, "203.0.113.9")
    rec.route(run, now - 7200, [["198.51.100.1"], ["198.51.100.3"], ["203.0.113.9"]])
    rec.route(run, now - 3600, [["198.51.100.1"], ["198.51.100.2"], ["203.0.113.9"]])
    rec.name("198.51.100.1", "ae1.cr1.bos1.example.net")
    rec.name("198.51.100.3", "ae3.cr1.dfw01.example.net")
    rec.flush()
    app._active = "svc.example.com"
    app._past_routes.clear()
    app._past_route_points()                      # starts the load
    deadline = time.monotonic() + 10
    while "svc.example.com" not in app._past_routes and time.monotonic() < deadline:
        while not app._ui_jobs.empty():
            app._ui_jobs.get()()
        time.sleep(0.02)
    (route,) = app._past_routes["svc.example.com"][1]
    assert route[1] == (2, "198.51.100.3", "ae3.cr1.dfw01.example.net", None), "the earlier route, named"
    points = app._past_route_points()
    assert points == [[(42.36, -71.06), (32.78, -96.80)]], "Boston then Dallas, by their names"
    rec.end_run(run)


def _ink():
    return Ink({"grid": "#ccc", "line": "#0a0", "graph_bg": "#fff", "axis_text": "#888", "title": "#000",
                "fg_bad": "#c00", "fg_warn": "#a60", "fg_dest": "#060", "fg_ok": "#000"},
               lambda *a, **k: ("Segoe UI", 9), lambda v: int(v))


def test_the_map_leaves_off_an_answer_the_round_trip_rules_out(tk_root):
    from pingerplot.geoip import GeoInfo
    c = tk.Canvas(tk_root, width=600, height=400)
    c.winfo_width, c.winfo_height = (lambda: 600), (lambda: 400)
    g = GeoResolver()
    g.cache["198.51.100.9"] = GeoInfo(39.74, -104.99, "Denver", "", "US", "")
    views = [V(ttl=2, address="198.51.100.1", hostname="ae1.cr1.nyc1.example.net", best_here=9.0,
               loss_pct=0.0, avg=9.0),
             V(ttl=3, address="198.51.100.9", hostname="", best_here=10.0, loss_pct=0.0, avg=10.0)]
    where = locating.Locator(g, locating.anchors([("ae1.cr1.nyc1.example.net", 9.0)]),
                             locating.path_of(views))
    caption = gui_charts.draw_map(c, _ink(), views, g, locator=where)
    # (documentation addresses don't count as public; real ones would read "1 of 2")
    assert caption.startswith("1 of ") and "1 left off" in caption
    assert len([i for i in c.find_all() if c.type(i) == "oval"]) == 1, "only New York is drawn"
    c.destroy()


class _Live:
    running = True

    def __init__(self, routers):
        self._routers = routers

    def routers(self):
        return list(self._routers)

    def hop_addresses(self):
        return []


def test_the_window_checks_each_target_against_every_live_targets_routers(app):
    """A path with no router placed by its name (an anycast resolver's, say)
    is still checked, against the routers other targets placed."""
    from pingerplot.geoip import GeoInfo
    app._monitors["other.example.com"] = _Live([("ae1.cr1.bos1.example.net", 3.0)])
    app._anchors_at = -1e9
    app.geo.cache["203.0.113.53"] = GeoInfo(-27.47, 153.03, "Brisbane", "", "Australia", "")
    try:
        views = [V(ttl=4, address="203.0.113.53", hostname="", best_here=3.0)]
        w = app._locator("dns.example.com", views=views).view(views[0])
        assert w.text == "" and "Brisbane is" in w.why and "Boston" in w.why
    finally:
        del app._monitors["other.example.com"]
        app.geo.cache.pop("203.0.113.53", None)
        app._anchors_at = -1e9


class _Running:
    running = True

    def __init__(self, views):
        self._views = views

    def snapshot(self):
        return self._views, "", None, ""

    def routers(self):
        return []

    def hop_addresses(self):
        return []


def test_the_map_says_when_other_targets_lose_packets_at_the_same_router(app):
    def path(dest):
        return [V(ttl=1, address="192.168.1.1", hostname="gw.lan", loss_pct=0.0, avg=1.0),
                V(ttl=2, address="198.51.100.2", hostname="ae1.cr1.bos1.example.net", loss_pct=8.0, avg=8.0),
                V(ttl=3, address=dest, hostname="", loss_pct=8.0, avg=20.0)]
    app._monitors["a.example.com"] = _Running(path("203.0.113.10"))
    app._monitors["b.example.com"] = _Running(path("203.0.113.20"))
    app._active, app._shared_at = "a.example.com", -1e9
    try:
        app._fill_trouble(path("203.0.113.10"))
        text = app.trouble_label.cget("text")
        assert text.startswith("Loss reaching the destination starts in Boston, US (hop 2, 8%).")
        assert "The same router (hop 2, 198.51.100.2) is where loss starts for 2 of your 2 targets" in text
    finally:
        for n in ("a.example.com", "b.example.com"):
            del app._monitors[n]
        app._shared_at = -1e9
