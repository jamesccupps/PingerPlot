"""Make the helper modules in this directory importable from the test files.

pytest runs with ``--import-mode=importlib``, which deliberately does *not* put
a test file's own directory on sys.path â€” so ``import mock_router`` would fail
without this. conftest.py is always imported first, which makes it the right
place for it.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gui_harness import (  # noqa: E402,F401
    app, isolated_settings, load_session, tk_root,
)


@pytest.fixture(autouse=True)
def _never_the_real_settings_folder(tmp_path, monkeypatch):
    """Every test gets a throwaway settings folder, and so does any process
    it starts (they inherit the environment). The headless tests ran main()
    without isolated_settings, in process and as pythonw, and recorded their
    127.0.0.1 runs into the real history of whoever ran the suite."""
    monkeypatch.delenv("PINGERPLOT_DATA_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def _no_error_log_left_behind():
    """A test that installs the error log (headless.main does) mustn't leave
    its handler or exception hooks for the next."""
    yield
    from pingerplot import errorlog
    errorlog.uninstall()
