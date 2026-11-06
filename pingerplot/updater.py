"""In-app updates: a signed release manifest, a verified download, an install.

Each release carries ``latest.json``:

    {"payload": base64(JSON), "signature": base64(Ed25519 over the payload)}

    payload = {"app": "pingerplot", "version": "1.4.0", "published": "...",
               "installer": {"file": "PingerPlot-Setup.exe", "size": N, "sha256": "..."},
               "portable":  {"file": "PingerPlot-win64.zip", "size": N, "sha256": "..."}}

The app checks the signature against the public key built into it
(update_key.py), and only then trusts the sizes and hashes; a download that
doesn't match them is deleted. Nothing is downloaded without a click.

Installed copies (Setup.exe; Inno Setup's unins000.exe beside the program) run
the new installer silently, which waits for this copy to exit and starts the
new one. Portable copies unzip the new version to a temporary folder and start
*that* copy with --finish-update: it waits for this one to exit, swaps the
program files in place (never settings.json beside them), and starts the
updated app. If the swap fails, everything is put back and the old version is
started again. Running from source, it only says what's new.

What this reads from a release can never change, as every installed copy
reads it: the asset names, the manifest's shape, the tag ``vX.Y.Z``. It never
goes back a version, so a bad release is fixed by publishing a higher one.

Adapted from UniFi Stock Watcher (same author, same scheme), with urllib in
place of requests: PingerPlot has no third-party dependencies.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

from . import __version__, ed25519, settings
from .update_key import UPDATE_PUBLIC_KEY

log = logging.getLogger("pingerplot.update")

REPO = "jamesccupps/PingerPlot"
APP_KEY = "pingerplot"
MANIFEST_NAME = "latest.json"
INSTALLER_NAME = "PingerPlot-Setup.exe"
PORTABLE_NAME = "PingerPlot-win64.zip"
GUI_EXE = "PingerPlot.exe"
DEFAULT_SOURCE = f"https://github.com/{REPO}/releases/latest/download/{MANIFEST_NAME}"
RELEASES_URL = f"https://github.com/{REPO}/releases"
LOG_NAME = "update.log"

MAX_MANIFEST = 64 * 1024
MAX_DOWNLOAD = 300 * 1024 * 1024

_VERSION_RE = re.compile(r"^(\d{1,4})\.(\d{1,4})\.(\d{1,6})$")
STAGING_PREFIX = "PingerPlot-update-"       # + the version: a download's folder in %TEMP%
_STAGING_RE = re.compile(re.escape(STAGING_PREFIX) + r"\d{1,4}\.\d{1,4}\.\d{1,6}")
_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_GITHUB_LATEST = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/releases/latest/download/latest\.json$")


class UpdateError(Exception):
    """Anything that stops an update: a bad signature, a short download..."""


class RollbackIncomplete(UpdateError):
    """A swap failed and some of the old program couldn't be put back."""


# What a release puts beside the exe (the zip's top level, packaging/
# build_windows.py): the only names a swap renames to <name>.old, and so the
# only .old names cleanup may remove. A portable folder also holds the
# user's own files.
PROGRAM_FILES = (GUI_EXE, "pingerplot-headless.exe", "_internal", "portable.txt",
                 "LICENSE", "THIRD-PARTY-NOTICES.md")
SPARE_BYTES = 50 * 1024 * 1024


def parse_version(v):
    m = _VERSION_RE.match(v) if isinstance(v, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def update_source() -> str:
    """Where latest.json comes from: PINGERPLOT_UPDATE_URL (a URL or a folder,
    for testing and for anyone mirroring releases) or this repo's latest
    release."""
    return os.environ.get("PINGERPLOT_UPDATE_URL") or DEFAULT_SOURCE


def program_dir() -> Path:
    return Path(sys.executable).resolve().parent


def install_kind() -> str:
    """'source' (a checkout: main.py beside the package), 'pip' (installed
    with pip or pipx, the Linux way), 'installed' (Setup.exe), 'portable'
    (the zip), or 'folder' (a frozen build that is neither, e.g. copied by
    hand)."""
    if not getattr(sys, "frozen", False):
        here = Path(__file__).resolve().parent.parent
        return "source" if (here / "main.py").is_file() else "pip"
    if settings.portable_dir() is not None:
        return "portable"
    if (program_dir() / "unins000.exe").exists():
        return "installed"
    return "folder"


# --- the manifest -----------------------------------------------------------------

def _check_asset(asset, name):
    if asset is None:
        return None
    if not isinstance(asset, dict):
        raise UpdateError(f"{name}: not an object")
    f, size, sha = asset.get("file"), asset.get("size"), asset.get("sha256")
    # A bare file name: the manifest says what to fetch, never from where.
    if not (isinstance(f, str) and _FILE_RE.match(f)):
        raise UpdateError(f"{name}: bad file name {f!r}")
    if not (isinstance(size, int) and not isinstance(size, bool) and 0 < size <= MAX_DOWNLOAD):
        raise UpdateError(f"{name}: bad size {size!r}")
    if not (isinstance(sha, str) and _SHA_RE.match(sha)):
        raise UpdateError(f"{name}: bad sha256")
    return {"file": f, "size": size, "sha256": sha}


def verify_manifest(text: str, public_key: Optional[str] = None) -> dict:
    """The manifest's payload, if and only if it is signed by ``public_key``
    (base64 of the raw 32-byte key; the one built into the app by default)
    and well-formed."""
    public_key = public_key or UPDATE_PUBLIC_KEY
    try:
        outer = json.loads(text)
        payload = base64.b64decode(outer["payload"], validate=True)
        signature = base64.b64decode(outer["signature"], validate=True)
        key = base64.b64decode(public_key, validate=True)
    except (ValueError, KeyError, TypeError) as e:
        raise UpdateError(f"latest.json is malformed: {e}") from e
    if not ed25519.verify(key, payload, signature):
        raise UpdateError("latest.json isn't signed with the key this app trusts")
    try:
        m = json.loads(payload)
    except ValueError as e:
        raise UpdateError("latest.json's payload isn't JSON") from e
    if not isinstance(m, dict) or m.get("app") != APP_KEY:
        raise UpdateError("latest.json is for another app")
    if parse_version(m.get("version")) is None:
        raise UpdateError(f"latest.json has a bad version {m.get('version')!r}")
    return {
        "version": m["version"],
        "published": m.get("published") if isinstance(m.get("published"), str) else None,
        "installer": _check_asset(m.get("installer"), "installer"),
        "portable": _check_asset(m.get("portable"), "portable"),
    }


# --- fetching ------------------------------------------------------------------------

def _is_url(source: str) -> bool:
    return urlsplit(source).scheme in ("http", "https")


def asset_location(source: str, version: str, name: str) -> str:
    """Where a release file lives, next to its manifest.

    For GitHub's /releases/latest/ URL the file comes from that version's own
    tag instead: a release published between reading the manifest and the
    download would otherwise hand over the next version's file (refused by its
    hash, but a failed update nonetheless).
    """
    m = _GITHUB_LATEST.match(source)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}/releases/download/v{version}/{name}"
    if _is_url(source):
        return urljoin(source, name)
    p = Path(source)
    return str((p.parent if p.suffix.lower() == ".json" else p) / name)


def _open(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": f"PingerPlot/{__version__}"})
    return urllib.request.urlopen(req, timeout=timeout)


def _read_manifest(source: str) -> Optional[str]:
    if _is_url(source):
        try:
            with _open(source, 20) as r:
                data = r.read(MAX_MANIFEST + 1)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # The latest release has no latest.json: one from before 1.4,
                # or a release made by hand without it. Nothing to install.
                return None
            raise
    else:
        p = Path(source)
        if p.is_dir():
            p = p / MANIFEST_NAME
        with open(p, "rb") as fh:
            data = fh.read(MAX_MANIFEST + 1)
    if len(data) > MAX_MANIFEST:
        raise UpdateError("latest.json is too big")
    return data.decode("utf-8")


@dataclass
class Update:
    version: str
    published: Optional[str]
    kind: str                 # install_kind() at check time
    asset: Optional[dict]     # what this copy would download; None for source runs
    source: str

    @property
    def can_install(self) -> bool:
        return self.asset is not None


def check(current: str = __version__, source: Optional[str] = None) -> Optional[Update]:
    """The newer release, or None when this copy is up to date. Raises
    UpdateError (or an OSError from the network) when it can't tell."""
    source = source or update_source()
    text = _read_manifest(source)
    if text is None:
        log.info("no latest.json at %s; nothing to install", source)
        return None
    m = verify_manifest(text)
    if parse_version(m["version"]) <= (parse_version(current) or (0, 0, 0)):
        return None
    kind = install_kind()
    asset = {"installed": m["installer"], "portable": m["portable"], "folder": m["portable"]}.get(kind)
    return Update(m["version"], m["published"], kind, asset, source)


def download(update: Update, dest_dir: Optional[Path] = None,
             progress: Optional[Callable[[int, int], None]] = None) -> Path:
    """Fetch the update's file, check its size and SHA-256, return its path.
    progress(done_bytes, total_bytes) is called as it arrives."""
    if not update.can_install:
        raise UpdateError("nothing to download for this kind of copy")
    a = update.asset
    dest_dir = Path(dest_dir or Path(tempfile.gettempdir()) / f"{STAGING_PREFIX}{update.version}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    final, part = dest_dir / a["file"], dest_dir / (a["file"] + ".part")
    where = asset_location(update.source, update.version, a["file"])
    digest, done = hashlib.sha256(), 0
    try:
        src = _open(where, 60) if _is_url(where) else open(where, "rb")
        with src, open(part, "wb") as out:
            for chunk in iter(lambda: src.read(256 * 1024), b""):
                done += len(chunk)
                if done > a["size"]:
                    raise UpdateError(f"{a['file']} is bigger than latest.json says")
                digest.update(chunk)
                out.write(chunk)
                if progress:
                    progress(done, a["size"])
        if done != a["size"] or digest.hexdigest() != a["sha256"]:
            raise UpdateError(f"{a['file']} doesn't match latest.json "
                              f"({done} bytes, sha256 {digest.hexdigest()[:12]}…)")
        os.replace(part, final)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    log.info("%s %s downloaded and verified (%d bytes)", update.version, a["file"], done)
    return final


# --- installing ------------------------------------------------------------------------

_DETACHED = 0x00000008 | 0x00000200     # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def _spawn(args, cwd) -> subprocess.Popen:
    kwargs = {"cwd": str(cwd), "close_fds": True, "stdin": subprocess.DEVNULL,
              "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = _DETACHED
    return subprocess.Popen([str(a) for a in args], **kwargs)


def folder_is_writable(folder: Optional[Path] = None) -> bool:
    folder = Path(folder or program_dir())
    probe = folder / f".write-test-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def _safe_extract(zip_path: Path, dest: Path) -> None:
    dest = Path(dest).resolve()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if target != dest and dest not in target.parents:
                raise UpdateError(f"{info.filename}: outside the update folder")
        z.extractall(dest)


def stage(path: Path) -> Path:
    """Unzip a portable update and return the folder holding its exe."""
    staged = path.parent / "staged"
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)
    _safe_extract(path, staged)
    for exe in [staged / GUI_EXE, *staged.glob(f"*/{GUI_EXE}")]:
        if exe.is_file():
            return exe.parent
    raise UpdateError(f"{path.name} has no {GUI_EXE}")


def apply(update: Update, path: Path) -> None:
    """Start installing ``path``. The caller must exit straight away after."""
    if update.kind == "installed":
        log.info("starting the %s installer", update.version)
        _spawn([path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/relaunch=1"], cwd=path.parent)
        return
    if not folder_is_writable():
        raise UpdateError(f"{program_dir()} can't be written to. Move PingerPlot to a folder of "
                          "your own, or download the new version by hand.")
    staged = stage(path)
    log.info("starting the staged %s to swap it in", update.version)
    # The helper's working folder is the staging area's parent, never the
    # program folder, which it is about to rename.
    _spawn([staged / GUI_EXE, "--finish-update", program_dir(), os.getpid()], cwd=staged.parent)


def ulog(msg: str, path: Optional[Path] = None) -> None:
    path = path or settings.config_dir() / LOG_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _wait_for_exit(pid: int, timeout: float = 60) -> bool:
    if sys.platform != "win32":
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            time.sleep(0.2)
        return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x00100000, False, pid)          # SYNCHRONIZE
    if not h:
        return True                                       # already gone
    try:
        return k32.WaitForSingleObject(h, int(timeout * 1000)) == 0
    finally:
        k32.CloseHandle(h)


def _retry(fn, tries: int = 40, delay: float = 0.25):
    for i in range(tries):
        try:
            return fn()
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(delay)


def _remove(p: Path) -> None:
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()


def swap_in(app_dir: Path, staged: Path) -> None:
    """Replace app_dir's program files with staged's. Data files stay put.

    Each item is renamed to <name>.old first and only then copied in; on any
    failure the copies are removed and the .old items renamed back.
    """
    app_dir, staged = Path(app_dir), Path(staged)
    names = [e.name for e in staged.iterdir()
             if not (e.name == settings.PORTABLE_MARKER
                     and not (app_dir / settings.PORTABLE_MARKER).exists())]
    # The old files stay beside the new ones until the new version has
    # started, so the new copy needs room of its own: a nearly full USB
    # stick is refused before anything is touched.
    need = sum(f.stat().st_size for n in names for f in
               ([staged / n] if (staged / n).is_file() else (staged / n).rglob("*"))
               if f.is_file())
    free = shutil.disk_usage(app_dir).free
    if free < need + SPARE_BYTES:
        raise UpdateError(f"not enough free space in {app_dir}: the update needs "
                          f"{(need + SPARE_BYTES) // 2**20} MB, there are {free // 2**20} MB")
    renamed, placed = [], []
    try:
        for n in names:
            target, old = app_dir / n, app_dir / (n + ".old")
            if old.exists():
                _retry(lambda: _remove(old))
            if target.exists():
                _retry(lambda: target.rename(old))
                renamed.append(n)
            src = staged / n
            # Recorded before the copy: a copy that fails half way leaves a
            # half-new folder, which the rollback has to remove too.
            placed.append(n)
            if src.is_dir():
                shutil.copytree(src, target)
            else:
                shutil.copy2(src, target)
    except BaseException as exc:
        stuck = []
        for n in placed:
            try:
                if (app_dir / n).exists():
                    _retry(lambda: _remove(app_dir / n), tries=10)
            except OSError:
                stuck.append(n)
        for n in renamed:
            try:
                _retry(lambda: (app_dir / (n + ".old")).rename(app_dir / n), tries=10)
            except OSError:
                stuck.append(n + ".old")
        if stuck and isinstance(exc, Exception):
            raise RollbackIncomplete(f"{exc!r}; couldn't put back: {', '.join(stuck)}") from exc
        raise


def finish_update(app_dir, pid) -> int:
    """Run by the staged copy: swap it into app_dir, then start it there.
    Returns the process exit code."""
    app_dir, staged = Path(app_dir), program_dir()
    # This copy runs from the staging folder, whose own portable.txt would
    # point its data folder there; the log belongs with the app's.
    logfile = (app_dir if (app_dir / settings.PORTABLE_MARKER).exists()
               else settings.config_dir()) / LOG_NAME
    ulog(f"update: {__version__} waiting for {pid} to exit", logfile)
    if not _wait_for_exit(int(pid)):
        ulog("update: the old copy didn't exit; giving up", logfile)
        return 1
    try:
        swap_in(app_dir, staged)
    except RollbackIncomplete as e:
        ulog(f"update: FAILED, and the old version couldn't be fully put back: {e}. "
             f"Download the release again from {RELEASES_URL}", logfile)
        _spawn([app_dir / GUI_EXE, "--update-failed"], cwd=app_dir)
        return 1
    except Exception as e:      # whatever went wrong, the old copy comes back
        ulog(f"update: failed, old version restored: {e!r}", logfile)
        _spawn([app_dir / GUI_EXE, "--update-failed"], cwd=app_dir)
        return 1
    ulog(f"update: done, starting {__version__}", logfile)
    _spawn([app_dir / GUI_EXE, "--updated"], cwd=app_dir)
    return 0


def cleanup_after_update(remove_old: bool = True) -> None:
    """Remove what a swap leaves behind: the program's own <name>.old beside
    it (only those: a portable folder holds the user's files too, and only
    after a successful update, when the .old items are the version being
    replaced, never a rollback's leftovers) and the staging folders in %TEMP%.
    Anything still locked is left for next time."""
    if remove_old and getattr(sys, "frozen", False):
        for name in PROGRAM_FILES:
            p = program_dir() / (name + ".old")
            try:
                if p.exists():
                    _remove(p)
            except OSError:
                pass
    # Exactly the folders download() makes. Not glob("PingerPlot-update-*"):
    # on Windows that matches without regard to case, and it once deleted a
    # test's own pingerplot-update-test-NNN folder, the updated copy in it.
    for p in Path(tempfile.gettempdir()).iterdir():
        if _STAGING_RE.fullmatch(p.name) and p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
