"""The Map tab has to repaint when geo lookups come back.

_refresh_once only redraws the active canvas when its inputs change, because
the refresh timer ticks faster than probes arrive. The key was (target, round,
selected hop, theme) — and geo lookups touch none of those. They land on a
background thread roughly a second apart, well after the Map tab drew itself.

On a live monitor the next round's redraw hid it. A *loaded session* has no
next round: _round is frozen, so the map drew its coastlines, fired off the
lookups, and then never plotted a single answer. Resizing the window was the
only way to see them, because the Configure binding forces a redraw.

draw_version lives in model.py, not gui.py, precisely so this is testable without
a display, on every platform in the matrix.
"""
from pingerplot.geoip import GeoResolver, GeoInfo
from pingerplot.model import draw_version
from pingerplot.monitor import Monitor


class Stub:
    def __init__(self, rnd=0):
        self._round = rnd


def _v(**kw):
    args = dict(active_name="8.8.8.8", monitor=Stub(3), selected_ttl=2,
                theme="dark", geo_count=0)
    args.update(kw)
    return draw_version(**args)


def test_identical_state_compares_equal():
    """The whole point of the key: a tick with nothing new must not redraw."""
    assert _v() == _v()


def test_a_completed_geo_lookup_changes_the_key():
    """The regression."""
    assert _v(geo_count=0) != _v(geo_count=1)


def test_geo_lookups_keep_changing_it_as_they_trickle_in():
    keys = [_v(geo_count=n) for n in range(5)]
    assert len(set(keys)) == 5


def test_a_frozen_monitor_still_repaints_for_geo():
    """A loaded session never advances _round — geo is the only thing that can
    move, so it has to be enough on its own."""
    frozen = Stub(0)
    a = draw_version("loaded", frozen, None, "dark", 0)
    b = draw_version("loaded", frozen, None, "dark", 3)
    assert a != b


def test_the_other_inputs_still_matter():
    assert _v() != _v(active_name="1.1.1.1")
    assert _v() != _v(monitor=Stub(4))
    assert _v() != _v(selected_ttl=5)
    assert _v() != _v(theme="light")


def test_no_active_monitor_is_handled():
    assert draw_version(None, None, None, "dark", 0)[1] == -1


def test_the_key_is_hashable_and_comparable():
    """It is stored and compared with != every tick; an unhashable or
    unorderable member would throw in the refresh loop."""
    assert hash(_v())
    assert (_v() == _v()) is True


# --- the counter it reads --------------------------------------------------

def test_resolver_count_starts_at_zero():
    assert GeoResolver().count() == 0


def test_resolver_count_includes_skipped_private_hops():
    """A private hop is cached as None without a lookup. That is still a state
    change the map should repaint for — the hop is now known-unplottable rather
    than pending."""
    r = GeoResolver()
    r.request("10.0.0.1")
    assert r.count() == 1


def test_resolver_count_tracks_resolved_entries():
    r = GeoResolver()
    r.cache["8.8.8.8"] = GeoInfo(1.0, 2.0, "c", "r", "co", "isp")
    r.cache["1.1.1.1"] = None
    assert r.count() == 2


def test_monitor_round_is_read_through_getattr_not_assumed():
    """draw_version is handed whatever the GUI has, including the placeholder
    Monitor used when no target is selected."""
    assert draw_version("x", Monitor(), None, "dark", 0)[1] == 0
