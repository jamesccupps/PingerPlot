"""The window from the keyboard (the 2026-11-05 review, M13): the tabs and the
target list take focus, keys choose, focus is visible, and the views have
shortcuts."""
from tkinter import ttk

import pytest

from pingerplot.monitor import Monitor


@pytest.fixture
def shown(app):
    """Keys go to the widget with the keyboard focus, and a window that was
    never shown has none: show it for these tests."""
    app.root.deiconify()
    app.root.update()
    yield app
    app.root.withdraw()


def press(widget, key):
    widget.focus_force()
    widget.update()
    widget.event_generate(key)
    widget.update()


def _flat(layout):
    for name, opts in layout:
        yield name
        yield from _flat(opts.get("children", []))


def test_every_control_the_keyboard_reaches_shows_focus(app):
    st = ttk.Style(app.root)
    for style, element in (("TButton", "Button.focus"), ("Accent.TButton", "Button.focus"),
                           ("Subtle.TButton", "Button.focus"), ("TCheckbutton", "Checkbutton.focus"),
                           ("Switch.TCheckbutton", "Checkbutton.focus"), ("Nav.TLabel", "Label.focus"),
                           ("NavActive.TLabel", "Label.focus")):
        assert element in list(_flat(st.layout(style))), f"{style} has no focus ring"


def test_tabs_take_focus_and_keys_choose_them(shown):
    app = shown
    labels = app.nav._labels
    assert all(str(lab.cget("takefocus")) in ("1", "True") for lab in labels.values())
    press(labels["settings"], "<Return>")
    assert app.nav.current == "settings"
    press(labels["settings"], "<Left>")
    assert app.nav.current == "history"
    assert "focus" in labels["history"].state(), "the keyboard moved with it"
    press(labels["history"], "<Right>")
    press(labels["settings"], "<Right>")
    assert app.nav.current == "targets", "Right wraps round"


def test_shortcuts_reach_each_view_and_settings(shown):
    app = shown
    app.nav.select("history", notify=True)
    press(app.nav._labels["history"], "<Control-Key-3>")
    assert (app.nav.current, app.detail_nav.current) == ("targets", "map")
    press(app.nav._labels["targets"], "<Control-Key-4>")
    assert app.detail_nav.current == "events"
    press(app.nav._labels["targets"], "<Control-comma>")
    assert app.nav.current == "settings"


def test_the_target_list_works_like_a_list_box(shown):
    app = shown
    for name in ("a.example.com", "b.example.com", "c.example.com"):
        m = Monitor()
        m.target_input = name
        app._monitors[name] = m
    try:
        app._update_cards()
        app._select_card("a.example.com")
        app._update_cards()
        cards = app._cards
        assert [str(cards[n].cget("takefocus")) in ("1", "True") for n in cards] == [True, False, False], \
            "only the selected card is in the Tab order"
        press(cards["a.example.com"], "<Down>")
        assert app._active == "b.example.com"
        press(cards["b.example.com"], "<Down>")
        press(cards["c.example.com"], "<Down>")
        assert app._active == "c.example.com", "Down stops at the last"
        press(cards["c.example.com"], "<Up>")
        assert app._active == "b.example.com"
        assert str(cards["b.example.com"].cget("takefocus")) in ("1", "True")
    finally:
        for name in ("a.example.com", "b.example.com", "c.example.com"):
            app._monitors.pop(name).shutdown()
        app._update_cards()
