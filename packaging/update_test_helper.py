"""The Python half of packaging/test_update.ps1, which updates a copy of
PingerPlot to this build for real.

    python packaging/update_test_helper.py previous
        The newest published release older than this build that has an
        updater (a latest.json asset), as X.Y.Z, or nothing. GITHUB_TOKEN, if
        set, goes to the API.

    python packaging/update_test_helper.py sign RELEASE_DIR VERSION
        Write RELEASE_DIR/latest.json for VERSION, signed with a new
        throwaway key, and print the key's public half. Never the release
        key, so the folder is harmless if it's left behind.

    python packaging/update_test_helper.py drive OLD_SOURCE RELEASE_DIR KIND APP_DIR OLD_PID PUBLIC_KEY [DATA_DIR]
        Run the old copy's own updater (its source, OLD_SOURCE: the previous
        release's, or this tree's when the build updates itself) the way its
        window does when Install is clicked: check RELEASE_DIR, download,
        verify, apply. Then end OLD_PID, the old copy that is running, as the
        app ends itself after apply. KIND and APP_DIR are what the frozen old
        copy would find for itself; PUBLIC_KEY stands in for the one built
        into it. The copy the update starts finds its settings in DATA_DIR
        (installed) or beside it (portable).

Adapted from UniFi Stock Watcher's scripts/update_test_helper.py.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _version(text):
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def _get_json(req, tries=4, wait=10):
    """GitHub's answer as JSON, tried again (further apart each time) on no
    answer, a 5xx or a 429; anything else, or the last try, raises."""
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:      # before URLError, its base class
            if (e.code < 500 and e.code != 429) or attempt == tries:
                raise
            why = f"HTTP {e.code}"
        except urllib.error.URLError as e:
            if attempt == tries:
                raise
            why = str(e.reason)
        print(f"GitHub's API failed ({why}); trying again in {wait * attempt} s", file=sys.stderr)
        time.sleep(wait * attempt)


def previous():
    """Prints the version, or nothing when there's no older release with an
    updater. Fails (non-zero) if GitHub can't be asked: printing nothing then
    would quietly turn the cross-version test into the build updating itself."""
    sys.path.insert(0, str(ROOT))
    from pingerplot import __version__
    from pingerplot.updater import REPO
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases?per_page=50",
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "PingerPlot-update-test"})
    if os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
    releases = _get_json(req)
    current = _version(__version__)
    older = [(_version(r["tag_name"]), r["tag_name"][1:]) for r in releases
             if not r.get("draft") and not r.get("prerelease")
             and _version(r["tag_name"]) and _version(r["tag_name"]) < current
             # Releases before 1.4.0 have no in-app updater to test.
             and any(a.get("name") == "latest.json" for a in r.get("assets", []))]
    if older:
        print(max(older)[1])


def sign(release_dir, version):
    sys.path.insert(0, str(ROOT / "packaging"))
    import signing
    seed = os.urandom(32)
    signing.sign(Path(release_dir), version, seed)
    print(signing.public_key_b64(seed))


def drive(old_source, release_dir, kind, app_dir, old_pid, public_key, data_dir=None):
    # The old code's own settings folder is a scratch one, so nothing real is
    # read or written; and only the old code is importable, never this
    # build's (unless they are the same tree).
    scratch = tempfile.mkdtemp(prefix="pingerplot-old-updater-")
    atexit.register(shutil.rmtree, scratch, True)
    os.environ["PINGERPLOT_DATA_DIR"] = scratch
    sys.path[0] = str(Path(old_source).resolve())
    from pingerplot import __version__, updater
    print(f"the {__version__} updater, as a {kind} copy in {app_dir}")
    updater.UPDATE_PUBLIC_KEY = public_key
    updater.install_kind = lambda: kind
    updater.program_dir = lambda: Path(app_dir)
    u = updater.check(source=release_dir)
    if u is None:
        raise SystemExit(f"the {__version__} updater found nothing newer in {release_dir}")
    print(f"found {u.version}: {u.asset['file']}, {u.asset['size']:,} bytes")
    path = updater.download(u)
    print(f"downloaded and verified: {path}")
    if data_dir:
        os.environ["PINGERPLOT_DATA_DIR"] = data_dir
    else:
        del os.environ["PINGERPLOT_DATA_DIR"]
    updater.apply(u, path)
    print("applied; ending the old copy")
    os.kill(int(old_pid), 9)                    # TerminateProcess on Windows
    if not updater._wait_for_exit(int(old_pid), timeout=30):
        raise SystemExit(f"the old copy ({old_pid}) didn't exit")


if __name__ == "__main__":
    command, args = sys.argv[1], sys.argv[2:]
    {"previous": previous, "sign": sign, "drive": drive}[command](*args)
