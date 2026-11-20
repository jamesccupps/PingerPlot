"""What went wrong, kept: a small log in the settings folder of errors, and of
when PingerPlot started, quit and restarted.

The installed app has no console. An exception in a window callback or a
background thread was printed to a stderr that doesn't exist, and the
package's loggers (updates, the tray icon, Start with Windows) wrote to the
same nowhere, so a copy that quit early or stopped recording left no trace
(2026-11-18: 25 targets ended half a minute after an update, and nothing
said why). Everything logged under "pingerplot" now lands here, with each
uncaught exception's traceback.

Run as administrator, PingerPlot writes this file like the history: never
through a link or junction, which any program running as you could plant
in the settings folder. A URL is cut to its host: a webhook's secret is its
path.
"""
from __future__ import annotations

import logging
import logging.handlers
import re
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional

NAMES = {"gui": "error.log", "headless": "headless-error.log"}
MAX_BYTES = 256 * 1024          # then one older file, error.log.1

log = logging.getLogger("pingerplot")

_URL = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^/\s'\"<>]+)[^\s'\"<>]*", re.I)


def scrub(text: str) -> str:
    """``text`` with every URL cut to its scheme and host."""
    return _URL.sub(lambda m: m.group(1) + ("/…" if len(m.group(0)) > len(m.group(1)) else ""), text)


class _Scrubbed(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record))


class _Handler(logging.handlers.RotatingFileHandler):
    def _open(self):
        from .history import _is_link
        path = Path(self.baseFilename)
        for p in (path.parent, path):
            if _is_link(p):
                raise OSError(f"{p} is a link or junction, and PingerPlot won't write through one")
        return super()._open()


_installed: Optional[_Handler] = None
_previous_hooks = None


def _default_folder() -> Path:
    from . import settings
    return settings.config_dir()


def install(program: str = "gui", folder: Optional[Path] = None) -> Optional[Path]:
    """Start logging to ``folder`` (the settings folder by default) and catch
    uncaught exceptions on every thread. Returns the log's path, or None when
    it can't be written (a link, a folder that can't be made)."""
    global _installed
    if _installed is not None:
        return Path(_installed.baseFilename)
    path = Path(folder if folder is not None else _default_folder()) / NAMES[program]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = _Handler(path, maxBytes=MAX_BYTES, backupCount=1, encoding="utf-8", delay=True)
    except OSError:
        return None
    handler.setFormatter(_Scrubbed("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    if sys.stderr is not None:
        # Run from a terminal, warnings and tracebacks still show there too
        # (the handler above stops logging's own last-resort printing).
        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        console.setFormatter(_Scrubbed("%(levelname)s %(name)s: %(message)s"))
        log.addHandler(console)
    log.setLevel(logging.INFO)
    _installed = handler
    global _previous_hooks
    previous_hook, previous_thread_hook = _previous_hooks = sys.excepthook, threading.excepthook

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            previous_hook(exc_type, exc, tb)
            return
        exception("Uncaught", (exc_type, exc, tb))
        if previous_hook is not sys.__excepthook__:     # the default would print it twice
            previous_hook(exc_type, exc, tb)

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        exception(f"Uncaught in thread {name}", (args.exc_type, args.exc_value, args.exc_traceback))
        if previous_thread_hook is not threading.__excepthook__:
            previous_thread_hook(args)

    sys.excepthook, threading.excepthook = hook, thread_hook
    return path


def uninstall() -> None:
    """Stop logging to the file and put back the exception hooks found (tests)."""
    global _installed, _previous_hooks
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    _installed = None
    if _previous_hooks is not None:
        sys.excepthook, threading.excepthook = _previous_hooks
        _previous_hooks = None


def exception(where: str, exc_info) -> None:
    text = "".join(traceback.format_exception(*exc_info)).rstrip()
    log.error("%s:\n%s", where, text)


def tk_callback(exc_type, exc, tb) -> None:
    """For ``Tk.report_callback_exception``: Tk's own prints to stderr, which
    the installed app doesn't have."""
    exception("In the window", (exc_type, exc, tb))


def note(message: str) -> None:
    """A line about the app itself: started, quit, restarted."""
    logging.getLogger("pingerplot.app").info(message)
