"""The Map's labels: one per place, never on top of each other.

At world scale a path within one region (Portland, Boston, New York,
Ashburn) is a few pixels across, and one label per hop drew an unreadable
pile; a destination and its edge router in one city drew two labels on one
spot."""
from types import SimpleNamespace

import pytest

from pingerplot.gui_charts import Ink, _label_places, _ttl_list


@pytest.fixture
def canvas(tk_root):
    import tkinter as tk
    c = tk.Canvas(tk_root, width=1080, height=540)
    yield c
    c.destroy()


INK = Ink(col={"title": "#ffffff"}, font=lambda *a: ("Segoe UI", 9), s=lambda px: int(px))


def _hop(ttl, x, y, city):
    return (SimpleNamespace(ttl=ttl), SimpleNamespace(city=city, country="US"), x, y)


def _labels(c):
    return [(c.itemcget(i, "text"), c.bbox(i)) for i in c.find_all() if c.type(i) == "text"]


def _overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def test_hops_in_one_place_share_a_label(canvas):
    _label_places(canvas, INK, [_hop(5, 300, 200, "Denver"), _hop(6, 300, 200, "Denver")], 7)
    assert [t for t, _b in _labels(canvas)] == ["5–6. Denver"]


def test_labels_close_together_never_overlap(canvas):
    # Roughly Portland, Boston, New York and Ashburn on a 1080 px world.
    hops = [_hop(2, 329, 139, "Portland"), _hop(3, 327, 143, "Boston"),
            _hop(4, 318, 148, "New York"), _hop(5, 309, 153, "Ashburn")]
    _label_places(canvas, INK, hops, 7)
    labels = _labels(canvas)
    assert labels[0][0] == "2. Portland", "the first hop always gets its label"
    for i, (_t, a) in enumerate(labels):
        for _u, b in labels[i + 1:]:
            assert not _overlap(a, b)


def test_a_crowded_label_moves_to_the_other_side_first(canvas):
    _label_places(canvas, INK, [_hop(2, 400, 200, "Portland"), _hop(3, 402, 203, "Boston")], 7)
    (t1, b1), (t2, b2) = _labels(canvas)
    assert (t1, t2) == ("2. Portland", "3. Boston")
    assert b1[0] > 400 and b2[2] < 402, "Portland to the right of its dot, Boston to the left"


def test_a_label_never_covers_another_hops_dot(canvas):
    """Chicago's dot sits where Boston's label would go on either side."""
    rad = 4
    hops = [_hop(2, 420, 200, "Portland"), _hop(3, 410, 204, "Boston"), _hop(4, 370, 206, "Chicago")]
    _label_places(canvas, INK, hops, 7, rad)
    dots = {ttl: (x - rad, y - rad, x + rad, y + rad) for (v, _g, x, y) in hops for ttl in [v.ttl]}
    for text, box in _labels(canvas):
        own = int(text.split(".")[0])
        for ttl, dot in dots.items():
            if ttl != own:
                assert not _overlap(box, dot), f"{text!r} covers hop {ttl}'s dot"


def test_a_label_steps_off_the_paths_line(canvas):
    """Boston's line runs off to the right, so its label goes the other way;
    London is the end of the path and keeps the right."""
    _label_places(canvas, INK, [_hop(2, 100, 200, "Boston"), _hop(3, 600, 200, "London")], 7)
    (_t1, b1), (_t2, b2) = _labels(canvas)
    assert b1[2] < 100 and b2[0] > 600
    assert not [i for i in canvas.find_all() if canvas.type(i) == "rectangle"], "no plate needed"


def test_crossing_detection():
    from pingerplot.gui_charts import _crosses
    box = (10, 10, 20, 20)
    assert _crosses(((0, 15), (30, 15)), box)          # straight through
    assert _crosses(((15, 15), (40, 40)), box)         # starts inside
    assert not _crosses(((0, 0), (30, 5)), box)        # passes above
    assert not _crosses(((0, 30), (5, 0)), box)        # passes to the left
    assert not _crosses(((25, 0), (25, 30)), box)      # vertical, beside it


@pytest.mark.parametrize("ttls,text", [([5], "5"), ([5, 6], "5–6"), ([2, 5], "2, 5"),
                                       ([3, 4, 5], "3–5")])
def test_ttl_lists(ttls, text):
    assert _ttl_list(ttls) == text


# --- the view: zoomed to the path -------------------------------------------------

import math  # noqa: E402

from pingerplot.gui_charts import MAP_MIN_SPAN, map_view  # noqa: E402

NORTHEAST = [(-70.26, 43.66), (-71.06, 42.36), (-74.01, 40.71), (-77.49, 39.04)]


def test_with_nothing_located_it_is_the_whole_world():
    proj = map_view([], 1000, 500)
    assert proj(-180, 90) == (0, 0) and proj(180, -90) == (1000, 500)


def test_the_path_fills_the_view_inside_the_margin():
    w, h, m = 1000, 600, 24
    proj = map_view(NORTHEAST, w, h, margin=m)
    for lon, lat in NORTHEAST:
        x, y = proj(lon, lat)
        assert m <= x <= w - m and m <= y <= h - m
    xs = [proj(*p)[0] for p in NORTHEAST]
    assert max(xs) - min(xs) > 150, "a regional path should spread out, not sit in a clump"


def test_the_path_is_centred():
    proj = map_view(NORTHEAST, 1000, 600)
    mid_lon = (min(p[0] for p in NORTHEAST) + max(p[0] for p in NORTHEAST)) / 2
    mid_lat = (min(p[1] for p in NORTHEAST) + max(p[1] for p in NORTHEAST)) / 2
    x, y = proj(mid_lon, mid_lat)
    assert abs(x - 500) < 1e-6 and abs(y - 300) < 1e-6


def test_nothing_is_stretched():
    """A degree of longitude is cos(latitude) of a degree of latitude, at the
    view's middle latitude."""
    proj = map_view(NORTHEAST, 1000, 600)
    lat_mid = (39.04 + 43.66) / 2
    dx = proj(-70, 41)[0] - proj(-71, 41)[0]
    dy = proj(-70, 40)[1] - proj(-70, 41)[1]
    assert dx / dy == pytest.approx(math.cos(math.radians(lat_mid)))


@pytest.mark.parametrize("points", [[(-70.26, 43.66)], [(-70.26, 43.66), (-70.25, 43.65)]])
def test_it_never_zooms_past_what_the_coastlines_can_show(points):
    w, h = 1000, 600
    proj = map_view(points, w, h)
    lats = [p[1] for p in points]
    k = math.cos(math.radians((min(lats) + max(lats)) / 2))
    px_per_deg = proj(1, points[0][1])[0] - proj(0, points[0][1])[0]
    assert w / (px_per_deg / k) >= MAP_MIN_SPAN - 1e-9


def test_a_label_that_must_cross_the_line_gets_a_plate(canvas):
    """Chicago has the path on all four sides: its label sits across the line,
    on a plate in the map's colour so the line doesn't show through."""
    ink = Ink(col={"title": "#ffffff", "graph_bg": "#202020"}, font=INK.font, s=INK.s)
    hops = [_hop(2, 100, 200, "Boston"), _hop(4, 300, 200, "Chicago"), _hop(5, 500, 200, "Denver"),
            _hop(6, 300, 50, "Calgary"), _hop(7, 300, 350, "Dallas")]
    _label_places(canvas, ink, hops, 7, 4)
    items = list(canvas.find_all())
    text = next(i for i in items if canvas.type(i) == "text" and canvas.itemcget(i, "text") == "4. Chicago")
    plate = next(i for i in items if canvas.type(i) == "rectangle")
    assert items.index(plate) < items.index(text), "the plate is below the text"
    assert canvas.itemcget(plate, "fill") == "#202020"


def test_a_label_at_the_right_edge_goes_to_the_left(canvas):
    _label_places(canvas, INK, [_hop(2, 990, 200, "Portland")], 7, 4, bounds=(0, 0, 1000, 500))
    (text, box), = _labels(canvas)
    assert box[2] <= 1000 and box[2] < 990
