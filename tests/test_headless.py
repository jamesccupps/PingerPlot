"""Tests for headless mode — config parsing and the non-network code paths
(the run loop itself needs real monitors and is exercised by a smoke run)."""
import json

from pingerplot import headless


def test_target_options_merges_defaults_and_overrides():
    defaults = {"interval": 2.5, "timeout_ms": 1000, "final_hop_only": False}
    target, kw = headless._target_options(
        defaults, {"target": "8.8.8.8", "final_hop_only": True, "interval": 1.0})
    assert target == "8.8.8.8"
    assert kw["timeout_ms"] == 1000        # inherited from defaults
    assert kw["interval"] == 1.0           # overridden by the target
    assert kw["final_hop_only"] is True    # overridden
    assert kw["alert_sound"] is False      # headless never beeps


def test_a_quoted_false_is_refused_not_taken_as_true():
    """bool("false") is True: the option used to switch on."""
    import pytest
    for key in ("final_hop_only", "resolve_names", "alert_enabled"):
        with pytest.raises(ValueError, match=key):
            headless._target_options({}, {"target": "192.0.2.1", key: "false"})
    _t, kw = headless._target_options({}, {"target": "192.0.2.1", "final_hop_only": 0,
                                           "alert_enabled": False})
    assert kw["final_hop_only"] is False and kw["alert_enabled"] is False


def test_a_status_interval_that_isnt_a_number_is_a_reason_not_a_traceback(tmp_path, capsys):
    cfg = tmp_path / "monitor.json"
    cfg.write_text(json.dumps({"status_interval": "often", "targets": [{"target": "192.0.2.1"}]}),
                   encoding="utf-8")
    assert headless.main([str(cfg)]) == 1
    assert "status_interval" in capsys.readouterr().err


def test_target_options_blank_target():
    target, _kw = headless._target_options({}, {"target": "   "})
    assert target == ""


def test_init_writes_a_valid_sample(tmp_path):
    cfg = tmp_path / "monitor.json"
    assert headless.main([str(cfg), "--init"]) == 0
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["targets"] and "defaults" in data
    for t in data["targets"]:                         # sample must map cleanly
        name, kw = headless._target_options(data["defaults"], t)
        assert name and "interval" in kw and "log_path" in kw


def test_init_refuses_to_overwrite(tmp_path):
    cfg = tmp_path / "monitor.json"
    cfg.write_text("{}", encoding="utf-8")
    assert headless.main([str(cfg), "--init"]) == 1


def test_main_rejects_missing_config(tmp_path):
    assert headless.main([str(tmp_path / "nope.json")]) == 1


def test_main_rejects_empty_targets(tmp_path):
    cfg = tmp_path / "monitor.json"
    cfg.write_text(json.dumps({"targets": []}), encoding="utf-8")
    assert headless.main([str(cfg)]) == 1
