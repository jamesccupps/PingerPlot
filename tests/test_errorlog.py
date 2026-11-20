"""The error log (errorlog.py): what the installed app, which has no console,
keeps of errors and of starting and quitting."""
import logging
import sys
import threading

import pytest

from pingerplot import errorlog, history


def _read(path):
    return path.read_text(encoding="utf-8")


def test_notes_and_package_warnings_reach_the_file(tmp_path):
    path = errorlog.install("gui", tmp_path)
    assert path == tmp_path / "error.log"
    errorlog.note("PingerPlot 9.9.9 started as administrator")
    logging.getLogger("pingerplot.update").warning("update check failed: timed out")
    logging.getLogger("pingerplot.win").debug("too chatty for the file")
    text = _read(path)
    assert "INFO pingerplot.app: PingerPlot 9.9.9 started as administrator" in text
    assert "WARNING pingerplot.update: update check failed: timed out" in text
    assert "too chatty" not in text


def test_a_window_callbacks_exception_is_kept_with_its_traceback(tmp_path):
    path = errorlog.install("gui", tmp_path)
    try:
        {}["missing"]
    except KeyError:
        errorlog.tk_callback(*sys.exc_info())
    text = _read(path)
    assert "ERROR pingerplot: In the window:" in text and "KeyError: 'missing'" in text
    assert "test_a_window_callbacks_exception" in text, "the traceback, not just the message"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")   # passed on, as it should be
def test_an_uncaught_exception_in_a_thread_is_kept(tmp_path):
    path = errorlog.install("gui", tmp_path)

    def boom():
        raise RuntimeError("the writer died")

    t = threading.Thread(target=boom, name="history-writer")
    t.start()
    t.join()
    text = _read(path)
    assert "Uncaught in thread history-writer" in text and "RuntimeError: the writer died" in text


def test_a_webhooks_secret_never_reaches_the_file(tmp_path):
    path = errorlog.install("gui", tmp_path)
    logging.getLogger("pingerplot.monitor").warning(
        "webhook failed for https://hooks.example.com/services/T000/B000/SECRET?token=abc: 404")
    text = _read(path)
    assert "https://hooks.example.com/…" in text
    assert "SECRET" not in text and "token" not in text
    assert errorlog.scrub("see https://example.com and ntfy://ntfy.example.net/topic") == \
        "see https://example.com and ntfy://ntfy.example.net/…"


def test_it_wont_write_through_a_link(tmp_path, monkeypatch):
    """Run as administrator, a link planted in the settings folder would
    otherwise point its writes anywhere."""
    monkeypatch.setattr(history, "_is_link", lambda p: p.name == "error.log")
    path = errorlog.install("gui", tmp_path)
    errorlog.note("something")
    assert not path.exists()


def test_it_stays_small(tmp_path, monkeypatch):
    monkeypatch.setattr(errorlog, "MAX_BYTES", 2000)
    path = errorlog.install("gui", tmp_path)
    for i in range(200):
        errorlog.note(f"line {i} " + "x" * 40)
    assert path.stat().st_size <= 2000
    assert (tmp_path / "error.log.1").exists() and not (tmp_path / "error.log.2").exists()


def test_the_headless_runner_has_a_file_of_its_own(tmp_path):
    assert errorlog.install("headless", tmp_path) == tmp_path / "headless-error.log"


def test_uninstall_puts_the_hooks_back(tmp_path):
    before = (sys.excepthook, threading.excepthook)
    errorlog.install("gui", tmp_path)
    assert sys.excepthook is not before[0]
    errorlog.uninstall()
    assert (sys.excepthook, threading.excepthook) == before


def test_the_recorders_problems_are_logged(tmp_path, monkeypatch):
    """Why recording stopped or never started: the question nothing could
    answer after 2026-11-18."""
    path = errorlog.install("gui", tmp_path)
    monkeypatch.setattr(history, "_is_link", lambda p: p.name == "hist")
    rec = history.Recorder(tmp_path / "hist", program="gui").start()
    rec.close()
    assert "WARNING pingerplot.history: History recording is off:" in _read(path)


@pytest.mark.parametrize("why", ["quit", "closing to restart as administrator"])
def test_quitting_says_so(app, tmp_path, why):
    path = errorlog.install("gui", tmp_path)
    app._quit(why)
    assert f"PingerPlot {__import__('pingerplot').__version__}: {why}" in _read(path)
