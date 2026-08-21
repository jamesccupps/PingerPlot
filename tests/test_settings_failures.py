"""settings.save() promises it never raises. It did.

It caught OSError only, but json.dump raises TypeError for a value it cannot
encode and ValueError for a circular reference. The GUI calls save() from
_on_close, so one unencodable value meant the app threw on the way out — after
the window had already been asked to close, which is about the least
recoverable moment there is.

Not reachable today (every value written is a str, bool or list of str), which
is exactly why it needs pinning: the settings dict is the sort of thing that
grows a new key later.
"""
import json

import pytest

from pingerplot import settings


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Never touch the real %APPDATA%\\PingerPlot during a test run."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_roundtrip_still_works(_isolated_config):
    assert settings.save({"theme": "dark", "targets": ["8.8.8.8"]}) is True
    assert settings.load() == {"theme": "dark", "targets": ["8.8.8.8"]}


@pytest.mark.parametrize("bad", [
    {"targets": {"8.8.8.8"}},        # a set — the easy mistake
    {"when": object()},
    {"n": b"\x00bytes"},
])
def test_unencodable_values_return_false_instead_of_raising(bad):
    assert settings.save(bad) is False


def test_circular_reference_returns_false():
    d = {"a": 1}
    d["self"] = d
    assert settings.save(d) is False


def test_a_failed_save_leaves_no_temp_file(_isolated_config):
    """The write goes to settings.json.tmp and is renamed into place. A failure
    partway through used to leave the .tmp behind for good."""
    settings.save({"targets": {1, 2}})
    leftovers = list(settings.config_dir().glob("*.tmp"))
    assert leftovers == [], f"stale temp file(s): {leftovers}"


def test_a_failed_save_does_not_damage_the_previous_settings(_isolated_config):
    """os.replace is atomic, so a bad save must be a no-op — not a way to lose
    the theme and target list you already had."""
    assert settings.save({"theme": "light", "targets": ["1.1.1.1"]}) is True
    assert settings.save({"theme": "dark", "targets": {1, 2}}) is False
    assert settings.load() == {"theme": "light", "targets": ["1.1.1.1"]}


def test_load_survives_a_corrupt_file(_isolated_config):
    settings.config_dir().mkdir(parents=True, exist_ok=True)
    settings.config_path().write_text("{not json", encoding="utf-8")
    assert settings.load() == {}


def test_load_rejects_a_non_object_document(_isolated_config):
    settings.config_dir().mkdir(parents=True, exist_ok=True)
    settings.config_path().write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert settings.load() == {}
