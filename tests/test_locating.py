"""An IP-database location is checked against the round trips: a reply can't
come from farther than light in fiber travels in that time. The round trips
are ones measured on real US East Coast paths; the addresses are
documentation ones and the names fictional."""
import ast
from pathlib import Path

from pingerplot import locating
from pingerplot.geoip import GeoInfo, GeoResolver
from pingerplot.locating import Anchor, Locator
from pingerplot.model import Hop, HopView, hop_from_session

BOSTON = Anchor(42.36, -71.06, 4.0, "Boston", "cr1.bos1.example.net")
NEW_YORK = Anchor(40.71, -74.01, 9.0, "New York", "cr1.nyc1.example.net")
MIAMI = Anchor(25.76, -80.19, 41.0, "Miami", "cr1.mia1.example.net")

BRISBANE = (-27.47, 153.03)
SAN_JOSE = (37.34, -121.89)
DENVER = (39.74, -104.99)
REDMOND = (47.67, -122.12)
WASHINGTON = (38.91, -77.04)
AMSTERDAM = (52.37, 4.90)


def test_an_anycast_answer_registered_on_another_continent_is_ruled_out():
    # An anycast resolver answers in 3 ms; the database says Brisbane, where its block is registered.
    assert locating.ruled_out(BRISBANE, 3.0, [BOSTON]) == BOSTON


def test_registered_headquarters_are_ruled_out():
    assert locating.ruled_out(SAN_JOSE, 10.0, [NEW_YORK]) == NEW_YORK    # a public resolver
    assert locating.ruled_out(DENVER, 10.0, [NEW_YORK]) == NEW_YORK      # a carrier router
    assert locating.ruled_out(REDMOND, 11.0, [NEW_YORK]) == NEW_YORK     # a mail front end


def test_possible_answers_are_believed():
    # Washington is 330 km from New York: 10 ms allows it, so the database stands.
    assert locating.ruled_out(WASHINGTON, 10.0, [NEW_YORK]) is None
    # 90 ms allows Amsterdam from Miami at 41 ms: not provably wrong.
    assert locating.ruled_out(AMSTERDAM, 90.0, [MIAMI]) is None


def test_no_round_trip_or_no_anchor_means_no_verdict():
    assert locating.ruled_out(BRISBANE, None, [BOSTON]) is None
    assert locating.ruled_out(BRISBANE, float("nan"), [BOSTON]) is None
    assert locating.ruled_out(BRISBANE, 3.0, []) is None


def test_whole_millisecond_readings_count_a_millisecond_more():
    # Windows reports 9.9 ms as 9, "<1" as 0: each reading may be up to 1 ms
    # short, so two 0 ms readings still allow about 350 km.
    assert locating.reach_km(0.0, 0.0) == 350.0
    assert locating.ruled_out((38.95, -77.45), 0.0, [Anchor(38.91, -77.04, 0.0, "x", "x")]) is None
    # Ashburn to Philadelphia, about 230 km, both "0 ms": possible after the allowance.
    assert locating.ruled_out((39.95, -75.17), 0.0, [Anchor(39.04, -77.49, 0.0, "x", "x")]) is None


def test_the_anchor_quoted_is_the_one_missed_by_most():
    assert locating.ruled_out(BRISBANE, 3.0, [MIAMI, BOSTON]) == BOSTON


# --- anchors -------------------------------------------------------------------

def test_anchors_are_named_routers_quickest_per_city():
    found = locating.anchors([
        ("ae1.cr1.bos1.example.net", 6.0),
        ("ae2.cr2.bos1.example.net", 4.0),          # quicker: this one counts for Boston
        ("ae1.cr1.nyc1.example.net", 9.0),
        ("svc.example.com", 2.0),                   # no place in its name
        ("ae1.cr1.chi1.example.net", None),          # no round trip
    ])
    assert {(a.city, a.best, a.router) for a in found} == {
        ("Boston", 4.0, "ae2.cr2.bos1.example.net"), ("New York", 9.0, "ae1.cr1.nyc1.example.net")}


def test_an_anchor_that_contradicts_the_rest_is_dropped():
    # A router named after the far end of a transatlantic link, 3 ms away:
    # trusted, it would rule out every correct answer near this computer.
    found = locating.anchors([
        ("ae1.cr1.bos1.example.net", 3.0), ("ae1.cr1.nyc1.example.net", 9.0),
        ("ae1.cr1.phl1.example.net", 11.0), ("xe-0.lon1.example.net", 3.0)])
    assert sorted(a.city for a in found) == ["Boston", "New York", "Philadelphia"]


def test_two_anchors_contradicting_only_each_other_both_go():
    found = locating.anchors([("ae1.cr1.bos1.example.net", 3.0), ("xe-0.lon1.example.net", 3.0)])
    assert found == []


# --- the locator ------------------------------------------------------------------

DOC = "198.51.100.7"


def _geo(**known):
    g = GeoResolver()
    for ip, info in known.items():
        g.cache[ip] = info
    return g


def _info(lat, lon, city, country="United States"):
    return GeoInfo(lat, lon, city, "", country, "Example ISP", 64500, "Example Transit")


def test_the_name_comes_first_then_the_database():
    g = _geo(**{DOC: _info(*DENVER, "Denver")})
    loc = Locator(g, [NEW_YORK])
    w = loc.where(DOC, "ae2.cr2.chcgil09.example.net", 10.0)
    assert (w.text, w.label, w.source, w.why) == ("Chicago, US", "Chicago", "name", "")
    w = loc.where(DOC, "svc.example.com", 40.0)
    assert (w.text, w.label, w.source, w.point) == ("Denver, United States", "Denver", "ip", DENVER)


def test_an_impossible_database_answer_is_left_out_and_explained():
    g = _geo(**{DOC: _info(*DENVER, "Denver")})
    w = Locator(g, [NEW_YORK]).where(DOC, "svc.example.com", 10.0)
    assert (w.text, w.label, w.point, w.source) == ("", "", None, "")
    assert w.info is not None, "Details still shows what the database said"
    assert w.why.startswith("Not possible: it answers in 10 ms and cr1.nyc1.example.net (New York")
    assert "Denver is 2,6" in w.why


def test_without_its_own_round_trip_the_database_stands():
    g = _geo(**{DOC: _info(*DENVER, "Denver")})
    assert Locator(g, [NEW_YORK]).where(DOC, None).source == "ip"


def test_a_hop_takes_its_round_trip_from_the_path_only_for_the_same_router():
    g = _geo(**{DOC: _info(*DENVER, "Denver")})
    loc = Locator(g, [NEW_YORK], [(5, DOC, None, 10.0)])
    assert loc.where(DOC, None, ttl=5).why, "the path's round trip for this router"
    assert loc.where("198.51.100.8", None, ttl=5).why == "", "another router: not its round trip"


def test_a_name_without_coordinates_keeps_its_text_and_the_database_point():
    g = _geo(**{DOC: _info(45.52, -122.68, "Portland")})
    w = Locator(g).where(DOC, "ae1.portland.example.net", 30.0)
    assert (w.text, w.source) == ("Portland", "ip")


def test_a_hop_view_carries_its_own_routers_best_round_trip():
    hop = Hop(5)
    hop.record(4.0, "198.51.100.1", 0)        # one router, quick
    hop.record(30.0, "198.51.100.2", 0)       # a reroute: another, slow
    hop.record(28.0, "198.51.100.2", 0)
    v = HopView.of(hop)
    assert v.best == 4.0, "the window spans both routers"
    assert v.best_here == 28.0, "the current router's own"


def test_a_hop_remembers_a_bounded_number_of_routers():
    from pingerplot.model import MAX_ROUTERS_PER_HOP
    hop = Hop(5)
    for i in range(MAX_ROUTERS_PER_HOP + 10):
        hop.record(5.0, f"10.0.{i // 250}.{i % 250}", 0)
    assert len(hop.best_by_addr) == MAX_ROUTERS_PER_HOP


def test_a_saved_session_attributes_its_best_to_the_router_it_names():
    hop = hop_from_session({"ttl": 3, "address": DOC, "samples": [[1.7e9, 12.0], [1.7e9 + 1, 9.0]]})
    assert HopView.of(hop).best_here == 9.0


# --- one way in -----------------------------------------------------------------------

def test_nothing_reads_the_ip_database_for_a_place_but_the_locator():
    """A database answer reaches the screen only through locating.Locator,
    or a check added there is quietly skipped somewhere (the map once had
    its own fallback that would have drawn ruled-out answers anyway)."""
    root = Path(__file__).resolve().parents[1] / "pingerplot"
    found = []

    class Scan(ast.NodeVisitor):
        def __init__(self, name):
            self.name, self.inside = name, []

        def visit_FunctionDef(self, node):
            self.inside.append(node.name)
            self.generic_visit(node)
            self.inside.pop()

        def visit_Call(self, node):
            f = node.func
            owner = getattr(f, "value", None)
            if (isinstance(f, ast.Attribute) and f.attr == "get"
                    and getattr(owner, "id", getattr(owner, "attr", "")) == "geo"
                    # Whose network a router is on (the Network column) isn't a place.
                    and (self.name, self.inside[-1:]) != ("gui_hopinfo.py", ["network"])):
                found.append(f"{self.name}:{node.lineno}")
            self.generic_visit(node)

    for path in root.glob("*.py"):
        if path.name not in ("locating.py", "geoip.py"):
            Scan(path.name).visit(ast.parse(path.read_text(encoding="utf-8")))
    assert not found, f"the IP database read outside locating.Locator: {found}"


# --- the neighbor judgement ---------------------------------------------------------

QUEBEC = "be3.owr01.yqb21.example.net"
ATLANTA = "be9.owr02.atl11.example.net"


def _path_locator(path, quickest):
    """A path of (ttl, address, hostname, best), anchors from ``quickest``
    (hostname, best) pairs, and the database putting 198.51.100.7 in Washington."""
    g = _geo(**{DOC: _info(*WASHINGTON, "Washington")})
    return Locator(g, locating.anchors(quickest), path)


def test_a_hop_beside_a_named_one_at_the_same_round_trip_but_far_from_it_is_doubtful():
    # Between Boston and Quebec City at 16 ms: physics allows Washington (any
    # reply over about 2 ms could come from there), but it's a link from Quebec City.
    loc = _path_locator([(7, "198.51.100.1", "be20.owr02.bos01.example.net", 16.0), (8, DOC, None, 16.0),
                         (9, "198.51.100.9", QUEBEC, 17.0)],
                        [("po26.ear02.bos01.example.net", 3.0), (QUEBEC, 15.0)])
    w = loc.where(DOC, None, ttl=8)
    assert (w.text, w.point) == ("", None)
    assert w.why.startswith(f"Doubtful: it answers within 2 ms of hop 9, {QUEBEC} (Quebec City")


def test_a_named_hop_slower_than_its_citys_quickest_router_says_nothing():
    # Inside an MPLS tunnel every router reports the far end's round trip:
    # Atlanta at 55 ms, when an Atlanta router elsewhere answers in 41.
    loc = _path_locator([(13, "198.51.100.1", ATLANTA, 55.0), (14, DOC, None, 54.0)],
                        [("po26.ear02.bos01.example.net", 3.0), ("ae2.cr2.atl10.example.net", 41.0)])
    assert loc.where(DOC, None, ttl=14).why == ""


def test_a_hop_a_later_hop_beats_says_nothing():
    loc = _path_locator([(8, DOC, None, 16.0), (9, "198.51.100.9", QUEBEC, 17.0),
                         (10, "198.51.100.10", None, 9.0)],
                        [(QUEBEC, 15.0)])
    assert loc.where(DOC, None, ttl=8).why == ""


def test_the_neighbor_must_be_close_in_hops_and_round_trip_and_the_answer_far():
    quick = [(QUEBEC, 15.0)]
    near = (9, "198.51.100.9", QUEBEC, 17.0)
    assert _path_locator([(5, DOC, None, 16.0), near], quick).where(DOC, None, ttl=5).why == "", "4 hops away"
    assert _path_locator([(8, DOC, None, 12.0), near], quick).where(DOC, None, ttl=8).why == "", "5 ms apart"
    g = _geo(**{DOC: _info(46.0, -71.5, "Thetford Mines", "Canada")})          # 100 km from Quebec City
    assert Locator(g, locating.anchors(quick), [(8, DOC, None, 16.0), near]).where(DOC, None, ttl=8).why == ""
