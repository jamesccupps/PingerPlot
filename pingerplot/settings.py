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


PORTABLE_MARKER = "portable.txt"


def portable_dir() -> "Path | None":
    """The program's own folder, when it's a portable copy: a frozen build
    with ``portable.txt`` beside it keeps its settings there, so a copy on a
    USB stick carries them along. Running from source never is."""
    if not getattr(sys, "frozen", False):
        return None
    here = Path(sys.executable).resolve().parent
    return here if (here / PORTABLE_MARKER).exists() else None


def config_dir() -> Path:
    """Where PingerPlot keeps its settings: PINGERPLOT_DATA_DIR if set (the
    self-test and the demo point it at a scratch folder), a portable copy's
    own folder, or the per-user config directory."""
    override = os.environ.get("PINGERPLOT_DATA_DIR")
    if override:
        return Path(override)
    portable = portable_dir()
    if portable is not None:
        return portable
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
        with open(config_dir() / FILE_NAME, "r", encoding="utf-8-sig") as fh:
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
        # Yours alone on Linux and macOS (Windows ignores these modes; the
        # profile folder is already private): a webhook URL in here can be a
        # Slack, Teams or ntfy secret, and the default mode let every local
        # user read it.
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = d / (FILE_NAME + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
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
