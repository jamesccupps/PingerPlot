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


def test_labels_far_apart_all_sit_to_the_right(canvas):
    _label_places(canvas, INK, [_hop(2, 100, 100, "Boston"), _hop(3, 600, 300, "London")], 7)
    assert all(b[0] > x for (_t, b), x in zip(_labels(canvas), (100, 600)))


@pytest.mark.parametrize("ttls,text", [([5], "5"), ([5, 6], "5–6"), ([2, 5], "2, 5"),
                                       ([3, 4, 5], "3–5")])
def test_ttl_lists(ttls, text):
    assert _ttl_list(ttls) == text
