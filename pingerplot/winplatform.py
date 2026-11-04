"""Windows integration: DPI, title bar colours, theme and accent, Start with
Windows, single instance. Every function is a harmless no-op elsewhere, so
the GUI runs (and its tests run) on Linux and macOS too.

Adapted from UniFi Stock Watcher, a sibling project with the same conventions.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger("pingerplot.win")

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes
    import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "PingerPlot"
MUTEX_NAME = "JamesCupps.PingerPlot"           # an installer waits on this too
APP_ID = "JamesCupps.PingerPlot"
TRAY_CLASS = "PingerPlotTray"
WM_SHOW_WINDOW = 0x8000 + 0x42                 # WM_APP + 0x42, see tray.py


def enable_dpi_awareness() -> None:
    """System DPI aware, before the first window. Tk 8.6 can't redraw when a
    window moves to a monitor of another scale, so per-monitor awareness
    would leave it the wrong size there; system awareness lets Windows scale
    it instead, and keeps text sharp on the main monitor."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def set_app_id(app_id: str = APP_ID) -> None:
    """The taskbar and notifications group this process under app_id; an
    installer gives its Start menu shortcut the same id."""
    if IS_WINDOWS:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except (AttributeError, OSError):
            pass


def _reg_value(path: str, name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def system_prefers_dark() -> Optional[bool]:
    """Windows' app light/dark setting; None where there isn't one to ask."""
    if not IS_WINDOWS:
        return None
    v = _reg_value(r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                   "AppsUseLightTheme")
    return None if v is None else v == 0


def system_accent(dark: bool) -> Optional[str]:
    """Windows' accent colour, in the shade Windows itself uses for accent
    buttons in this theme (AccentPalette: Dark1 in light mode, Light2 in
    dark mode), or None."""
    if not IS_WINDOWS:
        return None
    pal = _reg_value(r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent",
                     "AccentPalette")
    if isinstance(pal, bytes) and len(pal) >= 32:
        i = 4 * (1 if dark else 4)
        return "#{:02x}{:02x}{:02x}".format(pal[i], pal[i + 1], pal[i + 2])
    v = _reg_value(r"Software\Microsoft\Windows\DWM", "AccentColor")
    if isinstance(v, int):
        return "#{:02x}{:02x}{:02x}".format(v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
    return None


def style_title_bar(tk_window, dark: bool, caption: Optional[str] = None,
                    text: Optional[str] = None) -> None:
    """Dark or light title bar to match the window (Windows 10 1809+), and on
    Windows 11 the caption in the window's own background colour."""
    if not IS_WINDOWS:
        return
    try:
        tk_window.update_idletasks()
        hwnd = wintypes.HWND(int(tk_window.wm_frame(), 16))
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                              ctypes.c_void_p, wintypes.DWORD]
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):           # DWMWA_USE_IMMERSIVE_DARK_MODE, pre-20H1
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), 4) == 0:
                break
        for attr, colour in ((35, caption), (36, text)):   # CAPTION_COLOR, TEXT_COLOR
            if colour:
                r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
                c = ctypes.c_uint(r | (g << 8) | (b << 16))
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(c), 4)
    except Exception:       # cosmetic: an old Windows simply keeps its title bar
        log.debug("title bar styling failed", exc_info=True)


def set_menu_theme(dark: bool) -> None:
    """Native popup menus (tk.Menu, the tray menu) in the app's theme, through
    uxtheme's SetPreferredAppMode (ordinal 135, Windows 10 1903+)."""
    if not IS_WINDOWS:
        return
    try:
        ux = ctypes.WinDLL("uxtheme")
        set_mode = ux[135]
        set_mode.argtypes, set_mode.restype = [ctypes.c_int], ctypes.c_int
        set_mode(2 if dark else 3)      # ForceDark / ForceLight
        ux[136]()                       # FlushMenuThemes
    except Exception:       # cosmetic, and an undocumented export
        pass


# --- Start with Windows ------------------------------------------------------

def autostart_command() -> str:
    """What the Run key should hold to start this copy at sign-in: in the
    notification area, resuming the last session's targets."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" --minimized'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    interp = pyw if pyw.exists() else Path(sys.executable)
    script = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{interp}" "{script}" --minimized'


def autostart_value() -> Optional[str]:
    if not IS_WINDOWS:
        return None
    return _reg_value(RUN_KEY, RUN_VALUE)


def autostart_enabled() -> bool:
    return autostart_value() is not None


def set_autostart(enabled: bool) -> bool:
    """Add or remove the Run entry, touching the registry only when it has
    to change: Defender flags programs that keep rewriting Run keys.

    This starts the app as you, not elevated, so TCP/UDP traceroute (which
    needs Administrator) isn't available to targets it resumes; launch.ps1's
    -InstallAutostart is the elevated alternative."""
    if not IS_WINDOWS:
        return False
    current = autostart_value()
    wanted = autostart_command() if enabled else None
    if current == wanted:
        return True
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if wanted is None:
                winreg.DeleteValue(k, RUN_VALUE)
            else:
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, wanted)
        return True
    except OSError:
        log.warning("could not change Start with Windows", exc_info=True)
        return False


def refresh_autostart() -> None:
    """If Start with Windows is on but points at another copy's path (the
    folder moved, an old install), point it at this one."""
    current = autostart_value()
    if current is not None and current != autostart_command():
        set_autostart(True)


# --- Single instance ---------------------------------------------------------

class SingleInstance:
    """A named mutex held for the life of the process.

    ``already_running`` is True when another copy holds it; that copy can
    then be asked to show its window (its tray window handles
    WM_SHOW_WINDOW). Two copies would each restore and save the same target
    list and settings file, and probe every target twice.
    """

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.handle = None
        self.already_running = False
        if not IS_WINDOWS:
            return
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = wintypes.HANDLE
        self.handle = k32.CreateMutexW(None, False, name)
        self.already_running = k32.GetLastError() == 183    # ERROR_ALREADY_EXISTS

    def signal_existing(self) -> bool:
        if not IS_WINDOWS:
            return False
        u32 = ctypes.windll.user32
        u32.FindWindowW.restype = wintypes.HWND
        hwnd = u32.FindWindowW(TRAY_CLASS, None)
        if hwnd:
            u32.PostMessageW(hwnd, WM_SHOW_WINDOW, 0, 0)
            return True
        return False

    def release(self) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None
