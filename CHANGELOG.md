# Changelog

All notable changes to PingerPlot are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/).

## [1.3.2] — 2026-08-21

Acts on an independent third-party audit of 1.3.1. All fifteen findings are
addressed. No new features and no behaviour changes you would notice on a
healthy path — the theme is that several ways the app could stop doing its job
did so **silently**, and now say so.

### Fixed

- **One malformed reply ended geolocation for the whole session.** `_lookup`
  wrapped the request and `json.loads` in a `try`, but the first use of the
  parsed value sat outside it — and `json.loads` returns whatever the body
  held. A captive portal, proxy error page or CDN interstitial serving a JSON
  *array* raised `AttributeError`, which propagated out of the worker and
  killed the thread. That thread is started once and never restarted, so the
  Map tab silently stopped resolving with no error, no retry and no
  explanation. The body is now checked by shape, the worker survives anything
  that still escapes, and the Map header reports the failure count.
- **A probe log that could not be opened was disabled silently.** The error was
  reported by assigning `status`, which `_run` overwrites with
  `Resolving <target>...` milliseconds later, and no Event was logged. The run
  then looked entirely normal while writing nothing — the exact failure
  `supervise()`'s docstring rails against. Now a timestamped `warn` Event that
  stays. Headless also creates the directory for an **absolute** `log_path`,
  which was one of the ways to hit this.
- **Headless never checked whether the ICMP backend works.** The GUI does; the
  headless runner, which is the mode most likely to land on a Linux box, did
  not. Every probe raised, `_gather_range` swallowed it, and the operator got a
  clean-looking report saying the destination never answered — pointing them at
  the network instead of at a one-line `sysctl`. Now warned on stderr, naming
  the affected targets, and only when a target actually uses ICMP.
- **`--baseline` could crash a scheduled job after printing its report.**
  `print_comparison` read the monitor's private hop list with no lock while the
  probe thread was still running (`run_report` stops waiting, not probing).
  Iterating a hop's sample deque mid-append raises `RuntimeError`, which
  `main()`'s `except OSError` did not catch — so the process died with a
  traceback *after* the tables were printed and never wrote `--report-csv`.
- **A source IP the machine does not hold behaved differently on POSIX.** The
  README promises such an address is rejected by name rather than falling back
  to the default route. The Windows backend does; the POSIX one raised
  `OSError(EADDRNOTAVAIL)` out of `ping()` — which aborted the trace with a
  bare errno, or, inside a monitoring round, was swallowed into a timeout, so a
  pinned interface disappearing mid-run reported **100% loss on a path that was
  fine**.
- **Loading a session left a permanently dead row in the Targets grid.** The
  loaded session was persisted as a target under the key `"<name> (loaded)"`,
  which the next launch tried to resolve. Self-perpetuating: every save rewrote
  it.
- **The UDP port walk could wrap to zero.** With `port=65535` — reachable from
  the Engine dialog's spinbox — hop 1 computed port 0, `sendto` failed with
  `EINVAL`, and the error was discarded, so that hop vanished from the round.
- **`socket.SIO_RCVALL` is Windows-only** and every caller guards
  `_open_capture` with `except OSError`, which does not catch the resulting
  `AttributeError`. Unreachable on Linux; unverified on macOS, which has had an
  ICMP backend since 1.3.0.
- Alert *clearing* logged inside the monitor's lock, contradicting the rule
  `_retire_alerts` states in its own docstring — so an outbound webhook POST
  was started from inside the critical section the UI reads every 700 ms.
- The geo response body is read with a 64 KB bound. A location object is a few
  hundred bytes, and `urlopen`'s timeout covers socket inactivity rather than
  total transfer.

### Changed

- **The release workflow no longer interpolates `${{ }}` into a shell script.**
  The runner substitutes those into the script *text* before bash sees it, so a
  dispatch input containing shell metacharacters would execute — in a job
  holding `contents: write` and a token that can publish releases. Values now
  arrive via `env:`. The tag is validated by character set *and* shape: a glob's
  `*` matches a newline, so a tag containing one would otherwise pass the
  version check and inject an extra step output.
- **Actions are pinned by commit SHA**, with Dependabot added to keep the pins
  from going stale silently.
- `_gather_range` aborts on the run generation like every other loop in the
  worker, rather than on `running`, which a superseded worker sees flip back to
  `True`.
- The Engine dialog now says that UDP traceroute mode walks the destination
  port upward from *Port*, one per hop. It is the literal port probed only for
  TCP and for final-hop-only, and nothing said so.

### Notes

- Test suite: 370 → 424. Three of them exist because the fix they cover could
  otherwise be undone invisibly: no workflow may interpolate into a shell
  script, the hop list may not be read without the lock, and the tag guard is
  extracted from the workflow and actually executed.
- **`gui.py` is now under test.** 1,470 lines at ~10% coverage, skipped on the
  grounds that it imports `tkinter` at module level. It needs no
  infrastructure: a withdrawn root on Windows, `python3-tk` and `xvfb-run` on
  the ubuntu legs, which CI now installs.
- **One test depended on the LAN.** It assumed `192.0.2.1` black-holes traffic;
  the audit ran in a container whose own gateway *was* `192.0.2.1` and refused
  the port, so correct code failed. Replaced with a deterministic stand-in that
  sends no packets at all.
- Every fix in this release was verified by reverting it and watching its test
  go red. Two tests that could not fail were found and rewritten that way — one
  had spent 30 seconds proving nothing.

## [1.3.1] — 2026-08-21

Adds downloadable Windows executables, and fixes a config-file bug found while
building them.

### Added

- **Windows executables**, attached to each release and built by CI from the
  tagged commit: `PingerPlot.exe` (GUI, ~13.5 MB) and `pingerplot-headless.exe`
  (console, ~10.1 MB), with `SHA256SUMS.txt`. Neither needs Python installed.
  Both are smoke-tested before they are attached — the GUI binary's import
  graph is verified through a new `--version` flag (which returns before Tk is
  created, so it works on a machine with no display), and the headless binary
  runs a real ICMP probe. They are **unsigned**; the README says plainly what
  SmartScreen and antivirus heuristics will make of that, and why this binary
  is a worse-than-average case for the latter.
- `main.py --version` / `pingerplot --version`.

### Fixed

- **A byte-order mark made a config unreadable.** Writing `monitor.json` with
  PowerShell's `Set-Content -Encoding utf8` — the shell that ships with Windows
  — produced a file the app refused outright with `Unexpected UTF-8 BOM`, exit
  1. Files a person may have authored are now read as `utf-8-sig`, which
  tolerates a BOM and is otherwise identical: the headless config, a baseline
  session, a loaded session, and `settings.json`. The settings case was the
  quietest — `load()` swallows the decode error and returns `{}`, so a BOM
  there silently reset the theme, engine options, alert thresholds and target
  list to defaults with no message at all. Writes remain BOM-free.

### Changed

- CI opens `net.ipv4.ping_group_range` on the Linux runners. Without it the
  POSIX backend's live tests skipped on every ubuntu leg — so the backend
  shipped in 1.3.0 having never executed a single socket operation anywhere.
  They now run against real sockets on real Linux. A real multi-hop path is
  still untested; the README caveat stands.

## [1.3.0] — 2026-08-21

A full audit pass plus four features. Two of the fixes change results you may
have relied on — see **Changed** before comparing old exports with new ones.

### Added

- **Baseline comparison.** *File → Compare with saved session…* in the GUI, or
  `--baseline` alongside `--report` in headless mode, diffs the current path
  against one saved earlier. A hop whose responding address changed is reported
  as *rerouted* with no latency delta, because subtracting one router's latency
  from another's is a meaningless number. Thresholds are relative *and*
  absolute together, so 1.0 → 1.3 ms is not a 30% regression and 6 ms on a
  300 ms satellite path is not news.
- **DSCP marking** (Engine… → *DSCP*). Probe as the traffic class you care
  about rather than always as best-effort, so a QoS-marked path can be measured
  as itself. The ECN bits are never touched. ICMP mode marks via the IP Helper
  API; on TCP/UDP Windows may silently ignore it — see the README.
- **Source-interface binding** (Engine… → *Source IP*), via `IcmpSendEcho2Ex`.
  Pins the outgoing NIC on a multi-homed box. An address the machine does not
  hold is rejected outright rather than quietly falling back to the default
  route.
- **MOS alerting.** A threshold on the score itself, not just loss and latency
  separately — the E-model folds latency, jitter and loss together, so a path
  can sit under every individual threshold and still be unusable for voice.
  Off by default; fires at or *below* the threshold, since lower MOS is worse.
- **One-shot report mode.** `--report N` collects N rounds, prints an MTR-style
  table per target and exits; `--report-csv` writes the same data to a file.
  The exit status is 0 only if every target reached its destination, so a
  scheduled job can branch on it.
- **Linux and macOS ICMP backend** (`icmp_posix.py`), using unprivileged
  `SOCK_DGRAM`/`IPPROTO_ICMP` plus `IP_RECVERR`/`MSG_ERRQUEUE` — the `mtr`
  technique — with a separate path for macOS, which has neither. Unit-tested on
  every platform in CI; **not yet field-tested on real hardware.**
- **Automatic restart of a stopped target** in headless mode, with backoff from
  30 s to 5 minutes. A target whose name did not resolve at boot used to stay
  dead for the life of the service.
- A **mock router** test fixture (`tests/mock_router.py`) that stands in for the
  `SIO_RCVALL` capture socket, so the TCP/UDP round loops run against real
  sockets and real `select()` calls.

### Fixed

- **A route longer than 16 hops took two or more reply timeouts per round.**
  The probe pool was fixed at 16 workers while `max_hops` defaults to 30 and
  may reach 64, so a round silently split into waves — 2× at 30 hops, 4× at 64.
  With a 2 s timeout, a 30-hop round overran the 2.5 s interval entirely and the
  configured interval was quietly replaced by the round duration. The pool is
  now sized to `max_hops`; measured 1.0× at every length.
- **An alert never cleared once a reroute changed the path length.** Alerts are
  keyed by TTL and only the current destination's TTL was evaluated, so the old
  key was orphaned: the banner, the active-alert count and the summary row
  stayed wrong for the rest of the run — reporting loss on a hop that no longer
  existed while the real destination was healthy.
- **A closed TCP port read as an unreachable host.** Windows does not surface
  the RST until it has finished retransmitting the SYN (~2 s measured), and the
  default reply timeout was 1000 ms. TCP mode now raises the timeout to at least
  3000 ms and logs that it did.
- **`settings.save()` could raise**, despite promising not to: it caught
  `OSError` only, while `json.dump` raises `TypeError`/`ValueError`. Called from
  the window-close handler, so it would have thrown on the way out. A failed
  write is now a genuine no-op instead of leaving a stale temp file.
- **Writing to the probe log during shutdown raised `ValueError`**, not
  `OSError`, so it escaped the handler and surfaced as "Monitor error",
  aborting the round.
- **The Map tab never repainted when geo lookups came back.** On a live monitor
  the next round hid it; on a loaded session the map stayed empty until the
  window was resized.
- **The comparison table crashed on a legacy Windows console** — its header used
  a delta sign, which is in neither cp1252 nor cp437. Console output is now
  ASCII-only, with tests pinning it.
- **A router's identity could be discarded** when an ICMP error and the probe
  socket's error became ready in the same `select()` wakeup: the socket error
  was read first and the captured packet — the only thing naming the responder —
  was thrown away.

### Changed

- **Latency alone can now colour a hop row red.** `BAD_MS` (250 ms) was tested
  one branch too late and could never change the outcome, so a 900 ms hop with
  no loss looked exactly as alarming as a 130 ms one. Only packet loss produced
  red before.
- **TCP-mode reply timeouts below 3000 ms are raised**, with a note in the
  Events tab. A genuinely unreachable TCP destination now costs 3 s a round
  rather than 1 s — the trade for being able to tell "service down, host fine"
  from "host unreachable".
- `Hop.recent_stats(k)` returns loss, average and jitter in one pass, replacing
  repeated scans of the sample deque.
- CI runs `pytest -rs` and reports ICMP backend availability on each leg, so a
  matrix leg that silently skips a test group is visible in the log. Actions
  bumped to `checkout@v5` / `setup-python@v6`.

### Notes

- Test suite: 95 → 340 tests. Engine coverage 62% → 76%; `tcpudp.py`, which had
  no coverage of either round loop, went 25% → 78%.
- That the DSCP byte reaches the wire has **not** been verified — every code
  point is accepted without error, which is a weaker claim. Confirm with a
  capture before relying on a QoS result.

## [1.2.0] — 2026-05

- Headless / service mode (no GUI), driven from a JSON config.
- Atomic session save, load caps, `csv_safe` whitespace handling.
- Fixed `probe_path` dropping a hop's result on a non-clean TCP error.
- Coverage for geoip and route grow/shrink/trace.

## [1.1.1] — 2026-05

- Optimised the refresh hot path (~10× less per-tick CPU) via a single-pass
  `Hop.compute()`.

## [1.1.0] — 2026-05

- Persisted settings, per-target settings editing, pause/resume and
  copy-to-clipboard, webhook alert notifications, capped and rotating probe log.

## [1.0.0] — 2026-05

- Initial public release.

[1.3.2]: https://github.com/jamesccupps/PingerPlot/releases/tag/v1.3.2
[1.3.1]: https://github.com/jamesccupps/PingerPlot/releases/tag/v1.3.1
[1.3.0]: https://github.com/jamesccupps/PingerPlot/releases/tag/v1.3.0
[1.2.0]: https://github.com/jamesccupps/PingerPlot/releases/tag/v1.2.0
[1.1.1]: https://github.com/jamesccupps/PingerPlot/releases/tag/v1.1.1
[1.1.0]: https://github.com/jamesccupps/PingerPlot/releases/tag/v1.1.0
[1.0.0]: https://github.com/jamesccupps/PingerPlot/releases/tag/v1.0.0
