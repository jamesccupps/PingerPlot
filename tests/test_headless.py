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
