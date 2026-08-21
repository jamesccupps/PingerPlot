# Changelog

All notable changes to PingerPlot are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/).

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

[1.3.1]: https://github.com/jamesccupps/PingerPlot/releases/tag/v1.3.1
[1.3.0]: https://github.com/jamesccupps/PingerPlot/releases/tag/v1.3.0
[1.2.0]: https://github.com/jamesccupps/PingerPlot/releases/tag/v1.2.0
[1.1.1]: https://github.com/jamesccupps/PingerPlot/releases/tag/v1.1.1
[1.1.0]: https://github.com/jamesccupps/PingerPlot/releases/tag/v1.1.0
[1.0.0]: https://github.com/jamesccupps/PingerPlot/releases/tag/v1.0.0
