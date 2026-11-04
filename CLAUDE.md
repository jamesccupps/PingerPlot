# PingerPlot - notes for Claude Code

A continuous-traceroute network path monitor (MTR-style), Python standard library only: a Tkinter window
(`gui.py`), a headless runner for Task Scheduler (`headless.py`), and one engine under both (`monitor.py`). Every
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
python -m pip install --require-hashes --only-binary=:all: -r packaging/requirements-build.txt
python packaging/make_icon.py build/pingerplot.ico
python -m PyInstaller --noconfirm --clean packaging/pingerplot.spec    # dist\PingerPlot.exe, pingerplot-headless.exe
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
- `pingerplot/gui.py`: `App`. Toolbar + Engine dialog build start() options (`_toolbar_options()`), `_start(target,
  options)`; a Targets grid; tabs Overview (hop table + graph), Timeline, Events, Map; settings in
  `settings.py`; a 700 ms refresh timer that only reads snapshots.
- `pingerplot/headless.py`: config → `Target`s, `supervise()` (restart with back-off), `print_events()`,
  `print_status()`, `run_report()`/`format_report()`, `print_comparison()`, `shutdown_all()`.
- `pingerplot/compare.py`: baseline diff (per TTL; a changed address is *rerouted*, never a latency delta).
- `pingerplot/geoip.py`: the Map tab's ipwho.is lookups (opt-in, rate-limited, private addresses never sent).
- `pingerplot/settings.py`, `appicon.py` (the icon, drawn), `worldmap.py` (Natural Earth coastlines as data),
  `selftest.py`.
- `launch.ps1` (+ `Setup.cmd`, `PingerPlot.vbs`): launch, elevate, shortcuts, the elevated logon task.
- `packaging/`: `pingerplot.spec`, `make_icon.py`, `headless_entry.py`, `requirements-build.txt` (hash-pinned).
- Data (never in the repo): `%APPDATA%\PingerPlot\settings.json` (XDG config dir elsewhere); probe CSVs and
  sessions wherever the user puts them.

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

## Testing

- `tests/conftest.py` puts `tests/` on `sys.path` and imports `gui_harness` (which imports tkinter optionally: a
  skip raised while loading conftest is a collection error). The `app` fixture redirects `%APPDATA%` first, cancels
  the refresh timer and geo worker, and reports the ICMP backend available (its dialog would hang under xvfb).
- `tests/mock_router.py` stands in for the `SIO_RCVALL` capture socket with a loopback socket fed router-shaped ICMP
  errors, so the TCP/UDP round loops run their real `select()`/`recvfrom()` code.
- Live probes go to loopback only, plus one TTL-1 probe that accepts any answer. `test_ci_coverage_gate.py` fails the
  CI Linux jobs if the live POSIX tests would skip, so a green run means they ran against a real kernel.
- `test_packaging.py` enforces the workflow rules: no `${{ }}` inside `run:`, actions pinned by SHA with a version
  comment, only the publish job can write, checkouts without credentials, the build lock hashed.

## Releasing

1. Bump `__version__` in `pingerplot/__init__.py` and `version` in `pyproject.toml`; write the CHANGELOG entry and
   `release-notes/vX.Y.Z.md`.
2. `python -m pytest`, commit, push `main`, wait for CI.
3. An annotated tag whose message is the release text (the workflow uses `--notes-from-tag`):
   `git tag -a vX.Y.Z -F <notes>` and `git push origin vX.Y.Z`.

`release.yml`: the read-only `windows` job checks the tag, installs `packaging/requirements-build.txt` with
`--require-hashes`, runs the suite, builds both executables, smoke-tests them (the GUI's `--version` exits before Tk;
the headless one probes loopback) and uploads them with `SHA256SUMS.txt`; the `publish` job (the only one with
`contents: write`, running only SHA-pinned actions and `gh`) creates the release from the tag and attaches the files.
Dependabot proposes newer action pins and lock entries monthly; check each lock hash against PyPI's published
digests before taking it.

## Working on this repo from Claude Code

- `gh` isn't installed. Read GitHub through the public API (`curl https://api.github.com/repos/jamesccupps/PingerPlot/...`);
  Actions job logs need a signed-in browser, so make CI failures visible through test results instead.
- The repository's About text, topics and settings are edited in the built-in browser pane, where the user has signed
  in. Never enter credentials or secrets there.
- The Bash tool has collapsed `\\` to `\` inside heredocs: write files with backslashes through Write/Edit.
- `core.autocrlf` is on, so `.txt` files have CRLF in the working copy (`.py`/`.md`/`.yml` are LF by
  `.gitattributes`); scripts that edit them must keep the line endings they found.
