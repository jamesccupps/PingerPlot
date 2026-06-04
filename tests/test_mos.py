"""Tests for the MOS (Mean Opinion Score) estimate."""
from pingerplot.model import mos, mos_label


def test_no_latency_is_none():
    assert mos(None, None, 0.0) is None
    assert mos_label(None) == "—"


def test_clean_path_is_excellent():
    score = mos(20.0, 2.0, 0.0)
    assert score is not None
    assert score >= 4.3
    assert mos_label(score) == "Excellent"


def test_degraded_path_is_poor_or_worse():
    score = mos(300.0, 50.0, 10.0)
    assert score < 3.1
    assert mos_label(score) in ("Poor", "Bad")


def test_heavy_loss_bottoms_out():
    assert mos(50.0, 10.0, 50.0) == 1.0


def test_score_is_bounded():
    for avg in (0.0, 10.0, 80.0, 250.0, 1000.0):
        for jit in (0.0, 5.0, 100.0):
            for loss in (0.0, 1.0, 25.0, 100.0):
                s = mos(avg, jit, loss)
                assert 1.0 <= s <= 5.0


def test_label_thresholds():
    assert mos_label(4.4) == "Excellent"
    assert mos_label(4.1) == "Good"
    assert mos_label(3.7) == "Fair"
    assert mos_label(3.2) == "Poor"
    assert mos_label(2.0) == "Bad"
