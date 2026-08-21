"""A byte-order mark must not make a config unreadable.

Found while smoke-testing a frozen build: writing the headless config with
PowerShell 5.1's `Set-Content -Encoding utf8` produced a file the app refused
outright —

    Cannot read config monitor.json: Unexpected UTF-8 BOM
    (decode using utf-8-sig): line 1 column 1 (char 0)

— and the service exited 1. That is a Windows-first tool rejecting a file
written by the shell that ships with Windows. The file is valid JSON, opens
correctly in every editor, and looks identical to a working one; the only
difference is three invisible leading bytes.

Reading as ``utf-8-sig`` strips a BOM when present and is byte-for-byte
identical to ``utf-8`` when it is not, so it is the right choice everywhere the
app reads a file a person may have produced. Writes stay plain ``utf-8`` — the
app should not emit a BOM of its own.
"""
import json

import pytest

from pingerplot import compare, headless, settings

BOM = b"\xef\xbb\xbf"


def _write(path, text, bom: bool):
    path.write_bytes((BOM if bom else b"") + text.encode("utf-8"))
    return path


# --- the headless config, where this was found -----------------------------

@pytest.mark.parametrize("bom", [True, False])
def test_a_config_is_read_with_or_without_a_bom(tmp_path, bom, capsys):
    cfg = _write(tmp_path / "monitor.json",
                 json.dumps({"targets": [{"target": "127.0.0.1"}]}), bom)
    # --report 0 is rejected on its own merits (exit 2), which proves the config
    # itself parsed: a config that failed to load exits 1 before reaching that.
    assert headless.main([str(cfg), "--report", "0"]) == 2
    assert "Cannot read config" not in capsys.readouterr().err


def test_a_bommed_config_no_longer_reports_an_encoding_error(tmp_path, capsys):
    """The exact regression, phrased the way the user would have seen it."""
    cfg = _write(tmp_path / "monitor.json",
                 json.dumps({"targets": [{"target": "127.0.0.1"}]}), bom=True)
    headless.main([str(cfg), "--report", "0"])
    err = capsys.readouterr().err
    assert "BOM" not in err and "utf-8-sig" not in err


def test_genuinely_broken_json_is_still_rejected(tmp_path, capsys):
    """Tolerating a BOM must not turn into tolerating anything."""
    cfg = _write(tmp_path / "monitor.json", "{not json at all", bom=True)
    assert headless.main([str(cfg), "--report", "1"]) == 1
    assert "Cannot read config" in capsys.readouterr().err


def test_the_starter_config_is_written_without_a_bom(tmp_path):
    """We read BOMs; we do not write them."""
    cfg = tmp_path / "starter.json"
    assert headless.main([str(cfg), "--init"]) == 0
    assert not cfg.read_bytes().startswith(BOM)


# --- the other files a person can hand us ----------------------------------

@pytest.mark.parametrize("bom", [True, False])
def test_a_baseline_session_is_read_either_way(tmp_path, bom):
    session = {"target_input": "8.8.8.8", "hops": [
        {"ttl": 1, "address": "10.0.0.1", "hostname": "", "samples": [[1.0, 5.0]]}]}
    path = _write(tmp_path / "base.json", json.dumps(session), bom)
    lines = []
    headless.print_comparison([], str(path), log=lines.append)
    assert "Cannot read baseline" not in "\n".join(lines)


def test_a_baseline_that_really_is_unreadable_still_says_so(tmp_path):
    path = _write(tmp_path / "base.json", "}{", bom=True)
    lines = []
    headless.print_comparison([], str(path), log=lines.append)
    assert "Cannot read baseline" in "\n".join(lines)


@pytest.mark.parametrize("bom", [True, False])
def test_settings_survive_a_bom(tmp_path, monkeypatch, bom):
    """settings.json is plain JSON in a discoverable location, and people edit
    it. A BOM there used to silently reset every preference to default, because
    load() swallows the decode error and returns {}."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings.config_dir().mkdir(parents=True, exist_ok=True)
    _write(settings.config_path(), json.dumps({"theme": "light"}), bom)
    assert settings.load() == {"theme": "light"}


def test_settings_are_written_without_a_bom(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert settings.save({"theme": "dark"}) is True
    assert not settings.config_path().read_bytes().startswith(BOM)


# --- and the comparison layer that consumes them ---------------------------

def test_session_stats_parse_from_a_bommed_file(tmp_path):
    session = {"hops": [{"ttl": 1, "address": "10.0.0.1", "samples": [[1.0, 5.0]]}]}
    path = _write(tmp_path / "s.json", json.dumps(session), bom=True)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    assert [s.ttl for s in compare.stats_from_session(data)] == [1]
