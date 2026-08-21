"""Writing to the probe log after it has been closed must not raise.

stop() joins the worker with a bounded timeout (reply timeout + 2 s) and then
closes the log regardless. If the worker outlived the join — a slow probe, a
long TCP timeout, a stalled DNS lookup — it can still be inside _log_probe,
which captured the handle before the close.

The write then hits a closed file, and Python raises ValueError, not OSError:

    ValueError: I/O operation on closed file.

_log_probe caught OSError only, so it escaped through _apply_probe and
_probe_round to _run's catch-all and turned into "Monitor error: ValueError(...)"
in the status bar — a confusing message for what is really a benign shutdown
race, and it aborts the round it happened in.

_rotate_log_if_needed has the same shape: fh.tell() on a closed file raises
ValueError too.
"""
from pingerplot import icmp, monitor
from pingerplot.monitor import Monitor

OK = icmp.PingResult(icmp.IP_SUCCESS, 5.0, "1.2.3.4", True)


def _monitor_with_log(tmp_path):
    m = Monitor()
    m.log_path = str(tmp_path / "probe.csv")
    m._open_log()
    return m


def test_write_after_close_is_swallowed(tmp_path):
    """Reproduces the race exactly: the handle is closed while _log_fh still
    points at it, which is the window between stop()'s close and the worker's
    next write."""
    m = _monitor_with_log(tmp_path)
    try:
        m._log_fh.close()          # stop() closed it underneath the worker
        m._log_probe(1, OK)        # must not raise
    finally:
        m.shutdown()


def test_rotation_check_after_close_is_swallowed(tmp_path):
    m = _monitor_with_log(tmp_path)
    try:
        m._log_fh.close()
        m._rotate_log_if_needed()  # fh.tell() on a closed file
    finally:
        m.shutdown()


def test_the_probe_that_hit_the_race_does_not_abort_the_round(tmp_path):
    """The point of catching it: _apply_probe must still record the sample.
    Losing the log line is acceptable; losing the measurement is not."""
    m = _monitor_with_log(tmp_path)
    try:
        m.running = True
        m._generation = 1
        m._maybe_resolve = lambda *a: None
        m._log_fh.close()
        m._apply_probe(1, OK, gen=1)
        assert len(m._hops) == 1 and m._hops[0].sent == 1
        assert m._hops[0].current == 5.0
    finally:
        m.shutdown()


def test_normal_logging_still_works(tmp_path):
    m = _monitor_with_log(tmp_path)
    try:
        m._log_probe(1, OK)
        m._log_probe(2, icmp.PingResult(icmp.IP_REQ_TIMED_OUT, None, None, False))
        m._close_log()
        lines = (tmp_path / "probe.csv").read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith("epoch,iso_time,round,hop,ip,rtt_ms,status")
        assert len(lines) == 3
        assert lines[1].split(",")[4] == "1.2.3.4"
        assert lines[2].split(",")[5] == ""      # a lost probe logs no latency
    finally:
        m.shutdown()


def test_rotation_still_happens_when_the_handle_is_healthy(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "MAX_LOG_BYTES", 200)
    m = _monitor_with_log(tmp_path)
    try:
        for _ in range(60):
            m._log_probe(1, OK)
        m._close_log()
        assert (tmp_path / "probe.1.csv").exists()
    finally:
        m.shutdown()
