"""On Linux, "Match the system" follows the desktop's light/dark preference
through the freedesktop settings portal (GNOME, KDE Plasma, ...), read with
gdbus. Before, it had nothing to ask and was always dark."""
import subprocess

import pytest

from pingerplot import winplatform


class Ran:
    def __init__(self, stdout):
        self.stdout = stdout


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(winplatform.sys, "platform", "linux")
    monkeypatch.setattr(winplatform.shutil, "which", lambda name: "/usr/bin/gdbus")
    calls = []

    def answer(out):
        def run(args, **kw):
            calls.append(args)
            if isinstance(out, Exception):
                raise out
            return Ran(out)
        monkeypatch.setattr(winplatform.subprocess, "run", run)
    return answer, calls


@pytest.mark.parametrize("out,dark", [
    ("(<<uint32 1>>,)\n", True),        # Settings.Read wraps the value twice
    ("(<uint32 2>,)\n", False),          # ReadOne, once
    ("(<<uint32 0>>,)\n", None),         # no preference: the app's default
    ("", None),                          # no portal on the bus
])
def test_the_portal_answer(linux, out, dark):
    answer, calls = linux
    answer(out)
    assert winplatform.system_prefers_dark() is dark
    assert calls[0][0] == "/usr/bin/gdbus" and "org.freedesktop.appearance" in calls[0]


@pytest.mark.parametrize("err", [subprocess.TimeoutExpired("gdbus", 2), OSError("no such file")])
def test_a_portal_that_fails_means_no_preference(linux, err):
    answer, _calls = linux
    answer(err)
    assert winplatform.system_prefers_dark() is None


def test_no_gdbus_means_no_preference(monkeypatch):
    monkeypatch.setattr(winplatform.sys, "platform", "linux")
    monkeypatch.setattr(winplatform.shutil, "which", lambda name: None)
    assert winplatform.system_prefers_dark() is None


def test_the_label_names_the_right_system():
    assert winplatform.THEME_LABEL == ("Match Windows" if winplatform.IS_WINDOWS else "Match the system")
