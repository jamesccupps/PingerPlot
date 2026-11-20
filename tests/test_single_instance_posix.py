"""One copy at a time off Windows (the 2026-11-05 review, M11): autostart
plus a menu launch probed every target twice and fought over settings.json.
An flock on a file in the settings folder, and a Unix socket the first copy
listens on for a second launch's "show"."""
import threading

import pytest

from pingerplot import winplatform

pytestmark = pytest.mark.skipif(winplatform.IS_WINDOWS, reason="flock and Unix sockets")


@pytest.fixture
def no_runtime_dir(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)


def test_a_second_copy_sees_the_first_and_shows_its_window(tmp_path, no_runtime_dir):
    shown = threading.Event()
    first = winplatform.SingleInstance(folder=tmp_path)
    try:
        assert not first.already_running
        first.serve(shown.set)
        second = winplatform.SingleInstance(folder=tmp_path)
        try:
            assert second.already_running
            assert second.signal_existing()
            assert shown.wait(5), "the first copy wasn't asked to show its window"
        finally:
            second.release()
    finally:
        first.release()
    third = winplatform.SingleInstance(folder=tmp_path)
    try:
        assert not third.already_running, "the lock outlived its holder"
        assert not third.signal_existing(), "the socket outlived its holder"
    finally:
        third.release()


def test_a_crashed_copys_socket_is_replaced(tmp_path, no_runtime_dir):
    inst = winplatform.SingleInstance(folder=tmp_path)
    try:
        stale = inst._socket_path()
        stale.write_text("left behind")
        inst.serve(lambda: None)
        assert inst._server is not None
    finally:
        inst.release()


def test_the_socket_goes_in_the_runtime_folder_and_is_private(tmp_path, monkeypatch):
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    inst = winplatform.SingleInstance(folder=tmp_path / "cfg")
    try:
        inst.serve(lambda: None)
        path = inst._socket_path()
        assert path.parent == runtime and path.exists()
        assert path.stat().st_mode & 0o077 == 0
    finally:
        inst.release()


def test_copies_with_their_own_settings_folders_are_separate(tmp_path, no_runtime_dir):
    a = winplatform.SingleInstance(folder=tmp_path / "a")
    b = winplatform.SingleInstance(folder=tmp_path / "b")
    try:
        assert not a.already_running and not b.already_running
        assert a._socket_path() != b._socket_path()
    finally:
        a.release()
        b.release()
