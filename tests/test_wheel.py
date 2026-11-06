"""The mouse wheel scrolls on every platform: Windows touchpads (small
deltas that used to round to no movement) and Linux (Button-4/5, which Tk
8.6 on X11 sends instead of <MouseWheel>)."""
import sys
from types import SimpleNamespace

import pytest

from pingerplot import gui_widgets
from pingerplot.gui_widgets import WheelScroll


def _wheel():
    got = []
    return WheelScroll(got.append), got


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_a_mouse_notch_is_one_unit(monkeypatch, platform):
    monkeypatch.setattr(gui_widgets.sys, "platform", platform)
    w, got = _wheel()
    w.wheel(SimpleNamespace(delta=-120))
    w.wheel(SimpleNamespace(delta=240))
    assert got == [1, -2]


def test_touchpad_deltas_add_up(monkeypatch):
    monkeypatch.setattr(gui_widgets.sys, "platform", "win32")
    w, got = _wheel()
    for _ in range(4):
        w.wheel(SimpleNamespace(delta=-30))
    assert got == [1], "four 30s are one notch; each used to be int(-30/120) == 0"


def test_x11_buttons_scroll(monkeypatch):
    w, got = _wheel()
    w.up()
    w.down()
    assert got == [-1, 1]


def test_the_scroll_frame_and_timeline_bind_all_three(app):
    w = app.timeline_canvas
    for ev in WheelScroll.EVENTS:
        assert w.bind(ev), f"the Timeline doesn't bind {ev}"
