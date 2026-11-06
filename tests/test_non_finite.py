"""Infinity, 1e999 and NaN (which json reads and writes) can't stop the app
opening or make a button silently do nothing."""
import json
import math

import pytest

from pingerplot.monitor import Monitor


@pytest.mark.parametrize("opt", ["interval", "alert_loss_pct", "alert_latency_ms", "alert_mos"])
@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_the_engine_refuses_a_non_finite_option(opt, bad):
    with pytest.raises(ValueError, match="finite"):
        Monitor().start("127.0.0.1", **{opt: bad})


@pytest.mark.parametrize("text", ["inf", "1e999", "nan", "-Infinity"])
def test_the_settings_form_says_so(tk_root, text):
    from pingerplot.gui_settings import OptionVars
    ov = OptionVars(tk_root)
    ov["timeout_ms"].set(text)
    with pytest.raises(ValueError, match="Reply timeout"):
        ov.to_options()


def test_a_corrupt_target_still_lets_the_app_open(tk_root, isolated_settings, monkeypatch):
    from pingerplot import gui
    isolated_settings.save({"resume_on_launch": True, "targets": ["192.0.2.1"],
                            "target_options": {"192.0.2.1": {"timeout_ms": math.inf}}})
    assert "Infinity" in (isolated_settings.config_path()).read_text()
    calls = []

    def fake_start(self, name=None, options=None):
        calls.append(options)
        if options and math.isinf(options.get("timeout_ms", 0)):
            raise OverflowError("cannot convert float infinity to integer")
    monkeypatch.setattr(gui.App, "_start", fake_start)
    monkeypatch.setattr(gui.icmp, "is_available", lambda: True)
    app = gui.App(tk_root)
    for job in tk_root.tk.call("after", "info"):
        tk_root.after_cancel(job)
    app.geo.stop()
    assert len(calls) == 2 and not math.isinf((calls[1] or {}).get("timeout_ms", 0))
