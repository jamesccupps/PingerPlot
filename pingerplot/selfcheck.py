"""``PingerPlot --self-test``: what a build runs to prove it works, unattended.

The installer and the portable zip are checked by running the built program
with ``--self-test`` (and ``PINGERPLOT_DATA_DIR`` pointing at an empty folder,
so nothing real is touched). It builds the real window, round-trips the
settings file, sends one ICMP probe to loopback and makes the tray icons, then
writes what it found to ``self-test.txt`` in the data folder -- the windowed
executable has no console to print to -- and exits 0 only if all of it passed.
"""
from __future__ import annotations

import sys
import time
from typing import Callable, List, Optional

from . import __version__


def run() -> int:
    from . import appicon, icmp, settings, tray, winplatform

    lines: List[str] = []
    failed = False

    def check(name: str, fn: Callable[[], Optional[str]]) -> None:
        nonlocal failed
        try:
            lines.append(f"PASS  {name}: {fn() or 'ok'}")
        except Exception as exc:     # a self-test reports every failure, never stops at one
            failed = True
            lines.append(f"FAIL  {name}: {exc!r}")

    def version() -> str:
        kind = "frozen" if getattr(sys, "frozen", False) else "source"
        return f"PingerPlot {__version__} ({kind}, Python {sys.version.split()[0]}, {sys.platform})"

    def data_folder() -> str:
        marker = {"self_test": time.time()}
        if not settings.save(marker):
            raise OSError(f"cannot write {settings.config_path()}")
        if settings.load().get("self_test") != marker["self_test"]:
            raise ValueError("settings did not read back")
        portable = " (portable)" if settings.portable_dir() is not None else ""
        return f"{settings.config_dir()}{portable}"

    def window() -> str:
        import tkinter as tk
        from . import gui
        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.App(root)
            for after_id in root.tk.call("after", "info"):
                root.after_cancel(after_id)
            app._refresh_once()
            styles = len(app.look.style.element_names())
            app._quit()
        except BaseException:
            root.destroy()
            raise
        return f"built, {styles} theme elements"

    def probe() -> str:
        if not icmp.is_available():
            raise OSError(icmp.unavailable_reason())
        r = icmp.ping("127.0.0.1", 64, 2000)
        if not r.reached:
            raise OSError(f"no reply from 127.0.0.1 ({icmp.status_text(r.status)})")
        return f"127.0.0.1 answered in {r.rtt_ms:.1f} ms"

    def tray_icons() -> str:
        if not tray.AVAILABLE:
            return "no notification area on this platform"
        for state in appicon.STATES:
            h = tray.hicon_from_png(appicon.png_bytes(16, state), 16)
            if not h:
                raise OSError(f"could not make the {state} icon")
            tray._u32.DestroyIcon(h)
        return f"{len(appicon.STATES)} states"

    check("version", version)
    check("data folder", data_folder)
    check("window", window)
    check("icmp", probe)
    check("tray icons", tray_icons)
    if winplatform.IS_WINDOWS:
        lines.append(f"INFO  start with windows: {'on' if winplatform.autostart_enabled() else 'off'}")

    report = "\n".join(lines + ["", "FAILED" if failed else "PASSED"]) + "\n"
    try:
        settings.config_dir().mkdir(parents=True, exist_ok=True)
        (settings.config_dir() / "self-test.txt").write_text(report, encoding="utf-8")
    except OSError:
        failed = True
    if sys.stdout is not None:      # None in a windowed exe
        print(report, end="")
    return 1 if failed else 0
