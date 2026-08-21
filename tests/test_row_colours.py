"""Row colouring: red means a problem, amber means a warning.

BAD_MS was tested one branch too late:

    if v.loss_pct > 25:                                     return "bad"
    if v.loss_pct > 0 or (v.avg is not None and v.avg >= BAD_MS):  return "warn"
    if v.avg is not None and v.avg >= WARN_MS:              return "warn"

Any average clearing BAD_MS (250 ms) also clears WARN_MS (120 ms), and both
branches returned "warn", so the BAD_MS test could never change the outcome. It
was dead: latency alone never turned a row red however bad it got, which
contradicts BAD_MS's own comment ("at/above this it is treated as a problem")
and means a 900 ms path looked exactly as alarming as a 130 ms one.

Importing gui.py needs tkinter present. It is on Windows, the platform this app
targets and one half of the CI matrix; the skip is recorded rather than silent
so a run where it does not execute is visible in the summary.
"""
import pytest

pytest.importorskip("tkinter", reason="gui.py imports tkinter at module level")

from pingerplot.gui import BAD_MS, WARN_MS, App          # noqa: E402
from pingerplot.model import HopView                     # noqa: E402


def _hop(avg=None, loss=0.0, ttl=1):
    return HopView(ttl=ttl, address="10.0.0.1", hostname=None, last_status=0,
                   sent=10, received=10, loss_pct=loss, current=avg, avg=avg,
                   best=avg, worst=avg, jitter=0.0)


def tag(avg=None, loss=0.0, is_dest=False):
    return App._row_tag(_hop(avg, loss), is_dest)


# --- latency ---------------------------------------------------------------

def test_thresholds_are_ordered():
    assert 0 < WARN_MS < BAD_MS


@pytest.mark.parametrize("avg", [0.0, 1.0, 50.0, WARN_MS - 0.1])
def test_low_latency_is_not_flagged(avg):
    assert tag(avg=avg) == "ok"


@pytest.mark.parametrize("avg", [WARN_MS, WARN_MS + 1, BAD_MS - 0.1])
def test_latency_at_or_over_the_warn_threshold_is_amber(avg):
    assert tag(avg=avg) == "warn"


@pytest.mark.parametrize("avg", [BAD_MS, BAD_MS + 1, 400.0, 2000.0])
def test_latency_at_or_over_the_bad_threshold_is_red(avg):
    """The regression: these all used to come back amber."""
    assert tag(avg=avg) == "bad"


def test_a_very_slow_path_does_not_look_the_same_as_a_slightly_slow_one():
    assert tag(avg=WARN_MS + 10) != tag(avg=BAD_MS * 4)


# --- loss ------------------------------------------------------------------

@pytest.mark.parametrize("loss", [0.1, 5.0, 25.0])
def test_some_loss_is_amber(loss):
    assert tag(loss=loss) == "warn"


@pytest.mark.parametrize("loss", [25.1, 50.0, 100.0])
def test_heavy_loss_is_red(loss):
    assert tag(loss=loss) == "bad"


def test_loss_still_outranks_a_healthy_latency():
    assert tag(avg=1.0, loss=90.0) == "bad"


def test_either_signal_alone_is_enough_for_red():
    assert tag(avg=BAD_MS, loss=0.0) == "bad"
    assert tag(avg=1.0, loss=99.0) == "bad"


# --- the destination row ---------------------------------------------------

def test_a_healthy_destination_is_highlighted_not_flagged():
    assert tag(avg=10.0, is_dest=True) == "dest"


def test_a_problem_outranks_the_destination_highlight():
    assert tag(avg=BAD_MS, is_dest=True) == "bad"
    assert tag(avg=WARN_MS, is_dest=True) == "warn"


def test_a_hop_with_no_latency_yet_is_not_flagged():
    """avg is None before the first reply, and on a hop that never answers.
    Neither is evidence of a slow path."""
    assert tag(avg=None) == "ok"
    assert tag(avg=None, is_dest=True) == "dest"


# --- every tag the code can return must have a colour ----------------------

def test_every_tag_is_configured_in_both_palettes():
    from pingerplot.gui import DARK, LIGHT
    produced = {tag(avg=1.0), tag(avg=WARN_MS), tag(avg=BAD_MS),
                tag(loss=50.0), tag(avg=1.0, is_dest=True)}
    for name in produced:
        assert name in LIGHT and name in DARK, f"{name} has no colour"
