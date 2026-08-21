"""The pieces the Windows build depends on.

The frozen binaries themselves are built and smoke-tested by the release
workflow on a Windows runner — that is the only place they can be. What is
testable here is everything the build stands on: the icon generator, the
``--version`` path that lets CI verify a windowed binary without a display, and
the spec and workflow actually being present and consistent.

The ``--version`` flag exists specifically because a windowed PyInstaller build
cannot otherwise be checked. The first hand-built exe died at import from an
over-eager ``--exclude-module email`` and reported it in a message box, which a
CI runner would never have seen. ``--version`` returns before Tk is touched but
after the whole import graph has been walked, so that failure becomes a
non-zero exit code instead.
"""
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from pingerplot import __version__

REPO = Path(__file__).resolve().parent.parent


# --- the --version escape hatch --------------------------------------------

def test_version_flag_prints_the_version_and_exits():
    out = subprocess.run([sys.executable, str(REPO / "main.py"), "--version"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert __version__ in out.stdout


def test_version_flag_does_not_need_a_display():
    """The whole point: it must return before Tk is created, so a headless CI
    runner can use it to prove a frozen binary's import graph."""
    src = (REPO / "pingerplot" / "gui.py").read_text(encoding="utf-8")
    body = src[src.index("def main() -> None:"):]
    version_branch = body[:body.index("_enable_windows_dpi_awareness()")]
    assert "--version" in version_branch
    assert "tk.Tk()" not in version_branch


def test_version_walks_the_import_graph_that_broke():
    """gui -> geoip -> urllib.request -> email. If that chain is ever severed,
    the CI gate stops meaning anything."""
    import pingerplot.gui  # noqa: F401
    assert "pingerplot.geoip" in sys.modules
    assert "urllib.request" in sys.modules
    assert "email" in sys.modules


# --- the icon --------------------------------------------------------------

def test_icon_is_a_valid_multi_size_ico():
    sys.path.insert(0, str(REPO / "packaging"))
    import make_icon

    data = make_icon.build_ico()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1), "not an ICO header"
    assert count == len(make_icon.SIZES)

    seen = []
    for i in range(count):
        w, h, _c, _r, planes, bpp, size, offset = struct.unpack(
            "<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        assert planes == 1 and bpp == 32
        assert offset + size <= len(data), "entry points past the end of the file"
        assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n", "not PNG-compressed"
        seen.append(w or 256)
    assert seen == list(make_icon.SIZES)


def test_icon_includes_the_sizes_windows_actually_asks_for():
    sys.path.insert(0, str(REPO / "packaging"))
    import make_icon
    # 16 for the title bar and taskbar, 32 for alt-tab, 256 for large icons in
    # Explorer. Missing one makes Windows scale another and it looks it.
    for needed in (16, 32, 256):
        assert needed in make_icon.SIZES


def test_icon_is_written_to_disk(tmp_path):
    sys.path.insert(0, str(REPO / "packaging"))
    import make_icon
    dest = tmp_path / "nested" / "app.ico"
    assert make_icon.main([str(dest)]) == 0
    assert dest.exists() and dest.stat().st_size > 1000


# --- the spec and workflow -------------------------------------------------

def test_the_spec_exists_and_is_tracked():
    """.gitignore carries a blanket *.spec for PyInstaller's generated ones.
    The authored spec must be exempted or the build silently has no recipe."""
    spec = REPO / "packaging" / "pingerplot.spec"
    assert spec.exists()
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "!packaging/*.spec" in ignore


def test_the_spec_does_not_exclude_anything_the_app_imports():
    """The regression that started all this. Excluding email broke
    urllib.request, which geoip and the webhook both need."""
    spec = (REPO / "packaging" / "pingerplot.spec").read_text(encoding="utf-8")
    dangerous = ("email", "urllib", "http", "json", "socket", "ctypes",
                 "tkinter", "select", "csv", "queue", "ipaddress", "zlib")
    for mod in dangerous:
        assert f'"{mod}"' not in spec.split("EXCLUDES")[1].split("]")[0], \
            f"the spec excludes {mod!r}, which the app imports"


def test_the_spec_builds_both_binaries():
    spec = (REPO / "packaging" / "pingerplot.spec").read_text(encoding="utf-8")
    assert 'name="PingerPlot"' in spec
    assert 'name="pingerplot-headless"' in spec
    assert "console=False" in spec and "console=True" in spec


def test_the_spec_does_not_upx_pack():
    """UPX packing is a large share of PyInstaller AV false positives, and this
    binary already looks suspicious to a heuristic scanner without help."""
    spec = (REPO / "packaging" / "pingerplot.spec").read_text(encoding="utf-8")
    assert spec.count("upx=False") == 2


def test_the_release_workflow_verifies_before_it_uploads():
    """An unverified upload is how a broken binary reaches people."""
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    smoke_gui = wf.index("Smoke-test the frozen GUI")
    smoke_cli = wf.index("Smoke-test the frozen headless")
    upload = wf.index("Attach to the release")
    assert smoke_gui < upload and smoke_cli < upload


def test_the_workflow_checks_the_gui_exit_code_correctly():
    """A GUI-subsystem exe does not set $LASTEXITCODE in PowerShell -- the
    variable keeps whatever the previous command left, so the check silently
    passes a broken build. Start-Process -Wait -PassThru is the correct form."""
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    gui_step = wf[wf.index("Smoke-test the frozen GUI"):wf.index("Smoke-test the frozen headless")]
    # Only the commands, not the comment that explains why $LASTEXITCODE is
    # wrong here -- naming the trap is the opposite of falling into it.
    code = "\n".join(ln for ln in gui_step.splitlines()
                     if not ln.strip().startswith("#"))
    assert "-Wait -PassThru" in code
    assert "$LASTEXITCODE" not in code


def test_the_workflow_publishes_checksums():
    """The binaries are unsigned, so a hash is the only integrity story."""
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "SHA256SUMS.txt" in wf
    assert "SHA256" in wf
