"""Build the Windows release files into dist/.

    python packaging/build_windows.py [--no-installer]
    python packaging/build_windows.py --program
    python packaging/build_windows.py --package [--sign]

    dist/PingerPlot/              the program folder (PyInstaller)
    dist/PingerPlot-win64.zip     that folder plus portable.txt
    dist/PingerPlot-Setup.exe     the installer (needs Inno Setup 6)
    dist/SHA256SUMS.txt           hashes of the two downloads
    dist/latest.json              the signed update manifest, when the
                                  release-signing key is on this PC

Needs packaging/requirements-build.txt (PyInstaller). Without Inno Setup the
installer is skipped and everything else is built, which is enough to test the
program and the zip locally; CI builds and tests all of it. Without the
signing key (packaging/signing.py) latest.json is skipped: CI signs in a
separate job that never runs the build tools.

The release splits the build in two. --program makes only the program folder
(PyInstaller). --package makes the zip, the installer and the checksums from a
program folder already in dist/, with the standard library and Inno Setup
alone; with --sign it first has the two exes Authenticode-signed, and Inno
Setup the uninstaller and Setup.exe (packaging/authenticode.ps1, which needs an
Azure sign-in). The release's signing job runs --package --sign, so nothing
from PyPI runs where the signing identity is.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pingerplot import __version__  # noqa: E402
from pingerplot.settings import PORTABLE_MARKER  # noqa: E402

DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_DIR = DIST / "PingerPlot"
INSTALLER_NAME = "PingerPlot-Setup.exe"
PORTABLE_NAME = "PingerPlot-win64.zip"
EXES = ("PingerPlot.exe", "pingerplot-headless.exe")
AUTHENTICODE = ROOT / "packaging" / "authenticode.ps1"

PORTABLE_NOTE = """This file makes PingerPlot portable: it keeps its settings in this
folder (settings.json) rather than in %APPDATA%\\PingerPlot.
Delete this file to use %APPDATA%\\PingerPlot instead.
"""


def version_resource() -> None:
    """The version and description Windows shows in the exe's Properties."""
    major, minor, patch = (int(x) for x in __version__.split(".")[:3])
    BUILD.mkdir(exist_ok=True)
    (BUILD / "version_info.txt").write_text(f"""# Written by packaging/build_windows.py
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({major}, {minor}, {patch}, 0), prodvers=({major}, {minor}, {patch}, 0),
                    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'James Cupps'),
      StringStruct('FileDescription', 'PingerPlot network path monitor'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'PingerPlot'),
      StringStruct('LegalCopyright', 'Copyright (c) James Cupps. MIT license.'),
      StringStruct('OriginalFilename', 'PingerPlot.exe'),
      StringStruct('ProductName', 'PingerPlot'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")


def icon() -> None:
    sys.path.insert(0, str(ROOT / "packaging"))
    import make_icon
    if make_icon.main([str(BUILD / "pingerplot.ico")]) != 0:
        raise SystemExit("could not write the icon")


def pyinstaller() -> None:
    subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "pingerplot.spec"),
                    "--noconfirm", "--clean", "--distpath", str(DIST),
                    "--workpath", str(BUILD / "pyinstaller")], check=True, cwd=ROOT)
    for exe in EXES:
        if not (APP_DIR / exe).is_file():
            raise SystemExit(f"PyInstaller produced no {exe}")
    if (APP_DIR / PORTABLE_MARKER).exists():
        raise SystemExit("portable.txt must not be in the installer's folder")


def portable_zip() -> Path:
    """The program folder, under a top folder of its own, plus portable.txt."""
    out = DIST / PORTABLE_NAME
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(APP_DIR.rglob("*")):
            if p.is_file():
                z.write(p, Path("PingerPlot") / p.relative_to(APP_DIR))
        z.writestr(f"PingerPlot/{PORTABLE_MARKER}", PORTABLE_NOTE)
        for doc in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
            z.write(ROOT / doc, f"PingerPlot/{doc}")
    return out


def find_iscc():
    for p in (os.environ.get("ISCC"),
              shutil.which("iscc"),
              Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
              Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
              Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"):
        if p and Path(p).is_file():
            return str(p)
    return None


def authenticode(path: Path) -> None:
    subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(AUTHENTICODE), str(path)],
                   check=True)


def installer(sign: bool = False):
    iscc = find_iscc()
    if not iscc:
        if sign:
            raise SystemExit("Inno Setup 6 not found: a signed build must include the installer")
        print("Inno Setup 6 not found (set ISCC, or install it); skipping the installer")
        return None
    args = [iscc, f"/DAppVersion={__version__}", "/Q"]
    if sign:
        # Inno Setup runs this for the uninstaller and Setup.exe: $f is the file (quoted), $q a
        # quote. No literal quotes in the argument: Python would escape them as \", which ISCC's
        # own command-line parsing doesn't undo.
        args += ["/DSign", f"/Sauthenticode=pwsh -NoProfile -NonInteractive -File $q{AUTHENTICODE}$q $f"]
    subprocess.run(args + [str(ROOT / "packaging" / "PingerPlot.iss")], check=True, cwd=ROOT)
    return DIST / INSTALLER_NAME


def checksums() -> None:
    lines = []
    for name in (INSTALLER_NAME, PORTABLE_NAME):
        p = DIST / name
        if p.is_file():
            lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {name}")
    (DIST / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="ascii")


def manifest():
    sys.path.insert(0, str(ROOT / "packaging"))
    import signing
    key = signing.key_file()
    if not key.is_file():
        print(f"no signing key at {key}; latest.json not written")
        return None
    return signing.sign(DIST, __version__, signing.seed_from_pem(key.read_text(encoding="ascii")))


def main(argv: list[str]) -> None:
    unknown = set(argv) - {"--program", "--package", "--sign", "--no-installer"}
    if unknown or {"--program", "--package"} <= set(argv):
        raise SystemExit(__doc__)
    sign = "--sign" in argv
    if sign and ("--package" not in argv or "--no-installer" in argv):
        raise SystemExit("--sign goes with --package, and a signed build includes the installer")

    if "--package" in argv:
        for exe in EXES:
            if not (APP_DIR / exe).is_file():
                raise SystemExit(f"--package needs the program folder: no {APP_DIR / exe}")
        for name in (PORTABLE_NAME, INSTALLER_NAME, "SHA256SUMS.txt", "latest.json"):
            (DIST / name).unlink(missing_ok=True)
        icon()                      # the installer's own icon
    else:
        shutil.rmtree(DIST, ignore_errors=True)
        version_resource()
        icon()
        pyinstaller()

    if "--program" not in argv:
        if sign:
            for exe in EXES:
                authenticode(APP_DIR / exe)
        print("zip:", portable_zip())
        if "--no-installer" not in argv:
            print("installer:", installer(sign))
        checksums()
        print("manifest:", manifest())
    for p in sorted(DIST.iterdir()):
        if p.is_file():
            print(f"  {p.name:28} {p.stat().st_size:>12,} bytes")


if __name__ == "__main__":
    main(sys.argv[1:])
