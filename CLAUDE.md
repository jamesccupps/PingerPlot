# PingerPlot - notes for Claude Code

A continuous-traceroute network path monitor (MTR-style), Python standard library only: a Tkinter window
(`gui.py`), a headless runner for Task Scheduler (`headless.py`), and one engine under both (`monitor.py`). Shipped
for Windows as a per-user installer and a portable zip (PyInstaller onedir + Inno Setup), with signed in-app
updates. Every
round re-probes the whole path, so per-hop loss, latency, jitter and MOS accumulate over time and route changes are
seen live. ICMP needs no administrator rights on any platform (Windows: the IP Helper API; Linux/macOS: unprivileged
`SOCK_DGRAM` ICMP sockets); TCP/UDP traceroute needs Administrator on Windows (a raw `SIO_RCVALL` capture).

The repo is public on GitHub (`jamesccupps/PingerPlot`). It is a tool for mapping networks, so **no real addresses
in tracked files**: RFC 5737 (`192.0.2.x`, `198.51.100.x`, `203.0.113.x`) in docs and tests, enforced by
`tests/test_no_real_addresses.py`. Keep the README, CHANGELOG.md, `release-notes/`, ROADMAP.md and this file current
as work happens, and the repository's About text and topics too. Sibling projects with the conventions this one is
moving to: UniFi Stock Watcher (Python/Tk, the closest match), Slate (Rust), Protect Panes (Electron).

## Commands

```
python -m pytest                       # ~30 s; Windows, Linux (xvfb-run -a for the GUI tests) and macOS
python main.py                         # the app (real %APPDATA%\PingerPlot settings)
python -m pingerplot.headless cfg.json # headless; --init writes a starter config, --report N runs N rounds and exits
python -m pingerplot.selftest 8.8.8.8  # a quick trace in the console
python scripts/demo.py                 # the real app against a pretend network; --shots DIR takes the pictures
python -m pip install --require-hashes --only-binary=:all: -r packaging/requirements-build.txt
python packaging/build_windows.py      # dist\: PingerPlot\ (both exes), the zip, Setup.exe (Inno Setup 6), latest.json
pwsh packaging/test_portable.ps1       # unzip, --self-test, a headless loopback probe
pwsh packaging/test_update.ps1 -Kinds portable   # a copy updates itself to dist\ (installed: CI only, it replaces yours)
```

Run the suite after any change. On the development PC `python` is 3.14 (`C:\Program Files\Python314`, with user
site-packages enabled; `py -3.12` matches the release build). Linux needs
`sudo sysctl -w net.ipv4.ping_group_range="0 2147483647"` for the ICMP backend.

## Layout

- `pingerplot/monitor.py`: the engine. `Monitor` (one per target): `start()` clamps and records options, a worker
  thread traces then loops rounds; probes fan out over a pool sized to `max_hops`; reverse DNS on its own pool;
  alerts, events, webhook, probe CSV, session save/load. `start_options()` reproduces a run's options.
  `per_target_log_path()` derives `probe_<target>.csv`.
- `pingerplot/model.py`: `Hop` (ring buffer of `Sample`s, statistics), `HopView` (immutable snapshot for the UI),
  `mos()`, `csv_safe()`, `hop_from_session()` (the one session-hop parser), `draw_version()`.
- `pingerplot/icmp.py`: the Windows backend (`IcmpSendEcho`, `IcmpSendEcho2Ex` for a source address) and the
  dispatcher: `ping()` calls `icmp_posix.ping()` off Windows. `PingResult` and the IP_STATUS vocabulary both
  backends speak.
- `pingerplot/icmp_posix.py`: Linux/macOS. `SOCK_DGRAM`/`IPPROTO_ICMP`; TTL-exceeded from the error queue
  (`IP_RECVERR`, Linux) or as a readable message (macOS); kernel receive timestamps (`SO_TIMESTAMPNS`, Linux);
  `reply_is_ours()` matches replies to probes; `next_seq()`.
- `pingerplot/tcpudp.py`: TCP/UDP probes. `probe()` (one), `probe_path()` (a round in parallel on one capture
  socket), `_parse()`/`_match()` (ICMP errors), `source_problem()`, strict source binding.
- `pingerplot/gui.py`: `App`. Header (status, the ⋯ menu), Targets / Settings pages; target cards on the left; the
  detail on the right (MOS, the red alert banner, Hops / Timeline / Map / Events). `_start(target, options)`: a new
  target gets the Settings page's defaults (`_default_options()`), a stopped one its own options. A 700 ms refresh
  timer only reads snapshots. `_post()` queues work from other threads; `_drain_ui` runs it every 50 ms.
- `pingerplot/gui_settings.py` (`FIELDS`, `OptionVars`, the option form, `SettingsPage`, `EditTargetDialog`),
  `gui_widgets.py` (`ScrollFrame`, `Nav`, `TargetCard`, `setting_row`, `section`), `gui_charts.py` (drawing the
  graph, timeline and map onto canvases), `gui_updates.py` (`Updates`: checks, the bar, the Settings card).
- `pingerplot/theme.py` + `gfx.py`: the house look shared with UniFi Stock Watcher. Light/dark palettes, ttk elements
  drawn as signed-distance rasters into 9-slice `PhotoImage`s, Segoe Fluent icons with fallbacks. Every ttk style
  comes from `Theme`; `legacy_colours()` maps the palette to the keys the drawing code reads (`gui.COLORS`).
- `pingerplot/winplatform.py`: DPI awareness, the AppUserModelID, dark title bars, the Run key (Start with Windows,
  `--minimized`), `SingleInstance` (a named mutex; a second launch shows the first through the tray window).
  `tray.py`: the notification-area icon on its own thread (`Shell_NotifyIconW`). `tkthreads.py`: see below.
- `pingerplot/updater.py` + `ed25519.py` + `update_key.py`: signed updates (below). `selfcheck.py`: `--self-test`.
- `pingerplot/headless.py`: config → `Target`s, `supervise()` (restart with back-off), `print_events()`,
  `print_status()`, `run_report()`/`format_report()`, `print_comparison()`, `shutdown_all()`.
- `pingerplot/compare.py`: baseline diff (per TTL; a changed address is *rerouted*, never a latency delta).
- `pingerplot/geoip.py`: the Map tab's ipwho.is lookups (opt-in, rate-limited, private addresses never sent).
- `pingerplot/settings.py`, `appicon.py` (the icon, drawn), `worldmap.py` (Natural Earth coastlines as data),
  `selftest.py`.
- `launch.ps1` (+ `Setup.cmd`, `PingerPlot.vbs`): launch, elevate, shortcuts, the elevated logon task.
- `packaging/`: `pingerplot.spec` (onedir: `PingerPlot.exe` and `pingerplot-headless.exe` share `_internal`),
  `build_windows.py`, `PingerPlot.iss` (AppId `{64B8717F-...}` must never change), `signing.py` (the release key and
  `latest.json`), `make_icon.py`, `headless_entry.py`, `requirements-build.txt` (hash-pinned), and the tests CI runs
  on the built files: `test_portable.ps1`, `test_installer.ps1`, `test_update.ps1` (+ `update_test_helper.py`).
- `scripts/demo.py`: the real engine and window against simulated paths (RFC 5737 addresses); the README and site
  pictures come from `--shots site/images`. `scripts/make_social_preview.py` (Pillow, dev only). `site/`: the
  GitHub Pages page (`.github/workflows/pages.yml`).
- Data (never in the repo): `%APPDATA%\PingerPlot\` (`settings.json`, `update.log`; XDG config dir elsewhere), or
  beside the exe for a portable copy (`portable.txt` there), or `PINGERPLOT_DATA_DIR`. Probe CSVs and sessions
  wherever the user puts them.

## Design decisions and gotchas

- **Threads and Tk.** Monitor workers never touch Tk. The GUI polls `snapshot()`, `summary()`, `events_after()` and
  friends (all copy under the monitor's `RLock`) every 700 ms, and redraws a canvas only when `draw_version()`
  changes. `_refresh` re-arms in a `finally`: one exception in a redraw used to freeze every target's display.
- **Run generations.** `start()` bumps `_generation`; every write a worker makes checks it (`_alive(gen)`,
  `_apply_probe`, the trace trim, shrink/grow, status). `stop()` joins for timeout + 2 s and moves on, and DNS
  resolution can outlive that, so a superseded worker must never write into the new run.
- **Stopping many monitors**: `request_stop()` on all, then `stop()`/`shutdown()` each, or the waits add up
  (`_stop_all`, `_on_close`, `headless.shutdown_all`).
- **Statistics are per TTL across responders** (the MTR model), but the **alert window counts only samples since the
  current responder took the TTL** (`Hop.since_change`): after a reroute a router's rate-limited TTL-exceeded
  replies used to read as the destination's loss.
- **Jitter** is the mean absolute difference between consecutive received RTTs (lost probes skipped), not the
  standard deviation (changed in 1.3.3). It's the jitter term in `mos()`'s simplified E-model.
- **Alerts** judge the destination hop only, gated on it having answered and on a full window; keys are
  `(ttl, kind)`; a route change retires the old destination's keys. Beep, events and the webhook happen outside the
  lock. Event kinds: `route`, `alert`, `clear`, `info`, `warn` (`warn` never beeps or fires the webhook).
- **Webhooks never log or show the full URL** (`_webhook_host()`): Slack, Teams and ntfy put the secret in the path.
  A failed delivery is a `warn` event.
- **Source IP** is checked up front in every mode (`tcpudp.source_problem()`); TCP/UDP probes bind strictly when
  the user chose the address and report `ERROR_INVALID_NETNAME` (1214) per probe if it goes away, like the Windows
  ICMP API does. An auto-detected source falls back as before.
- **TCP timeout floor 3000 ms**: Windows surfaces a RST only after retrying the SYN (~2 s); below that a closed port
  looks like an unreachable host. `TCP_MAXRT` replaces the refusal with a timeout, so it doesn't help.
- **POSIX timing**: with `SO_TIMESTAMPNS` the RTT is the kernel's receive stamp minus `time.time_ns()` taken just
  before `sendto`; a stepped clock falls back to `perf_counter`. Python doesn't export the constant: 35
  (asm-generic) only on architectures known to use it. macOS and TCP/UDP time in user space.
- **macOS replies** are matched by sequence number (unique per probe, `next_seq()`) and, for router errors, the
  quoted echo header; Linux's ping sockets already deliver only a socket's own replies. Untested on a Mac.
- **Probe log**: one file per target (no target column; the CSV layout stays compatible), rolls over at 25 MB with
  one backup; a failed rollover (the file open in Excel) logs once, keeps appending and retries after 60 s.
  RTT to 3 decimals with zeros trimmed.
- **Sessions and configs are untrusted input**: `hop_from_session()` type-checks every field; `load_dict()` parses
  everything before changing state; files people author are read as `utf-8-sig` (PowerShell 5.1 writes a BOM).
- **Settings** save per-target options (`target_options`, filtered to `START_OPTION_KEYS` on load; a 1.3.x file
  with names only still loads). A loaded session lives in `_monitors` as `"<name> (loaded)"` and is never saved as
  a target.
- **Geo lookups**: a network failure raises `LookupFailed` (counted, retried after 60 s); a well-formed "no
  location" is cached.
- **Headless prints events** (`print_events`, through an encoder that can't raise on a cp437 console). A target that
  stops (DNS down at boot) is restarted with back-off from 30 s to 5 min; the event cursor resets with it.
- **launch.ps1 -InstallAutostart** registers the interpreter the py launcher resolves to (never the launcher, whose
  choice is per-user state), checks it, its stdlib and the app folder for admin-only paths, and every elevated path
  runs Python with `-E -s` (not `-I`: that drops the script's folder from `sys.path`). Known residual: the elevated
  app reads the user-writable `settings.json`.

- **Tk objects die on the main thread.** A `tk.Variable` or `PhotoImage` garbage-collected on a worker thread calls
  into Tcl from there, which waits for the main loop (68 s per App in the suite). `tkthreads.install()` (first thing
  in `App.__init__`) queues such finalisers; `drain()` runs them on the main thread.
- **One copy at a time**: `SingleInstance` before Tk. `--self-test` and `--version` run before it, so a running copy
  never stops a build being tested. `--finish-update` runs before anything else.
- **Signed updates** (UniFi Stock Watcher's scheme): each release has `latest.json`, an Ed25519-signed payload of the
  version and the installer's and zip's size and SHA-256. `updater.check()` trusts it only under `update_key.py`'s
  public key; `download()` fetches from the version's own tag and deletes anything that doesn't hash; `apply()` runs
  Setup `/VERYSILENT /relaunch=1` (installed: `unins000.exe` beside the exe) or starts the staged copy with
  `--finish-update <dir> <pid>`, which waits, swaps program files in (`<name>.old` first, rolled back on failure,
  `portable.txt` never added to a non-portable copy) and starts `--updated`. The asset names, the manifest shape and
  the `vX.Y.Z` tag can never change: every installed copy reads them. A bad release is fixed by a higher one.
  `--updated` resumes the saved targets even with resume off. `cleanup_after_update()` removes only
  `PingerPlot-update-X.Y.Z` folders: `Path.glob` is case-insensitive on Windows and once deleted the update test's
  own folder.
- **The signing key** is `%USERPROFILE%\.pingerplot\update-signing-key.pem` (PKCS#8, what OpenSSL reads) on the
  maintainer's PC and the `UPDATE_SIGNING_KEY` secret; never in the repo. `packaging/signing.py` signs with
  `ed25519.py` (standard library; checked against RFC 8032 and `cryptography`). Lose it and installed copies need
  one manual install of a build with a new public key.

## Testing

- `tests/conftest.py` puts `tests/` on `sys.path` and imports `gui_harness` (which imports tkinter optionally: a
  skip raised while loading conftest is a collection error). The `app` fixture redirects `%APPDATA%` first, cancels
  the refresh timer and geo worker, and reports the ICMP backend available (its dialog would hang under xvfb).
- `tests/mock_router.py` stands in for the `SIO_RCVALL` capture socket with a loopback socket fed router-shaped ICMP
  errors, so the TCP/UDP round loops run their real `select()`/`recvfrom()` code.
- Live probes go to loopback only, plus one TTL-1 probe that accepts any answer. `test_ci_coverage_gate.py` fails the
  CI Linux jobs if the live POSIX tests would skip, so a green run means they ran against a real kernel.
- `test_packaging.py` enforces the workflow rules: no `${{ }}` inside `run:`, actions pinned by SHA with a version
  comment, only the publish job can write, only the sign job reads a secret (and installs nothing), checkouts without
  credentials, the build lock hashed, the updater and the build agree on asset names.
- GUI tests that need worker threads drain `app._ui_jobs` themselves (`tests/test_gui_updates.py`).
- CI's `package` job builds the installer and zip on every push and runs the three PowerShell tests against them.

## Releasing

1. Bump `__version__` in `pingerplot/__init__.py` and `version` in `pyproject.toml`; write the CHANGELOG entry and
   `release-notes/vX.Y.Z.md` (the release text).
2. `python -m pytest`, commit, push `main`, wait for CI (including the `package` job).
3. `git tag -a vX.Y.Z -m "PingerPlot X.Y.Z"` and `git push origin vX.Y.Z`.

`release.yml`: the read-only `windows` job checks the tag and the version, installs the hash-pinned build lock, runs
the suite, builds (`build_windows.py`), smoke-tests the GUI (`--version`), runs `test_portable.ps1`,
`test_installer.ps1` and `test_update.ps1`, and uploads Setup.exe, the zip and `SHA256SUMS.txt`. The `sign` job holds
`UPDATE_SIGNING_KEY`, installs nothing, signs `latest.json` with the runner's `python3 -I` and verifies it against the
app's public key. The `publish` job (the only one with `contents: write`, running only SHA-pinned actions and `gh`)
creates the release from `release-notes/<tag>.md` and uploads the files, `latest.json` last.
Dependabot proposes newer action pins and lock entries monthly; check each lock hash against PyPI's published
digests before taking it.

## Working on this repo from Claude Code

- The repository's description, topics, the Pages source (GitHub Actions) and the social preview
  (`site/images/social-preview.png`) are set by hand in Settings.
- `gh` isn't installed. Read GitHub through the public API (`curl https://api.github.com/repos/jamesccupps/PingerPlot/...`);
  Actions job logs need a signed-in browser, so make CI failures visible through test results instead.
- The repository's About text, topics and settings are edited in the built-in browser pane, where the user has signed
  in. Never enter credentials or secrets there.
- The Bash tool has collapsed `\\` to `\` inside heredocs: write files with backslashes through Write/Edit.
- `core.autocrlf` is on, so `.txt` files have CRLF in the working copy (`.py`/`.md`/`.yml` are LF by
  `.gitattributes`); scripts that edit them must keep the line endings they found.
