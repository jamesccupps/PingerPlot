"""The release-signing key and latest.json, the manifest the updater trusts.

    python packaging/signing.py new-key
        One-time setup. Writes a new Ed25519 private key to KEY_FILE (below)
        and its public half into pingerplot/update_key.py, which ships inside
        the app. Refuses to replace an existing key.

    python packaging/signing.py sign [DIST] [VERSION]
        Writes DIST/latest.json (default dist/, pingerplot.__version__):
        the size and SHA-256 of PingerPlot-Setup.exe and PingerPlot-win64.zip,
        signed with the private key.

    python packaging/signing.py verify [DIST] [VERSION]
        Checks DIST/latest.json the way installed copies will: its signature
        against the public key built into the app, then every file it lists.
        Exits non-zero on any mismatch, so a release signed with the wrong key
        is stopped before it is published rather than by users' updaters.

The private key is read from the file named by PINGERPLOT_SIGNING_KEY, or
%USERPROFILE%\\.pingerplot\\update-signing-key.pem. It never goes in the
repository. CI gets it from the UPDATE_SIGNING_KEY repository secret (the same
PEM text), in a job that installs nothing (see release.yml).

Back the key up. Lose it and installed copies can no longer update in place:
they would need one manual install of a build carrying a new public key.

Standard library only: the key is a PKCS#8 PEM (what ``openssl genpkey
-algorithm ed25519`` writes, and what ``openssl pkey -pubout`` reads), and
pingerplot/ed25519.py signs.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pingerplot import __version__, ed25519, updater  # noqa: E402

KEY_ENV = "PINGERPLOT_SIGNING_KEY"
DEFAULT_KEY = Path.home() / ".pingerplot" / "update-signing-key.pem"
PUBLIC_FILE = ROOT / "pingerplot" / "update_key.py"

# PKCS#8 for Ed25519 is a fixed 16-byte DER header and the 32-byte seed
# (RFC 8410, section 7): version 0, algorithm id-Ed25519, the seed as an
# OCTET STRING inside an OCTET STRING.
_PKCS8_PREFIX = bytes.fromhex("302e020100300506032b657004220420")


def key_file() -> Path:
    return Path(os.environ.get(KEY_ENV) or DEFAULT_KEY)


def pem_from_seed(seed: bytes) -> str:
    b64 = base64.b64encode(_PKCS8_PREFIX + seed).decode("ascii")
    return f"-----BEGIN PRIVATE KEY-----\n{b64}\n-----END PRIVATE KEY-----\n"


def seed_from_pem(text: str) -> bytes:
    lines = [ln.strip() for ln in text.strip().splitlines()]
    if not lines or lines[0] != "-----BEGIN PRIVATE KEY-----" \
            or lines[-1] != "-----END PRIVATE KEY-----":
        raise ValueError("not a PKCS#8 PEM private key")
    der = base64.b64decode("".join(lines[1:-1]), validate=True)
    if len(der) != 48 or not der.startswith(_PKCS8_PREFIX):
        raise ValueError("not an unencrypted Ed25519 private key")
    return der[16:]


def public_key_b64(seed: bytes) -> str:
    return base64.b64encode(ed25519.public_key(seed)).decode("ascii")


def write_public_key(public_b64: str, path: Path = PUBLIC_FILE) -> None:
    path.write_bytes((
        '"""Public half of the release-signing key (packaging/signing.py new-key).\n\n'
        "The updater installs only releases whose latest.json is signed by the\n"
        "matching private key, which lives on the maintainer's PC and in the\n"
        'repository secret UPDATE_SIGNING_KEY, never in the repository.\n"""\n\n'
        f'UPDATE_PUBLIC_KEY = "{public_b64}"\n').encode("utf-8"))


def new_key(path: Path) -> str:
    """Create the private key file (never replacing one) and return the
    public key, base64."""
    seed = os.urandom(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii", newline="\n") as fh:
        fh.write(pem_from_seed(seed))
    return public_key_b64(seed)


def _describe(path: Path):
    if not path.is_file():
        return None
    data = path.read_bytes()
    return {"file": path.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def manifest(dist: Path, version: str, seed: bytes, published: str | None = None) -> str:
    """latest.json's text for the release files in ``dist``."""
    payload = {"app": updater.APP_KEY, "version": version,
               "published": published or datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for key, name in (("installer", updater.INSTALLER_NAME), ("portable", updater.PORTABLE_NAME)):
        described = _describe(Path(dist) / name)
        if described:
            payload[key] = described
    if "installer" not in payload and "portable" not in payload:
        raise SystemExit(f"neither {updater.INSTALLER_NAME} nor {updater.PORTABLE_NAME} is in {dist}")
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return json.dumps({"payload": base64.b64encode(raw).decode("ascii"),
                       "signature": base64.b64encode(ed25519.sign(seed, raw)).decode("ascii")},
                      indent=2) + "\n"


def sign(dist: Path, version: str, seed: bytes) -> Path:
    out = Path(dist) / updater.MANIFEST_NAME
    out.write_bytes(manifest(dist, version, seed).encode("utf-8"))
    return out


def verify(dist: Path, expected: str | None = None, public_key: str | None = None) -> list[str]:
    """What verify prints, line by line. Raises UpdateError or SystemExit."""
    dist = Path(dist)
    m = updater.verify_manifest((dist / updater.MANIFEST_NAME).read_text(encoding="utf-8"),
                                public_key)
    if expected and m["version"] != expected:
        raise SystemExit(f"latest.json says {m['version']}, expected {expected}")
    out = []
    for kind in ("installer", "portable"):
        a = m[kind]
        if a is None:
            raise SystemExit(f"latest.json lists no {kind}")
        data = (dist / a["file"]).read_bytes()
        if len(data) != a["size"] or hashlib.sha256(data).hexdigest() != a["sha256"]:
            raise SystemExit(f"{a['file']}: size or SHA-256 doesn't match latest.json")
        out.append(f"ok  {a['file']}  {len(data)} bytes  sha256 {a['sha256']}")
    out.append(f"ok  latest.json  version {m['version']}, signed by the key this app trusts")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("new-key")
    for name in ("sign", "verify"):
        p = sub.add_parser(name)
        p.add_argument("dist", nargs="?", default=str(ROOT / "dist"))
        p.add_argument("version", nargs="?", default=None)
    a = ap.parse_args(argv)

    if a.cmd == "new-key":
        path = key_file()
        if path.exists():
            raise SystemExit(f"{path} already exists; refusing to replace it. Releases signed "
                             "with a new key can't update copies that trust the old one.")
        write_public_key(new_key(path))
        print(f"private key: {path}  (back this up; keep it out of the repository)")
        print(f"public key:  {PUBLIC_FILE}")
        return 0
    version = (a.version or __version__).lstrip("v")
    if a.cmd == "sign":
        seed = seed_from_pem(key_file().read_text(encoding="ascii"))
        print("signed:", sign(Path(a.dist), version, seed))
        return 0
    try:
        for line in verify(Path(a.dist), version if a.version else None):
            print(line)
    except updater.UpdateError as e:
        raise SystemExit(f"latest.json: {e}") from e
    return 0


if __name__ == "__main__":
    sys.exit(main())
