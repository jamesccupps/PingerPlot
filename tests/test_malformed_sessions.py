"""A malformed session file is skipped piece by piece, never a crash.

load_dict and stats_from_session both say they skip a malformed hop rather
than crash, but only guarded the per-hop body against KeyError/ValueError/
TypeError/IndexError:

* ``"config": null``  -> AttributeError ('NoneType' has no .get)
* ``"events": null``  -> TypeError (not iterable)
* a hop that is not an object -> AttributeError
* a non-numeric config value -> ValueError, raised AFTER target_input,
  target_ip and route_len had been overwritten (a half-applied load)
* a session that is a JSON list -> AttributeError in --baseline, which main()
  does not catch, so the report died with a traceback

And the GUI registered the "(loaded)" row before calling load_dict, so a
failed load left an empty row behind.
"""
import json
import time

import pytest

from pingerplot import compare, headless
from pingerplot.monitor import Monitor

GOOD_HOP = {"ttl": 1, "address": "203.0.113.1", "samples": [[0, 1.0], [1, 2.0]]}

MALFORMED = {
    "config null": {"hops": [GOOD_HOP], "config": None},
    "events null": {"hops": [GOOD_HOP], "events": None},
    "events not pairs": {"hops": [GOOD_HOP], "events": [5, "x", None]},
    "hop not an object": {"hops": [1, "two", None, GOOD_HOP]},
    "hops is an object": {"hops": {"ttl": 1}},
    "config value not a number": {"hops": [GOOD_HOP], "config": {"interval": "fast"}},
    "address not a string": {"hops": [dict(GOOD_HOP, address=["a"], hostname={"b": 1})]},
    "target_input not a string": {"hops": [GOOD_HOP], "target_input": 42, "target_ip": [1]},
    # Python's json reads NaN, Infinity and 1e300 (2026-11 review).
    "values out of range": json.loads(
        '{"target_input":"x","hops":[{"ttl":1,"address":"192.0.2.1",'
        '"samples":[[1700000000, NaN],[1700000001, Infinity],[1e300, 5],[-1, 5],[1700000002, -3]]}],'
        '"events":[[0, 1e300, "info", "hello"],[1, NaN, "info", "x"]]}'),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_load_dict_survives(name):
    m = Monitor()
    m.load_dict(MALFORMED[name])
    views, _status, ip, target = m.snapshot()
    assert isinstance(target, str)
    assert ip is None or isinstance(ip, str)
    for v in views:
        assert v.address is None or isinstance(v.address, str)
        assert v.hostname is None or isinstance(v.hostname, str)
    m.shutdown()


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_stats_from_session_survives(name):
    stats = compare.stats_from_session(MALFORMED[name])
    for s in stats:
        assert s.address is None or isinstance(s.address, str)


def test_impossible_times_and_round_trips_are_dropped():
    """A time of 1e300 made time.localtime() raise in the Events view on
    every refresh; NaN and Infinity reached the averages and the drawing."""
    m = Monitor()
    data = json.loads(json.dumps(MALFORMED["values out of range"]))
    data["hops"][0]["samples"].append([1700000003, 7.5])
    data["events"].append([2, 1700000003, "info", "kept"])
    m.load_dict(data)
    (view,) = m.snapshot()[0]
    assert view.sent == 1 and view.avg == 7.5
    events, _seq = m.events_after(-1)
    assert [e.text for e in events] == ["kept"]
    for s in m.samples_for(1):
        time.localtime(s.t)
    m.shutdown()


def test_a_bad_config_value_keeps_the_rest_of_the_session():
    m = Monitor()
    m.load_dict({"target_input": "example.net", "hops": [GOOD_HOP],
                 "config": {"interval": "fast", "timeout_ms": 900}})
    assert m.target_input == "example.net"
    assert m.timeout_ms == 900, "one bad value discarded the good ones"
    assert len(m._hops) == 1
    m.shutdown()


def test_stats_from_session_ignores_a_non_object():
    assert compare.stats_from_session([GOOD_HOP]) == []


def test_load_dict_refuses_a_non_object_without_touching_state():
    m = Monitor()
    m.target_input = "keep.example.net"
    with pytest.raises(ValueError):
        m.load_dict(["not", "a", "session"])
    assert m.target_input == "keep.example.net"
    m.shutdown()


def test_baseline_that_is_not_an_object_is_reported_not_raised(tmp_path):
    path = tmp_path / "base.json"
    path.write_text(json.dumps([GOOD_HOP]), encoding="utf-8")
    out = []
    headless.print_comparison([], str(path), log=out.append)
    assert out and "not a PingerPlot session" in out[0]


def test_gui_failed_load_leaves_no_row(app, tmp_path, load_session, monkeypatch):
    from pingerplot import gui
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a, **k: errors.append(a))
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"hops": [GOOD_HOP], "target_input": 42}), encoding="utf-8")
    load_session(path)                     # odd but loadable: must not raise
    assert "42 (loaded)" in app._monitors

    monkeypatch.setattr(gui.Monitor, "load_dict",
                        lambda self, d: (_ for _ in ()).throw(ValueError("corrupt")))
    path2 = tmp_path / "t.json"
    path2.write_text(json.dumps({"hops": [], "target_input": "other"}), encoding="utf-8")
    load_session(path2)
    assert "other (loaded)" not in app._monitors, "a failed load left an empty row"
    assert errors
