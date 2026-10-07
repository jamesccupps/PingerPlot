"""One failed refresh must not freeze the window for good.

_refresh scheduled its next run only after _refresh_once returned, so any
exception in a redraw -- one bad value from one target -- ended the timer:
every target's table, banner and graphs stopped updating while the monitors
kept probing behind a display that looked alive. Tk reports a callback's
exception and carries on, but nothing re-armed the timer.
"""
import pytest


def test_the_timer_is_rearmed_even_when_a_refresh_raises(app, tk_root, monkeypatch):
    for after_id in tk_root.tk.call("after", "info"):
        tk_root.after_cancel(after_id)

    def broken():
        raise RuntimeError("one bad redraw")

    monkeypatch.setattr(app, "_refresh_once", broken)
    with pytest.raises(RuntimeError):
        app._refresh()
    assert tk_root.tk.call("after", "info"), "refresh timer died with the exception"
