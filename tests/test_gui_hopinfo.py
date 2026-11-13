"""Where a hop's router is and whose network: the table columns, the Details
window, and the lookups that feed them (faked: nothing goes to the network)."""
import json
import time

from pingerplot import gui_hopinfo, history
from pingerplot.geoip import GeoInfo, GeoResolver

PUBLIC = "8.8.8.8"
CHICAGO_NAME = "ae-5.r21.chcgil09.us.bb.example.net"


def _geo(**known):
    g = GeoResolver()
    for ip, info in known.items():
        g.cache[ip] = info
    return g


DENVER = GeoInfo(39.7, -105.0, "Denver", "Colorado", "United States", "Example ISP", 64500, "Example Transit")


def test_the_name_beats_the_ip_database_in_a_table():
    g = _geo(**{PUBLIC: DENVER})
    assert gui_hopinfo.location(PUBLIC, CHICAGO_NAME, g) == "Chicago, US"
    assert gui_hopinfo.location(PUBLIC, "dns.example.net", g) == "Denver, United States"
    assert gui_hopinfo.network(PUBLIC, g) == "AS64500 Example Transit"
    assert gui_hopinfo.location("10.0.0.1", None, g) == "" and gui_hopinfo.network(None, g) == ""


def test_details_show_both_witnesses_and_their_disagreement():
    rows = dict(gui_hopinfo.facts(PUBLIC, CHICAGO_NAME, _geo(**{PUBLIC: DENVER})))
    assert rows["Where, from its name"].startswith("Chicago, US")
    assert "chcgil09" in rows["Where, from its name"]
    assert rows["Where, from the IP database"] == "Denver, Colorado, United States"
    assert "disagree" in rows[""]
    assert rows["Network"] == "AS64500 Example Transit"


def test_details_for_a_router_not_yet_looked_up():
    rows = dict(gui_hopinfo.facts(PUBLIC, None, _geo()))
    assert rows["Name"] == "looking it up…"
    assert "not looked up" in rows["Where, from the IP database"]


# --- in the window ----------------------------------------------------------------

class _Mon:
    running = True

    def __init__(self, addrs):
        self._addrs = addrs

    def hop_addresses(self):
        return list(self._addrs)


def test_lookups_are_on_by_default_and_saved(app, tmp_path):
    assert app.lookup_var.get() is True
    app.lookup_var.set(False)
    app._save_settings()
    saved = json.loads(next(tmp_path.rglob("settings.json")).read_text(encoding="utf-8"))
    assert saved["lookup_hops"] is False


def test_running_targets_routers_are_queued_and_private_ones_never(app):
    app.geo.stop()
    app._monitors["svc.example.com"] = _Mon([PUBLIC, "10.0.0.1", "192.168.1.1"])
    app._geo_fed = 0.0
    app._feed_lookups()
    queued = []
    while not app.geo._q.empty():
        queued.append(app.geo._q.get_nowait())
    assert queued == [PUBLIC]
    del app._monitors["svc.example.com"]


def test_nothing_is_queued_with_lookups_off(app):
    app.lookup_var.set(False)
    app._monitors["svc.example.com"] = _Mon([PUBLIC])
    app._geo_fed = 0.0
    app._feed_lookups()
    assert app.geo._q.empty()
    del app._monitors["svc.example.com"]


def test_the_hop_table_has_location_and_network(app):
    cols = app.tree["columns"]
    assert "place" in cols and "network" in cols


def test_details_list_the_routers_seen_at_that_hop(app):
    now = time.time()
    rec = app.recorder
    run = rec.begin_run("svc.example.com", "icmp", None, {})
    for i, addr in enumerate(["198.51.100.1", "198.51.100.2"] * 30):
        rec.probe(run, now - 3600 + i, i, 3, addr, 5.0, 0)
    rec.end_run(run)
    rec.flush()
    app._active = "svc.example.com"
    win = gui_hopinfo.HopDetails(app, "svc.example.com", 3, "198.51.100.1", CHICAGO_NAME)
    try:
        trees = [w for w in win.winfo_children()[0].winfo_children() if w.winfo_class() == "Treeview"]
        (tree,) = trees
        routers = sorted(tree.item(i, "values")[0] for i in tree.get_children())
        assert routers == ["198.51.100.1", "198.51.100.2"]
    finally:
        win.destroy()
    assert history.Reader().runs("svc.example.com")
