"""Tests for persisted settings (pure stdlib; config dir redirected to tmp)."""
from pingerplot import settings


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "config_dir", lambda: tmp_path / "PingerPlot")
    data = {"theme": "light", "interval": "2.5", "final_hop_only": True,
            "targets": ["8.8.8.8", "1.1.1.1"]}
    assert settings.save(data) is True
    assert settings.load() == data


def test_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "config_dir", lambda: tmp_path / "does-not-exist")
    assert settings.load() == {}


def test_corrupt_json_returns_empty(tmp_path, monkeypatch):
    d = tmp_path / "PingerPlot"
    monkeypatch.setattr(settings, "config_dir", lambda: d)
    d.mkdir(parents=True)
    (d / "settings.json").write_text("{ not valid json", encoding="utf-8")
    assert settings.load() == {}


def test_non_dict_json_returns_empty(tmp_path, monkeypatch):
    d = tmp_path / "PingerPlot"
    monkeypatch.setattr(settings, "config_dir", lambda: d)
    d.mkdir(parents=True)
    (d / "settings.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert settings.load() == {}


def test_save_is_atomic_no_partial_on_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "config_dir", lambda: tmp_path / "PingerPlot")
    settings.save({"a": 1})
    settings.save({"b": 2})            # overwrite
    assert settings.load() == {"b": 2}
    assert not (tmp_path / "PingerPlot" / "settings.json.tmp").exists()
