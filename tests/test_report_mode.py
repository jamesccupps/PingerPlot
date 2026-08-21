"""One-shot report mode: collect N rounds, print a table, exit.

The run-forever loop is the right shape for a service and the wrong shape for
everything else — writing up a ticket, a scheduled "is the path healthy at 6am"
job, or a quick before/after around a change. Those want a fixed amount of
evidence and an exit status.

No network here: monitors are stand-ins with pre-loaded samples.
"""
import csv as _csv

import pytest

from pingerplot import headless
from pingerplot.model import Hop
from pingerplot.monitor import Monitor


def _monitor(name="8.8.8.8", hops=(("10.0.0.1", 2.0), ("203.0.113.9", 20.0)),
             reached=True, rounds=5):
    m = Monitor()
    m.target_input = name
    m.target_ip = hops[-1][0] if hops else None
    m.reached_target = reached
    m.interval = 0.05
    m.timeout_ms = 100
    m._round = rounds
    for i, (addr, rtt) in enumerate(hops, start=1):
        h = Hop(i)
        for _ in range(10):
            h.record(rtt, addr, 0)
        h.hostname = f"host{i}.example"
        m._hops.append(h)
    m.route_len = len(hops)
    return m


def _target(m, name="8.8.8.8"):
    return headless.Target(name, m, {})


def _quiet(*_a, **_k):
    pass


# --- the data behind the table ---------------------------------------------

def test_rows_cover_every_hop():
    m = _monitor()
    try:
        rows = headless.report_rows(m)
        assert [r["hop"] for r in rows] == [1, 2]
        assert [r["ip"] for r in rows] == ["10.0.0.1", "203.0.113.9"]
    finally:
        m.shutdown()


def test_only_the_destination_gets_a_mos():
    """MOS describes the end-to-end path. On an intermediate router it would
    measure how eagerly that box answers ICMP, not how the path performs."""
    m = _monitor()
    try:
        rows = headless.report_rows(m)
        assert rows[0]["mos"] is None and rows[0]["is_dest"] is False
        assert rows[-1]["mos"] is not None and rows[-1]["is_dest"] is True
    finally:
        m.shutdown()


def test_rows_are_empty_when_nothing_answered():
    m = _monitor(hops=(), reached=False)
    try:
        assert headless.report_rows(m) == []
    finally:
        m.shutdown()


# --- the printed table -----------------------------------------------------

def test_the_table_names_the_target_and_every_hop():
    m = _monitor()
    try:
        text = "\n".join(headless.format_report("8.8.8.8", m))
        assert "8.8.8.8" in text
        assert "10.0.0.1" in text and "203.0.113.9" in text
        assert "host1.example" in text
    finally:
        m.shutdown()


def test_the_table_marks_the_destination_row():
    m = _monitor()
    try:
        lines = headless.format_report("t", m)
        # The header repeats the target IP, so match the hop rows specifically.
        rows = [ln for ln in lines if "host1.example" in ln or "host2.example" in ln]
        assert len(rows) == 2
        assert not rows[0].lstrip().startswith("*"), "hop 1 is not the destination"
        assert rows[1].lstrip().startswith("*"), "the destination row is unmarked"
    finally:
        m.shutdown()


def test_the_table_says_so_when_the_destination_never_answered():
    m = _monitor(reached=False)
    try:
        text = "\n".join(headless.format_report("t", m))
        assert "never answered" in text
    finally:
        m.shutdown()


def test_a_hop_with_no_data_prints_a_dash_not_a_crash():
    m = Monitor()
    m.target_input = "x"
    m._hops = [Hop(1)]            # no samples at all
    m.route_len = 1
    try:
        text = "\n".join(headless.format_report("x", m))
        assert "-" in text
    finally:
        m.shutdown()


def test_the_table_records_the_probe_mode_and_settings():
    """A report that does not say how it was produced cannot be compared with
    another one — the same reason the GUI export carries a header."""
    m = _monitor()
    m.packet_type = "tcp"
    m.port = 443
    try:
        text = "\n".join(headless.format_report("t", m))
        assert "TCP:443" in text
        assert "timeout" in text
    finally:
        m.shutdown()


# --- CSV -------------------------------------------------------------------

def test_csv_has_one_row_per_hop_per_target(tmp_path):
    a, b = _monitor("a"), _monitor("b", hops=(("10.0.0.9", 3.0),))
    path = tmp_path / "r.csv"
    try:
        headless.write_report_csv(str(path), [_target(a, "a"), _target(b, "b")])
        rows = list(_csv.DictReader(path.open(encoding="utf-8")))
        assert len(rows) == 3
        assert {r["target"] for r in rows} == {"a", "b"}
        assert rows[0]["hop"] == "1"
    finally:
        a.shutdown(); b.shutdown()


def test_csv_defangs_a_hostile_hostname(tmp_path):
    """A hop's PTR record is chosen by whoever runs that router, so it is
    attacker-influenced text landing in a spreadsheet."""
    m = _monitor()
    m._hops[0].hostname = "=cmd|'/c calc'!A1"
    path = tmp_path / "r.csv"
    try:
        headless.write_report_csv(str(path), [_target(m)])
        rows = list(_csv.DictReader(path.open(encoding="utf-8")))
        assert rows[0]["hostname"].startswith("'=")
    finally:
        m.shutdown()


# --- the runner ------------------------------------------------------------

def test_it_returns_when_every_target_has_its_rounds():
    m = _monitor(rounds=0)
    t = _target(m)
    m._round = 3          # already there
    try:
        rc = headless.run_report([t], rounds=3, log=_quiet, poll=0.01, deadline=5)
        assert rc == 0
    finally:
        m.shutdown()


def test_a_target_that_never_reached_gives_a_nonzero_exit():
    """So a scheduled job can branch on it."""
    m = _monitor(reached=False)
    m._round = 3
    try:
        rc = headless.run_report([_target(m)], rounds=3, log=_quiet,
                                 poll=0.01, deadline=5)
        assert rc == 1
    finally:
        m.shutdown()


def test_a_stalled_target_does_not_hang_the_report():
    """A name that will not resolve never advances a round. Without a deadline
    one bad hostname would hold the whole report open forever."""
    import time
    m = _monitor(reached=False)
    m._round = 0
    m.running = True
    try:
        t0 = time.monotonic()
        rc = headless.run_report([_target(m)], rounds=99, log=_quiet,
                                 poll=0.01, deadline=0.5)
        assert time.monotonic() - t0 < 3.0
        assert rc == 1
    finally:
        m.shutdown()


def test_a_short_report_still_prints_what_it_has():
    lines = []
    m = _monitor(reached=False)
    m._round = 1
    m.running = True
    try:
        headless.run_report([_target(m)], rounds=50, log=lines.append,
                            poll=0.01, deadline=0.3)
        text = "\n".join(lines)
        assert "short of 50 rounds" in text
        assert "10.0.0.1" in text, "partial data must still be reported"
    finally:
        m.shutdown()


# --- CLI -------------------------------------------------------------------

def test_report_rejects_a_nonsense_round_count(tmp_path, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text('{"targets": [{"target": "127.0.0.1"}]}', encoding="utf-8")
    rc = headless.main([str(cfg), "--report", "0"])
    assert rc == 2
    assert "at least 1" in capsys.readouterr().err


def test_the_flags_are_parsed():
    import argparse
    with pytest.raises(SystemExit):
        headless.main(["--help"])
