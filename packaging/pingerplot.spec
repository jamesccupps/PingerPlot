# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build: one folder, two programs sharing it.

    python packaging/build_windows.py      (runs this, then makes the zip and the installer)

dist/PingerPlot/
    PingerPlot.exe              the app (windowed)
    pingerplot-headless.exe     the headless runner (console), for Task Scheduler
    _internal/                  Python, Tk, the package

A folder rather than 1.3's single-file executables: a single-file build unpacks
itself to %TEMP% on every start (slower, and what antivirus engines dislike
most about PyInstaller), and a folder is what the installer and the portable
zip carry.

Excludes are limited to packages the app provably never imports. An early
build passed --exclude-module email to save space; urllib.request imports
email, geoip imports urllib.request, and the frozen app died at import with a
message box. It cost 0.3 MB and the whole application.

The app has zero third-party dependencies and zero data files -- the world map
is a Python module and the icon is drawn at run time -- so there is nothing to
bundle beyond the interpreter, Tk and the package. build_windows.py writes the
icon (build/pingerplot.ico) and the version resource (build/version_info.txt)
before this runs.
"""
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
ICON = os.path.join(ROOT, "build", "pingerplot.ico")
VERSION = os.path.join(ROOT, "build", "version_info.txt")

EXCLUDES = ["pytest", "numpy", "setuptools", "pip", "pkg_resources"]


def analyse(script):
    return Analysis(
        [os.path.join(ROOT, script)],
        pathex=[ROOT],
        binaries=[],
        datas=[],
        hiddenimports=[],
        hookspath=[],
        runtime_hooks=[],
        excludes=EXCLUDES,
        noarchive=False,
    )


gui_a = analyse("main.py")
cli_a = analyse(os.path.join("packaging", "headless_entry.py"))

common = dict(
    exclude_binaries=True,      # one shared _internal, collected below
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    icon=ICON if os.path.exists(ICON) else None,
    version=VERSION if os.path.exists(VERSION) else None,
)

gui_exe = EXE(
    PYZ(gui_a.pure), gui_a.scripts, [],
    name="PingerPlot",
    console=False,              # no console window for the everyday app
    upx=False,                  # UPX packing is a large share of AV false positives
    disable_windowed_traceback=False,   # a crash should say so, not vanish
    **common,
)

cli_exe = EXE(
    PYZ(cli_a.pure), cli_a.scripts, [],
    name="pingerplot-headless",
    console=True,               # it prints tables and events; it needs somewhere to print
    upx=False,
    **common,
)

COLLECT(
    gui_exe, gui_a.binaries, gui_a.datas,
    cli_exe, cli_a.binaries, cli_a.datas,
    strip=False,
    upx=False,
    name="PingerPlot",
)
