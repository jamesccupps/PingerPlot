"""Start the window, or say what's missing.

tkinter comes with Python on Windows and from python.org on macOS, but most
Linux distributions package it separately. Without it ``import tkinter``
fails inside gui.py's imports, before the app can say anything, and the
user gets a ModuleNotFoundError traceback. Every way of starting the window
(main.py, ``python -m pingerplot``, the ``pingerplot`` command) comes here
first.
"""
from __future__ import annotations

import sys

TK_PACKAGES = (
    ("Debian, Ubuntu, Mint", "sudo apt install python3-tk"),
    ("Fedora, RHEL", "sudo dnf install python3-tkinter"),
    ("Arch, Manjaro", "sudo pacman -S tk"),
    ("openSUSE", "sudo zypper install python3-tk"),
)


def missing_tk_message() -> str:
    width = max(len(d) for d, _c in TK_PACKAGES)
    rows = "\n".join(f"  {d + ':':<{width + 1}}  {c}" for d, c in TK_PACKAGES)
    return ("PingerPlot's window needs Tk (Python's tkinter), and this Python doesn't have it.\n"
            f"Install it for your system, then start PingerPlot again:\n\n{rows}\n\n"
            "The headless runner needs no Tk: python3 -m pingerplot.headless --help")


def main() -> None:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print(missing_tk_message(), file=sys.stderr)
        raise SystemExit(1)
    from .gui import main as gui_main
    gui_main()
