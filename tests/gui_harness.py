"""Fixtures for driving the real gui.App in a test.

gui.py is 1,470 lines at ~10% coverage, and the suite has been skipping it on
the grounds that it imports tkinter at module level. That is true but not a
reason: Tk is testable headlessly with no heavy infrastructure. On Windows a
withdrawn root works directly; on Linux ``xvfb-run -a python -m pytest`` is the
whole story, and CI now does that on the ubuntu legs.

What this makes reachable is the part of the app that persists state, restores
it, and decides what belongs in the Targets grid -- which is where the
settings/session bugs live, because nothing else in the codebase can see them.

Everything here refuses to touch the user's real settings file. That matters
more than usual: settings.save() writes to %APPDATA%\\PingerPlot, and a test
that clobbered it would silently delete somebody's target list.
"""
from __future__ import annotations

import json

import pytest

tk = pytest.importorskip("tkinter", reason="gui.py needs tkinter")


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Point settings.save()/load() at a throwaway directory.

    Autouse would be wrong -- it should be obvious in a test's signature that
    it writes settings -- but every GUI fixture below depends on it, so no
    App is ever constructed against the real file.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from pingerplot import settings
    return settings


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:                      # no display
        pytest.skip(f"no display for Tk: {exc}")
    root.withdraw()                                 # never flash a window
    try:
        yield root
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


@pytest.fixture
def app(tk_root, isolated_settings):
    """A real gui.App, with the periodic refresh and geo worker suppressed.

    Both are cancelled rather than left running: _refresh reschedules itself
    every 700 ms and would keep firing against a destroyed root at teardown,
    and the geo worker would make real HTTPS requests from a test run.
    """
    from pingerplot import gui

    a = gui.App(tk_root)
    for after_id in tk_root.tk.call("after", "info"):
        tk_root.after_cancel(after_id)
    a.geo.stop()
    try:
        yield a
    finally:
        for name in list(a._monitors):
            try:
                a._monitors.pop(name).shutdown()
            except Exception:
                pass
        a.geo.stop()


def write_session(path, target="example.net", hops=(1, 2)):
    """A minimal but valid session file, of the shape Monitor.to_dict emits.

    Addresses are RFC 5737 documentation space -- tests/test_no_real_addresses.py
    exists because a real capture once made it into the README.
    """
    data = {
        "target_input": target,
        "target_ip": "203.0.113.10",
        "packet_type": "icmp",
        "hops": [
            {"ttl": ttl, "address": f"203.0.113.{ttl}", "hostname": f"r{ttl}.example.net",
             "samples": [[0, 1.0 + ttl], [1, 1.2 + ttl], [2, 1.1 + ttl]]}
            for ttl in hops
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


@pytest.fixture
def load_session(app, monkeypatch):
    """Drive App._load_session by faking only the file dialog.

    Deliberately not a refactor of _load_session into dialog + loader: the real
    method, including its guards on a non-session file, is what should be under
    test, and the only untestable part of it is the modal.
    """
    from pingerplot import gui

    def _load(path):
        monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **_kw: str(path))
        app._load_session()

    return _load
