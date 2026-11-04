"""Linux desktop integration through freedesktop.org conventions: alert
notifications with notify-send, an entry in the applications menu, and
starting at sign-in (an XDG autostart entry).

Windows gets these from tray.py and winplatform.py; Linux has no
notification-area API Tk can reach without a third-party module, but nearly
every desktop ships notify-send (libnotify), which speaks the freedesktop
notification spec to GNOME, KDE Plasma, XFCE, Cinnamon and the rest, and
every one of them reads .desktop files.
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


# --- the applications menu and starting at sign-in ---------------------------------

APP_FILE = "pingerplot.desktop"
WM_CLASS = "PingerPlot"     # gui.main() names the Tk root this; StartupWMClass matches it


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def menu_entry_path() -> Path:
    return data_home() / "applications" / APP_FILE


def autostart_path() -> Path:
    return config_home() / "autostart" / APP_FILE


def launch_args() -> list:
    """How a desktop entry starts this copy (it runs in $HOME, so a relative
    path or ``-m`` from a checkout wouldn't): the main.py beside the package
    in a git checkout, else ``python -m pingerplot`` for a pip or pipx
    install, whose interpreter can import it from anywhere."""
    script = Path(__file__).resolve().parent.parent / "main.py"
    if script.is_file():
        return [sys.executable, str(script)]
    return [sys.executable, "-m", "pingerplot"]


def _exec_quote(arg: str) -> str:
    """One argument of a desktop entry's Exec key (Desktop Entry spec,
    "The Exec key"): quoted when it has to be, with the characters that are
    special inside quotes escaped, and % doubled."""
    arg = arg.replace("%", "%%")
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    return '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'


def exec_line(extra=()) -> str:
    return " ".join(_exec_quote(str(a)) for a in [*launch_args(), *extra])


def _entry(extra=(), autostart: bool = False) -> str:
    icon = icon_file(256)
    lines = ["[Desktop Entry]", "Type=Application", "Name=PingerPlot",
             "GenericName=Network path monitor",
             "Comment=Continuous traceroute: per-hop loss, latency, jitter and MOS over time",
             f"Exec={exec_line(extra)}", "Terminal=false",
             "Categories=Network;Monitor;Utility;",
             "Keywords=traceroute;ping;mtr;latency;packet loss;jitter;",
             f"StartupWMClass={WM_CLASS}"]
    if icon is not None:
        lines.append(f"Icon={icon}")
    if autostart:
        lines.append("X-GNOME-Autostart-enabled=true")
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not (path.is_file() and path.read_text(encoding="utf-8") == text):
            path.write_text(text, encoding="utf-8")
        return True
    except OSError as e:
        log.warning("couldn't write %s: %s", path, e)
        return False


def _remove(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as e:
        log.warning("couldn't remove %s: %s", path, e)
        return False


def menu_entry_enabled() -> bool:
    return IS_LINUX and menu_entry_path().is_file()


def set_menu_entry(enabled: bool) -> bool:
    if not IS_LINUX:
        return False
    return _write(menu_entry_path(), _entry()) if enabled else _remove(menu_entry_path())


def autostart_enabled() -> bool:
    return IS_LINUX and autostart_path().is_file()


def set_autostart(enabled: bool) -> bool:
    """Start minimized at sign-in, resuming the last targets (--minimized,
    as Windows' Run entry), or stop doing so."""
    if not IS_LINUX:
        return False
    if enabled:
        return _write(autostart_path(), _entry(["--minimized"], autostart=True))
    return _remove(autostart_path())


def refresh_entries() -> None:
    """Point existing entries at this copy, if it has moved (a new checkout
    folder, a different virtualenv)."""
    if menu_entry_enabled():
        set_menu_entry(True)
    if autostart_enabled():
        set_autostart(True)
