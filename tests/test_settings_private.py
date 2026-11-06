"""settings.json can hold webhook secrets: on Linux and macOS it's written
readable by its owner only."""
import os
import stat
import sys

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_settings_file_is_owner_only(isolated_settings):
    old = os.umask(0o022)
    try:
        assert isolated_settings.save({"webhook_url": "https://hooks.example.com/x/SECRET"})
    finally:
        os.umask(old)
    mode = stat.S_IMODE(os.stat(isolated_settings.config_path()).st_mode)
    assert mode == 0o600, oct(mode)


def test_save_still_round_trips(isolated_settings):
    assert isolated_settings.save({"theme": "dark"})
    assert isolated_settings.load()["theme"] == "dark"
