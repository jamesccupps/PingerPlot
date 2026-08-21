# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: the windowed GUI and the console headless runner.

Run from the repo root:

    python packaging/make_icon.py build/pingerplot.ico
    python -m PyInstaller --noconfirm --clean packaging/pingerplot.spec

A spec rather than a pile of command-line flags, because the flags are the part
that goes wrong quietly. An early attempt at this passed
``--exclude-module email`` to save space; ``urllib.request`` imports ``email``,
``geoip`` imports ``urllib.request``, and the frozen app died at import with a
message box. It cost 0.3 MB and the whole application. Excludes here are
therefore limited to packages the app provably never imports, and the release
workflow runs both binaries before it will attach either.

The app has zero third-party dependencies and zero data files -- the world map
is a Python module and the icon is drawn from math at runtime -- so there is
nothing to bundle beyond the interpreter, Tk, and the package itself.
"""
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
ICON = os.path.join(ROOT, "build", "pingerplot.ico")
if not os.path.exists(ICON):
    ICON = None  # build without one rather than fail; make_icon.py writes it

# Only things the package genuinely never imports. Verified against the import
# graph, not guessed at.
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


# --- windowed GUI ----------------------------------------------------------
gui_a = analyse("main.py")
gui_pyz = PYZ(gui_a.pure)
gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    gui_a.binaries,
    gui_a.datas,
    [],
    name="PingerPlot",
    console=False,          # no console window for the everyday launcher
    icon=ICON,
    upx=False,              # UPX packing is a large share of AV false
                            # positives, and this binary is already suspicious
                            # enough to a heuristic scanner: ctypes into
                            # iphlpapi, raw sockets, path enumeration.
    strip=False,
    debug=False,
    bootloader_ignore_signals=False,
    disable_windowed_traceback=False,  # a crash should say so, not vanish
)

# --- console headless runner ----------------------------------------------
cli_a = analyse(os.path.join("packaging", "headless_entry.py"))
cli_pyz = PYZ(cli_a.pure)
cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    cli_a.binaries,
    cli_a.datas,
    [],
    name="pingerplot-headless",
    console=True,           # it prints tables; it needs somewhere to print
    icon=ICON,
    upx=False,
    strip=False,
    debug=False,
    bootloader_ignore_signals=False,
)
