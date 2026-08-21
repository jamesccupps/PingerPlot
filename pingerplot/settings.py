"""Persisted UI / engine / alert settings (pure standard library).

Stored as JSON under ``%APPDATA%\\PingerPlot`` on Windows (XDG/home fallback
elsewhere), so the theme, engine options, alert thresholds, and the target list
survive a restart instead of resetting every launch.

Convenience, not critical state: a missing or unreadable file yields ``{}`` and
a save failure is swallowed — the app must never fail to start (or to close)
because of settings. Writes are atomic (temp file + ``os.replace``).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "PingerPlot"
FILE_NAME = "settings.json"


def config_dir() -> Path:
    """Per-user config directory for PingerPlot."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / APP_NAME


def config_path() -> Path:
    return config_dir() / FILE_NAME


def load() -> dict:
    """Return the saved settings, or ``{}`` if absent/unreadable/not a dict."""
    try:
        with open(config_dir() / FILE_NAME, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict) -> bool:
    """Atomically write ``data``. Returns False on failure, and never raises.

    TypeError and ValueError are caught alongside OSError because json.dump
    raises them for a value it cannot encode and for a circular reference —
    not I/O errors, but just as fatal here. The GUI calls this from its
    window-close handler, so an escaping exception means the app throws on the
    way out, after the window has already been asked to close.

    A failed write is a no-op: the temp file is removed rather than left behind
    forever, and os.replace never runs, so the previous settings survive.
    """
    tmp = None
    try:
        d = config_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (FILE_NAME + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, d / FILE_NAME)
        return True
    except (OSError, TypeError, ValueError):
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
        return False
