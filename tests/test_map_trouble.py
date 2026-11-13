"""The Map view: routers placed by name first, the trouble table under it,
and earlier routes from the history."""
import time
import tkinter as tk
from types import SimpleNamespace as V

from pingerplot import gui_charts, gui_hopinfo
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
    lat, lon, label, source = gui_hopinfo.locate("198.51.100.2", "ae2.cr2.chcgil09.example.net", g)
    assert (label, source) == ("Chicago", "name") and round(lat) == 42
    lat, lon, label, source = gui_hopinfo.locate("198.51.100.2", "svc.example.com", g)
    assert (label, source) == ("Denver", "ip")
    assert gui_hopinfo.locate("198.51.100.7", None, g) is None


def test_draw_map_counts_how_hops_were_placed_and_draws_earlier_routes(tk_root):
    c = tk.Canvas(tk_root, width=600, height=400)
    # The test root is never shown, so the canvas would report 1 x 1.
    c.winfo_width, c.winfo_height = (lambda: 600), (lambda: 400)
    ink = Ink({"grid": "#ccc", "line": "#0a0", "graph_bg": "#fff", "axis_text": "#888", "title": "#000",
               "fg_bad": "#c00", "fg_warn": "#a60", "fg_dest": "#060", "fg_ok": "#000"},
              lambda *a, **k: ("Segoe UI", 9), lambda v: int(v))
    past = [[(41.88, -87.63), (39.74, -104.99)]]
    caption = gui_charts.draw_map(c, ink, _views(), GeoResolver(), locate=gui_hopinfo.locate, past=past)
    assert "2 by the router's name" in caption and "1 earlier route(s) dashed" in caption
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
    assert route[1] == ("198.51.100.3", "ae3.cr1.dfw01.example.net"), "the earlier route, named"
    points = app._past_route_points()
    assert points == [[(42.36, -71.06), (32.78, -96.80)]], "Boston then Dallas, by their names"
    rec.end_run(run)
