"""The signed updater: manifest rules, verified downloads, the portable swap,
and the release-signing tool. Offline: releases are folders in tmp_path, and
signing is ed25519.py's (tests/test_ed25519.py checks it against the RFC).
"""
import base64
import hashlib
import io
import json
import os
import sys
import urllib.error
import zipfile
from pathlib import Path

import pytest

from pingerplot import ed25519, updater

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packaging"))
import signing  # noqa: E402

sys.path.pop(0)


@pytest.fixture
def seed(monkeypatch):
    """A throwaway signing key, trusted by the updater for this test."""
    s = os.urandom(32)
    monkeypatch.setattr(updater, "UPDATE_PUBLIC_KEY", signing.public_key_b64(s))
    return s


def sign(seed, payload):
    raw = json.dumps(payload).encode()
    return json.dumps({"payload": base64.b64encode(raw).decode(),
                       "signature": base64.b64encode(ed25519.sign(seed, raw)).decode()})


@pytest.fixture
def release(tmp_path, seed):
    """A 9.9.9 release folder: Setup.exe, the zip (the program folder with a
    portable marker) and latest.json, signed by packaging/signing.py."""
    rel = tmp_path / "release"
    rel.mkdir()
    (rel / updater.INSTALLER_NAME).write_bytes(b"MZ fake installer " * 100)
    with zipfile.ZipFile(rel / updater.PORTABLE_NAME, "w") as z:
        z.writestr(f"PingerPlot/{updater.GUI_EXE}", b"new exe")
        z.writestr("PingerPlot/pingerplot-headless.exe", b"new headless")
        z.writestr("PingerPlot/_internal/lib.dll", b"new lib")
        z.writestr("PingerPlot/portable.txt", b"portable")
    signing.sign(rel, "9.9.9", seed)
    return rel


# --- the manifest ----------------------------------------------------------------

def test_verify_manifest_accepts_a_signed_one(release):
    m = updater.verify_manifest((release / "latest.json").read_text())
    assert m["version"] == "9.9.9"
    assert m["portable"]["file"] == updater.PORTABLE_NAME
    assert m["installer"]["size"] == (release / updater.INSTALLER_NAME).stat().st_size


def test_verify_manifest_rejects_another_key(release, monkeypatch):
    monkeypatch.setattr(updater, "UPDATE_PUBLIC_KEY", signing.public_key_b64(os.urandom(32)))
    with pytest.raises(updater.UpdateError, match="isn't signed"):
        updater.verify_manifest((release / "latest.json").read_text())


def test_verify_manifest_rejects_a_tampered_payload(release):
    outer = json.loads((release / "latest.json").read_text())
    payload = json.loads(base64.b64decode(outer["payload"]))
    payload["installer"]["sha256"] = "0" * 64
    outer["payload"] = base64.b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(updater.UpdateError, match="isn't signed"):
        updater.verify_manifest(json.dumps(outer))


@pytest.mark.parametrize("change", [
    {"app": "unifi-stock-watcher"},
    {"version": "3.0"}, {"version": "v3.0.0"}, {"version": "3.0.0-beta"}, {"version": 3},
    {"installer": {"file": "../evil.exe", "size": 1, "sha256": "0" * 64}},
    {"installer": {"file": "https://x/evil.exe", "size": 1, "sha256": "0" * 64}},
    {"installer": {"file": "ok.exe", "size": 0, "sha256": "0" * 64}},
    {"installer": {"file": "ok.exe", "size": True, "sha256": "0" * 64}},
    {"installer": {"file": "ok.exe", "size": updater.MAX_DOWNLOAD + 1, "sha256": "0" * 64}},
    {"installer": {"file": "ok.exe", "size": 1, "sha256": "zz"}},
    {"installer": "not an object"},
])
def test_a_signed_but_malformed_manifest_is_refused(seed, change):
    """Signed is necessary, not sufficient: the manifest is for this app, names
    bare files of a bounded size, and has a plain X.Y.Z version."""
    payload = {"app": updater.APP_KEY, "version": "9.9.9"}
    payload.update(change)
    with pytest.raises(updater.UpdateError):
        updater.verify_manifest(sign(seed, payload))


@pytest.mark.parametrize("text", ["", "{}", "not json", "[]",
                                  '{"payload": "!!", "signature": "x"}'])
def test_garbage_is_an_update_error(seed, text):
    with pytest.raises(updater.UpdateError):
        updater.verify_manifest(text)


def test_versions():
    assert updater.parse_version("1.10.2") > updater.parse_version("1.9.9")
    assert updater.parse_version("1.2") is None
    assert updater.parse_version(None) is None


# --- checking -----------------------------------------------------------------------

@pytest.mark.parametrize("kind,asset", [("installed", "installer"), ("portable", "portable"),
                                        ("folder", "portable"), ("source", None)])
def test_check_offers_the_right_file(release, monkeypatch, kind, asset):
    monkeypatch.setattr(updater, "install_kind", lambda: kind)
    u = updater.check(current="1.4.0", source=str(release))
    assert u.version == "9.9.9" and u.kind == kind
    assert (u.asset["file"] if u.asset else None) == (
        {"installer": updater.INSTALLER_NAME, "portable": updater.PORTABLE_NAME}.get(asset))
    assert u.can_install is (asset is not None)


@pytest.mark.parametrize("current", ["9.9.9", "10.0.0"])
def test_check_finds_nothing_when_current(release, current):
    assert updater.check(current=current, source=str(release)) is None


def test_check_reads_a_manifest_path_too(release):
    assert updater.check(current="1.0.0", source=str(release / "latest.json")).version == "9.9.9"


def test_a_huge_manifest_is_refused(tmp_path, seed):
    (tmp_path / "latest.json").write_bytes(b" " * (updater.MAX_MANIFEST + 1))
    with pytest.raises(updater.UpdateError, match="too big"):
        updater.check(current="1.0.0", source=str(tmp_path))


def test_a_release_without_latest_json_means_up_to_date(monkeypatch):
    """GitHub's latest release can be one from before 1.4 (no latest.json): a
    404 for the manifest is "nothing to install", not an error to show."""
    def not_found(url, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())
    monkeypatch.setattr(updater, "_open", not_found)
    assert updater.check(current="1.4.0", source=updater.DEFAULT_SOURCE) is None


def test_other_http_errors_are_raised(monkeypatch):
    def server_error(url, timeout):
        raise urllib.error.HTTPError(url, 503, "Unavailable", {}, io.BytesIO())
    monkeypatch.setattr(updater, "_open", server_error)
    with pytest.raises(urllib.error.HTTPError):
        updater.check(current="1.4.0", source=updater.DEFAULT_SOURCE)


def test_the_update_source_can_be_overridden(monkeypatch):
    monkeypatch.delenv("PINGERPLOT_UPDATE_URL", raising=False)
    assert updater.update_source() == updater.DEFAULT_SOURCE
    monkeypatch.setenv("PINGERPLOT_UPDATE_URL", "https://mirror.example/pp/latest.json")
    assert updater.update_source() == "https://mirror.example/pp/latest.json"


def test_asset_location_pins_github_downloads_to_the_tag():
    """A release published between reading latest.json and the download must
    not hand over the next version's file."""
    loc = updater.asset_location(updater.DEFAULT_SOURCE, "1.4.0", updater.INSTALLER_NAME)
    assert loc == ("https://github.com/jamesccupps/PingerPlot/releases/download/"
                   f"v1.4.0/{updater.INSTALLER_NAME}")
    assert updater.asset_location("https://mirror.example/u/latest.json", "1.4.0", "a.zip") \
        == "https://mirror.example/u/a.zip"
    assert updater.asset_location(str(Path("rel") / "latest.json"), "1.4.0", "a.zip") \
        == str(Path("rel") / "a.zip")


# --- downloading ---------------------------------------------------------------------

def test_download_verifies_and_keeps_the_file(release, tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "install_kind", lambda: "installed")
    u = updater.check(current="1.4.0", source=str(release))
    seen = []
    path = updater.download(u, dest_dir=tmp_path / "dl", progress=lambda d, t: seen.append(d))
    assert path.read_bytes() == (release / updater.INSTALLER_NAME).read_bytes()
    assert seen and seen[-1] == u.asset["size"]
    assert not list((tmp_path / "dl").glob("*.part"))


@pytest.mark.parametrize("damage", ["append", "flip", "truncate"])
def test_a_download_that_doesnt_match_is_deleted(release, tmp_path, monkeypatch, damage):
    monkeypatch.setattr(updater, "install_kind", lambda: "installed")
    u = updater.check(current="1.4.0", source=str(release))
    f = release / updater.INSTALLER_NAME
    data = bytearray(f.read_bytes())
    if damage == "append":
        data += b"extra"
    elif damage == "flip":
        data[5] ^= 0xFF
    else:
        del data[-1]
    f.write_bytes(bytes(data))
    with pytest.raises(updater.UpdateError):
        updater.download(u, dest_dir=tmp_path / "dl")
    assert list((tmp_path / "dl").iterdir()) == []


def test_a_source_copy_has_nothing_to_download(release, monkeypatch):
    monkeypatch.setattr(updater, "install_kind", lambda: "source")
    u = updater.check(current="1.4.0", source=str(release))
    with pytest.raises(updater.UpdateError):
        updater.download(u)


def test_staging_rejects_a_path_outside_the_folder(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../escaped.txt", b"x")
    with pytest.raises(updater.UpdateError):
        updater._safe_extract(z, tmp_path / "out")
    assert not (tmp_path / "escaped.txt").exists()


def test_stage_finds_the_exe_in_its_top_folder(release, tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "install_kind", lambda: "portable")
    u = updater.check(current="1.4.0", source=str(release))
    staged = updater.stage(updater.download(u, dest_dir=tmp_path / "dl"))
    assert (staged / updater.GUI_EXE).read_bytes() == b"new exe"
    assert staged.name == "PingerPlot"


def test_stage_refuses_a_zip_without_the_exe(tmp_path):
    z = tmp_path / "dl" / "x.zip"
    z.parent.mkdir()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("PingerPlot/readme.txt", b"x")
    with pytest.raises(updater.UpdateError, match="has no"):
        updater.stage(z)


# --- the portable swap ------------------------------------------------------------------

def _app_folder(tmp_path, portable=True):
    app = tmp_path / "app"
    (app / "_internal").mkdir(parents=True)
    (app / updater.GUI_EXE).write_bytes(b"old exe")
    (app / "_internal" / "lib.dll").write_bytes(b"old lib")
    (app / "settings.json").write_text('{"targets": ["dns.example.net"]}')
    if portable:
        (app / "portable.txt").write_text("portable")
    staged = tmp_path / "staged"
    (staged / "_internal").mkdir(parents=True)
    (staged / updater.GUI_EXE).write_bytes(b"new exe")
    (staged / "pingerplot-headless.exe").write_bytes(b"new headless")
    (staged / "_internal" / "lib.dll").write_bytes(b"new lib")
    (staged / "portable.txt").write_text("portable")
    return app, staged


def test_swap_replaces_program_files_and_keeps_settings(tmp_path):
    app, staged = _app_folder(tmp_path)
    updater.swap_in(app, staged)
    assert (app / updater.GUI_EXE).read_bytes() == b"new exe"
    assert (app / "pingerplot-headless.exe").read_bytes() == b"new headless"
    assert (app / "_internal" / "lib.dll").read_bytes() == b"new lib"
    assert (app / "settings.json").read_text() == '{"targets": ["dns.example.net"]}'
    assert (app / (updater.GUI_EXE + ".old")).exists()       # removed by the new app


def test_swap_never_makes_a_folder_copy_portable(tmp_path):
    """The zip carries portable.txt; copied into a copy that keeps settings in
    %APPDATA%, it would silently move the user to an empty settings folder."""
    app, staged = _app_folder(tmp_path, portable=False)
    updater.swap_in(app, staged)
    assert not (app / "portable.txt").exists()


def test_a_failed_swap_puts_everything_back(tmp_path, monkeypatch):
    app, staged = _app_folder(tmp_path)
    real = updater.shutil.copy2

    def fail_on_exe(src, dst, *a, **k):
        if str(dst).endswith(updater.GUI_EXE):
            raise OSError("in use")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(updater.shutil, "copy2", fail_on_exe)
    monkeypatch.setattr(updater, "_retry", lambda fn, tries=40, delay=0.25: fn())
    with pytest.raises(OSError):
        updater.swap_in(app, staged)
    assert (app / updater.GUI_EXE).read_bytes() == b"old exe"
    assert (app / "_internal" / "lib.dll").read_bytes() == b"old lib"
    assert not (app / "pingerplot-headless.exe").exists()
    assert not list(app.glob("*.old"))


def test_finish_update_logs_and_restarts(tmp_path, monkeypatch):
    app, staged = _app_folder(tmp_path)
    spawned = []
    monkeypatch.setattr(updater, "program_dir", lambda: staged)
    monkeypatch.setattr(updater, "_wait_for_exit", lambda pid, timeout=60: True)
    monkeypatch.setattr(updater, "_spawn", lambda args, cwd: spawned.append(args))
    assert updater.finish_update(str(app), "123") == 0
    assert spawned[-1] == [app / updater.GUI_EXE, "--updated"]
    assert "update: done" in (app / "update.log").read_text(encoding="utf-8")


def test_finish_update_restarts_the_old_version_on_failure(tmp_path, monkeypatch):
    app, staged = _app_folder(tmp_path)
    spawned = []
    monkeypatch.setattr(updater, "program_dir", lambda: staged)
    monkeypatch.setattr(updater, "_wait_for_exit", lambda pid, timeout=60: True)
    monkeypatch.setattr(updater, "_spawn", lambda args, cwd: spawned.append(args))
    monkeypatch.setattr(updater, "swap_in", lambda a, s: (_ for _ in ()).throw(OSError("locked")))
    assert updater.finish_update(str(app), "123") == 1
    assert spawned[-1][-1] == "--update-failed"
    assert "old version restored" in (app / "update.log").read_text(encoding="utf-8")


def test_finish_update_gives_up_if_the_old_copy_stays(tmp_path, monkeypatch):
    app, staged = _app_folder(tmp_path)
    spawned = []
    monkeypatch.setattr(updater, "program_dir", lambda: staged)
    monkeypatch.setattr(updater, "_wait_for_exit", lambda pid, timeout=60: False)
    monkeypatch.setattr(updater, "_spawn", lambda args, cwd: spawned.append(args))
    assert updater.finish_update(str(app), "123") == 1
    assert spawned == [] and (app / updater.GUI_EXE).read_bytes() == b"old exe"


def test_apply_refuses_an_unwritable_folder(release, tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "install_kind", lambda: "portable")
    u = updater.check(current="1.4.0", source=str(release))
    path = updater.download(u, dest_dir=tmp_path / "dl")
    monkeypatch.setattr(updater, "folder_is_writable", lambda folder=None: False)
    with pytest.raises(updater.UpdateError, match="can't be written"):
        updater.apply(u, path)


def test_apply_starts_the_staged_copy_for_a_portable_update(release, tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "install_kind", lambda: "portable")
    monkeypatch.setattr(updater, "program_dir", lambda: tmp_path / "app")
    monkeypatch.setattr(updater, "folder_is_writable", lambda folder=None: True)
    u = updater.check(current="1.4.0", source=str(release))
    path = updater.download(u, dest_dir=tmp_path / "dl")
    spawned = []
    monkeypatch.setattr(updater, "_spawn", lambda args, cwd: spawned.append((args, cwd)))
    updater.apply(u, path)
    (args, cwd), = spawned
    assert args[0] == tmp_path / "dl" / "staged" / "PingerPlot" / updater.GUI_EXE
    assert args[1:3] == ["--finish-update", tmp_path / "app"] and args[3] == os.getpid()
    assert cwd == tmp_path / "dl" / "staged"     # never the folder it renames


def test_apply_runs_the_installer_silently(release, tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "install_kind", lambda: "installed")
    u = updater.check(current="1.4.0", source=str(release))
    path = updater.download(u, dest_dir=tmp_path / "dl")
    spawned = []
    monkeypatch.setattr(updater, "_spawn", lambda args, cwd: spawned.append(args))
    updater.apply(u, path)
    assert spawned[0][0] == path
    assert {"/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/relaunch=1"} <= set(spawned[0])


def test_install_kind(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert updater.install_kind() == "source"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / updater.GUI_EXE))
    assert updater.install_kind() == "folder"
    (tmp_path / "unins000.exe").write_bytes(b"")
    assert updater.install_kind() == "installed"
    (tmp_path / "portable.txt").write_text("")
    assert updater.install_kind() == "portable"


def test_cleanup_removes_what_a_swap_leaves(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / updater.GUI_EXE))
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    (tmp_path / "app" / "_internal.old").mkdir(parents=True)
    (tmp_path / "app" / (updater.GUI_EXE + ".old")).write_bytes(b"")
    (tmp_path / "app" / "settings.json").write_text("{}")
    (tmp_path / "temp" / "PingerPlot-update-9.9.9" / "staged").mkdir(parents=True)
    updater.cleanup_after_update()
    assert sorted(p.name for p in (tmp_path / "app").iterdir()) == ["settings.json"]
    assert list((tmp_path / "temp").iterdir()) == []


# --- the shipped key and the signing tool -------------------------------------------------

def test_the_shipped_public_key_is_a_real_key():
    from pingerplot.update_key import UPDATE_PUBLIC_KEY
    raw = base64.b64decode(UPDATE_PUBLIC_KEY, validate=True)
    assert len(raw) == 32 and ed25519._decompress(raw) is not None


def test_pem_roundtrip():
    s = os.urandom(32)
    pem = signing.pem_from_seed(s)
    assert pem.startswith("-----BEGIN PRIVATE KEY-----\n")
    assert signing.seed_from_pem(pem) == s
    assert signing.seed_from_pem(pem.replace("\n", "\r\n") + "\n") == s


@pytest.mark.parametrize("text", ["", "hello", "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----",
                                  "-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----"])
def test_a_bad_key_file_is_refused(text):
    with pytest.raises(ValueError):
        signing.seed_from_pem(text)


def test_the_pem_is_what_openssl_and_cryptography_read():
    """RFC 8410's PKCS#8 encoding, as the cryptography package writes it, when
    that package is installed."""
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    s = os.urandom(32)
    theirs = Ed25519PrivateKey.from_private_bytes(s).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    assert signing.pem_from_seed(s) == theirs


def test_new_key_never_replaces_one(tmp_path):
    path = tmp_path / "k" / "key.pem"
    pub = signing.new_key(path)
    assert signing.public_key_b64(signing.seed_from_pem(path.read_text())) == pub
    with pytest.raises(FileExistsError):
        signing.new_key(path)


def test_write_public_key_is_importable(tmp_path):
    out = tmp_path / "update_key.py"
    signing.write_public_key("abc=", out)
    ns = {}
    exec(out.read_text(encoding="utf-8"), ns)
    assert ns["UPDATE_PUBLIC_KEY"] == "abc="


def test_verify_accepts_a_good_release(release, seed):
    lines = signing.verify(release, "9.9.9")
    assert lines[-1].endswith("signed by the key this app trusts")


def test_verify_catches_the_wrong_key(release):
    with pytest.raises(updater.UpdateError):
        signing.verify(release, public_key=signing.public_key_b64(os.urandom(32)))


def test_verify_catches_a_wrong_version_and_a_changed_file(release, seed):
    with pytest.raises(SystemExit, match="expected 9.9.8"):
        signing.verify(release, "9.9.8")
    (release / updater.PORTABLE_NAME).write_bytes(b"swapped")
    with pytest.raises(SystemExit, match="doesn't match"):
        signing.verify(release)


def test_sign_needs_a_release_file(tmp_path, seed):
    with pytest.raises(SystemExit, match="neither"):
        signing.sign(tmp_path, "9.9.9", seed)


def test_the_payload_hashes_the_files(release):
    payload = json.loads(base64.b64decode(
        json.loads((release / "latest.json").read_text())["payload"]))
    data = (release / updater.INSTALLER_NAME).read_bytes()
    assert payload["installer"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert payload["app"] == "pingerplot" and payload["published"].endswith("+00:00")
