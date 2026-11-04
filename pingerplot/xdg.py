"""Linux desktop integration through freedesktop.org conventions: alert
notifications with notify-send.

Windows gets these from tray.py; Linux has no notification-area API Tk can
reach without a third-party module, but nearly every desktop ships
notify-send (libnotify), which speaks the freedesktop notification spec to
GNOME, KDE Plasma, XFCE, Cinnamon and the rest.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger("pingerplot.xdg")

IS_LINUX = sys.platform.startswith("linux")


def cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "PingerPlot"


def icon_file(size: int = 128) -> Optional[Path]:
    """The app's icon as a PNG on disk (drawn by appicon), for notifications
    to show; written once to the cache folder. None if it can't be."""
    from . import appicon
    path = cache_dir() / f"pingerplot-{size}.png"
    try:
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(appicon.png_bytes(size))
        return path
    except OSError:
        return None


class Notifier:
    """notify(title, text, on_click=None): a desktop notification. It shows
    and goes; a click does nothing (notify-send can only report one by
    blocking until the notification is dismissed)."""

    can_click = False

    def __init__(self, exe: str, icon: Optional[Path] = None) -> None:
        self.exe, self.icon = exe, icon

    def notify(self, title: str, text: str, on_click=None, sound: bool = False) -> None:
        args = [self.exe, "--app-name=PingerPlot"]
        if self.icon is not None:
            args.append(f"--icon={self.icon}")
        args += ["--", title, text]
        try:
            # Not waited for: a notification daemon that's slow to answer
            # mustn't hold up the window.
            subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
        except OSError as e:
            log.warning("notify-send failed: %s", e)


def notifier() -> Optional[Notifier]:
    """A Notifier on a Linux desktop with notify-send, else None."""
    if not IS_LINUX:
        return None
    exe = shutil.which("notify-send")
    return Notifier(exe, icon_file()) if exe else None
