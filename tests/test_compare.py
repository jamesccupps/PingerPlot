"""Comparing a run against an earlier one.

The tool could always say what a path looks like now. It could not say whether
that differs from last Tuesday, which is the question people actually arrive
with: is it slower since the circuit cutover, did the firewall change make hop
4 start dropping.

The trap this module exists to avoid is conflating "this hop got worse" with
"this TTL is a different router now". Reporting "hop 4 got 80 ms slower" when
hop 4 is simply a different box would be the most misleading output it could
produce, and it is exactly what a naive by-position diff does.
"""
import pytest

from pingerplot import compare
from pingerplot.compare import (
    BETTER, GONE, NEW, REROUTED, SAME, WORSE, HopStats, Comparison,
)
from pingerplot.model import Hop
from pingerplot.monitor import Monitor


def hop(ttl, addr="10.0.0.1", loss=0.0, avg=10.0, jitter=1.0, sent=100):
    return HopStats(ttl=ttl, address=addr, hostname=None, sent=sent,
                    loss_pct=loss, avg=avg, jitter=jitter)


def one(base, now):
    """Compare a single-hop path and return the row."""
    return compare.compare([base], [now]).rows[0]


# --- verdicts --------------------------------------------------------------

def test_an_unchanged_hop_is_same():
    assert one(hop(1), hop(1)).verdict == SAME


def test_a_big_latency_rise_is_worse():
    assert one(hop(1, avg=10.0), hop(1, avg=40.0)).verdict == WORSE


def test_a_big_latency_drop_is_better():
    assert one(hop(1, avg=40.0), hop(1, avg=10.0)).verdict == BETTER


def test_a_small_absolute_move_is_noise():
    """1.0 -> 1.3 ms is a 30% rise and completely meaningless. Requiring the
    absolute threshold as well is what stops a LAN hop's jitter reading as a
    regression on every comparison."""
    assert one(hop(1, avg=1.0), hop(1, avg=1.3)).verdict == SAME


def test_a_small_relative_move_is_noise_on_a_slow_path():
    """6 ms on a 300 ms satellite path is nothing; the same 6 ms on a 2 ms LAN
    hop is a 4x regression. Hence relative AND absolute."""
    assert one(hop(1, avg=300.0), hop(1, avg=306.0)).verdict == SAME
    assert one(hop(1, avg=2.0), hop(1, avg=8.0)).verdict == WORSE


def test_new_loss_is_worse():
    assert one(hop(1, loss=0.0), hop(1, loss=30.0)).verdict == WORSE


def test_recovered_loss_is_better():
    assert one(hop(1, loss=30.0), hop(1, loss=0.0)).verdict == BETTER


def test_a_trivial_loss_wobble_is_noise():
    assert one(hop(1, loss=1.0), hop(1, loss=3.0)).verdict == SAME


def test_loss_outranks_latency():
    """A hop that started dropping packets AND got faster is not 'better'."""
    assert one(hop(1, loss=0.0, avg=50.0), hop(1, loss=40.0, avg=5.0)).verdict == WORSE


def test_a_hop_with_no_latency_either_side_is_not_a_regression():
    assert one(hop(1, avg=None), hop(1, avg=None)).verdict == SAME
    assert one(hop(1, avg=10.0), hop(1, avg=None)).verdict == SAME


# --- the trap --------------------------------------------------------------

def test_a_changed_address_is_rerouted_not_worse():
    """The whole point. A different router at the same TTL is not the same hop
    getting slower."""
    r = one(hop(1, addr="10.0.0.1", avg=5.0), hop(1, addr="10.0.0.9", avg=95.0))
    assert r.verdict == REROUTED


def test_a_rerouted_hop_reports_no_deltas():
    """Subtracting one router's latency from another's produces a number that
    means nothing, so it must not be shown at all."""
    r = one(hop(1, addr="10.0.0.1", avg=5.0, loss=0.0),
            hop(1, addr="10.0.0.9", avg=95.0, loss=50.0))
    assert r.d_avg is None and r.d_loss is None


def test_an_unresponsive_hop_gaining_an_address_is_not_a_reroute():
    """A hop that was '*' and now answers has not moved; we just know more."""
    assert one(hop(1, addr=None, avg=None), hop(1, addr="10.0.0.1", avg=5.0)).verdict == SAME


# --- route length changes --------------------------------------------------

def test_a_longer_route_marks_the_extra_hops_new():
    c = compare.compare([hop(1), hop(2)], [hop(1), hop(2), hop(3)])
    assert [r.verdict for r in c.rows] == [SAME, SAME, NEW]
    assert c.route_changed


def test_a_shorter_route_marks_the_missing_hops_gone():
    c = compare.compare([hop(1), hop(2), hop(3)], [hop(1), hop(2)])
    assert c.rows[2].verdict == GONE
    assert c.route_changed


def test_an_identical_route_is_not_flagged_as_changed():
    c = compare.compare([hop(1), hop(2)], [hop(1), hop(2)])
    assert not c.route_changed


def test_new_and_gone_hops_report_no_deltas():
    c = compare.compare([hop(1)], [hop(1), hop(2, avg=99.0)])
    assert c.rows[1].d_avg is None and c.rows[1].d_loss is None


# --- deltas ----------------------------------------------------------------

def test_deltas_are_signed_current_minus_baseline():
    r = one(hop(1, avg=10.0, loss=5.0), hop(1, avg=45.0, loss=25.0))
    assert r.d_avg == pytest.approx(35.0)
    assert r.d_loss == pytest.approx(20.0)


def test_an_improvement_reads_negative():
    r = one(hop(1, avg=45.0), hop(1, avg=10.0))
    assert r.d_avg < 0


# --- the summary line ------------------------------------------------------

def test_summary_says_nothing_happened_when_nothing_did():
    assert compare.compare([hop(1)], [hop(1)]).summary() == "no significant change"


def test_summary_counts_regressions_and_route_changes():
    base = [hop(1), hop(2, addr="10.0.0.2"), hop(3, avg=10.0)]
    now = [hop(1), hop(2, addr="10.0.0.99"), hop(3, avg=90.0), hop(4)]
    s = compare.compare(base, now).summary()
    assert "3 -> 4 hops" in s
    assert "rerouted" in s
    assert "1 worse" in s


def test_summary_reports_the_mos_move():
    base = [hop(1, avg=5.0, jitter=1.0, loss=0.0)]
    now = [hop(1, avg=400.0, jitter=80.0, loss=20.0)]
    s = compare.compare(base, now).summary()
    assert "MOS" in s and "->" in s


def test_regressions_and_improvements_are_listed_separately():
    c = compare.compare([hop(1, avg=10.0), hop(2, avg=90.0)],
                        [hop(1, avg=90.0), hop(2, avg=10.0)])
    assert [r.ttl for r in c.regressions] == [1]
    assert [r.ttl for r in c.improvements] == [2]


# --- reading a saved session ----------------------------------------------

def _session(hops):
    m = Monitor()
    m.target_input = "8.8.8.8"
    for ttl, addr, rtts in hops:
        h = Hop(ttl)
        for r in rtts:
            h.record(r, addr, 0 if r is not None else 11010)
        m._hops.append(h)
    m.route_len = len(hops)
    data = m.to_dict()
    m.shutdown()
    return data


def test_stats_come_back_out_of_a_saved_session():
    data = _session([(1, "10.0.0.1", [4.0, 6.0]), (2, "8.8.8.8", [20.0, 20.0])])
    stats = compare.stats_from_session(data)
    assert [s.ttl for s in stats] == [1, 2]
    assert stats[0].address == "10.0.0.1"
    assert stats[0].avg == pytest.approx(5.0)
    assert stats[1].loss_pct == 0.0


def test_loss_survives_the_round_trip():
    data = _session([(1, "10.0.0.1", [4.0, None, None, 4.0])])
    assert compare.stats_from_session(data)[0].loss_pct == pytest.approx(50.0)


def test_a_malformed_hop_is_skipped_not_fatal():
    """A session file may not be one of ours."""
    data = {"hops": [{"ttl": "not a number"}, {"no_ttl": 1},
                     {"ttl": 2, "address": "10.0.0.2", "samples": [[1.0, 5.0]]}]}
    stats = compare.stats_from_session(data)
    assert [s.ttl for s in stats] == [2]


def test_an_empty_session_yields_nothing():
    assert compare.stats_from_session({}) == []
    assert compare.stats_from_session({"hops": []}) == []


def test_a_live_monitor_can_be_compared_to_a_saved_one():
    """The end-to-end shape: save a session, degrade the path, compare."""
    base = compare.stats_from_session(
        _session([(1, "10.0.0.1", [2.0] * 20), (2, "8.8.8.8", [10.0] * 20)]))
    now = compare.stats_from_session(
        _session([(1, "10.0.0.1", [2.0] * 20), (2, "8.8.8.8", [95.0] * 20)]))
    c = compare.compare(base, now, target="8.8.8.8")
    assert [r.verdict for r in c.rows] == [SAME, WORSE]
    assert c.rows[1].d_avg == pytest.approx(85.0)


# --- the rendered table ----------------------------------------------------

def test_the_table_shows_every_hop_and_the_verdicts():
    c = compare.compare([hop(1, avg=10.0), hop(2, addr="10.0.0.2")],
                        [hop(1, avg=90.0), hop(2, addr="10.0.0.77")])
    text = "\n".join(compare.format_comparison(c, "last Tuesday"))
    assert "last Tuesday" in text
    assert "worse" in text
    assert "rerouted 10.0.0.2 -> 10.0.0.77" in text


def test_deltas_are_rendered_with_a_sign():
    c = compare.compare([hop(1, avg=10.0)], [hop(1, avg=90.0)])
    assert "+80.0" in "\n".join(compare.format_comparison(c))


def test_a_rerouted_row_shows_no_delta_number():
    c = compare.compare([hop(1, addr="10.0.0.1", avg=5.0)],
                        [hop(1, addr="10.0.0.9", avg=95.0)])
    text = "\n".join(compare.format_comparison(c))
    assert "+90.0" not in text and "-90.0" not in text


def test_an_empty_comparison_renders_without_crashing():
    assert compare.format_comparison(compare.compare([], []))


# --- console safety --------------------------------------------------------

class TestConsoleEncoding:
    """The table goes to stdout, and a Windows console still defaults to cp1252
    or cp437. A delta sign in the header raised UnicodeEncodeError and took the
    whole report with it — the same trap as an em dash in a CLI help string."""

    def _table(self):
        c = compare.compare(
            [hop(1, avg=10.0), hop(2, addr="10.0.0.2", loss=0.0)],
            [hop(1, avg=90.0), hop(2, addr="10.0.0.77", loss=40.0), hop(3)],
            target="8.8.8.8")
        return "\n".join(compare.format_comparison(c, "last Tuesday"))

    @pytest.mark.parametrize("encoding", ["utf-8", "cp1252", "cp437", "ascii", "latin-1"])
    def test_the_table_encodes_on_a_legacy_console(self, encoding):
        self._table().encode(encoding)

    def test_it_is_plain_ascii(self):
        text = self._table()
        bad = sorted({ch for ch in text if ord(ch) > 127})
        assert bad == [], f"non-ASCII in console output: {bad}"

    def test_the_summary_is_ascii_too(self):
        c = compare.compare([hop(1, avg=5.0, jitter=1.0)],
                            [hop(1, avg=400.0, jitter=80.0, loss=20.0)])
        c.summary().encode("cp1252")
