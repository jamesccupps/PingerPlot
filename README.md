# PingerPlot

[![CI](https://github.com/jamesccupps/PingerPlot/actions/workflows/ci.yml/badge.svg)](https://github.com/jamesccupps/PingerPlot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6)
![Linux/macOS](https://img.shields.io/badge/Linux%20%2F%20macOS-beta-yellow)

A continuous-traceroute network path monitor (MTR-style): per-hop latency and
packet-loss tracking with a live latency graph, multi-target summary, MOS scoring
and MOS alerts, DSCP marking, baseline comparison, session save/load, a world map,
and ICMP/TCP/UDP probe modes. Pure Python, **zero third-party dependencies**,
dark-themed, HiDPI-aware. Free and Open Source.

Windows is the tested platform. A Linux/macOS ICMP backend landed in 1.3.0 and is
unit-tested but not yet field-tested — see [Platform](#platform).

![PingerPlot — dark theme, multi-target sidebar, hop table and latency graph](docs/screenshot.png)

## What it does

Like WinMTR or `mtr`, it discovers every router between you and a target,
then re-probes the whole path on an interval. Each round is effectively a full
traceroute, so you accumulate **min / avg / max / current latency, jitter, and
packet loss for every hop over time** — which makes it obvious *where* along the
path a problem lives (your LAN, your ISP's first hop, a peering point, or the
destination).

- **Route discovery** by walking the IP TTL upward and reading the
  "TTL exceeded" replies (standard traceroute primitive).
- **Continuous monitoring** of all hops, fanned out across a small thread pool
  so each round takes ~one timeout, not the sum.
- **Live hop table** — IP, reverse-DNS hostname, loss %, sent count,
  current/avg/min/max latency, jitter. Rows colour amber/red on loss or high
  latency.
- **Single-hop latency graph** (Overview tab) for the selected hop (click a
  row), with red ticks marking lost probes and a filled area for the trend.
- **All-hops timeline** (Timeline tab) — every hop drawn as a stacked strip on
  a shared time + latency scale, so a latency step shows up as the row where the
  bars get tall. This is the full MTR-style path-over-time view.
- **Continuous auto-retrace + route-change detection** — because every round is
  a full traceroute, reroutes (a hop's address changing) and route length
  grow/shrink are detected live and written to the Events tab.
- **Sustained-degradation alerts** — a red banner + Windows beep + Event-log
  entry when the **destination** shows sustained packet loss or high latency
  over the last *N* probes. Evaluated on the destination only (the one hop whose
  numbers aren't muddied by ICMP-deprioritising routers), and gated so a target
  that simply blocks ICMP never false-alarms.
- **ICMP / TCP / UDP probe modes** — trace with ICMP (default, no admin), or TCP
  SYN to a port / UDP, for paths that block ICMP or to test a specific service.
  See [Probe modes](#probe-modes) for the admin tradeoffs.
- **Multi-target** — monitor many targets at once; a left **summary grid** shows
  each one's hop count, loss, latency, and MOS at a glance. Click one to drive
  the detail tabs. Alerts on any target surface in the banner.
- **MOS (VoIP quality) score** for the destination — one number (ITU-T E-model)
  for "is this path good enough for voice/video", shown live in the status bar.
- **Session save / load** to JSON for offline review, plus **crash-safe
  CSV logging** of every probe (with a per-round index, so "did every hop spike
  in the same round?" is a one-line filter) — size-capped with rollover, so
  unattended multi-week runs can't fill the disk.
- **Engine options dialog** — packet type & port, reply timeout, payload size,
  send delay (rate throttle), max hops, name resolution, final-hop-only.
- **Dark theme by default**, with a light/dark toggle (button, top-right).
- **HiDPI-aware** — crisp on scaled (150 %/200 %) displays.
- **CSV export** of the per-hop summary (with destination MOS), under a
  self-documenting header that records the interval, timeout, probe mode, packet
  size, sample count, and the exact time window — so two exports can actually be
  compared instead of guessed at.
- **Pause / Resume & copy** — right-click a target to pause probing without
  losing its history (resume picks up where it left off); right-click a hop to
  copy its IP or hostname.
- **Per-target settings** — engine and alert options are remembered per target;
  right-click a target → *Edit settings…* to inspect or change just that one.
- **Webhook alerts** — point the Engine dialog's *Webhook URL* at an http(s)
  endpoint to get a JSON POST when the destination alert raises or clears.
- **Baseline comparison** — *File → Compare with saved session…* diffs the
  live path against one you saved earlier and shows what actually changed, per
  hop. A hop whose responding address changed is reported as **rerouted** with
  no latency delta, because subtracting one router's latency from another's is
  a meaningless number. Also available headless with `--baseline`.
- **DSCP marking** — probe as the traffic class you care about (46 = EF/voice,
  34 = AF41/video) instead of always as best-effort, so a QoS-marked path can be
  measured as itself. See [DSCP and source interface](#dscp-and-source-interface).
- **Source-interface binding** — pin the outgoing NIC on a multi-homed box, to
  ask what a path looks like *from a particular VLAN*. An address the machine
  doesn't hold is rejected outright rather than quietly falling back.
- **MOS alerts** — alert on the score itself, not just loss and latency
  separately. The E-model folds latency, jitter and loss together, so a path can
  sit under every individual threshold and still be unusable for voice.
- **One-shot report mode** — `--report N` collects N rounds, prints an MTR-style
  table per target and exits, with the exit status saying whether every target
  was reachable. For tickets and scheduled checks rather than a service.
- **Persists between launches** — theme, engine options, alert thresholds and
  your target list save to `%APPDATA%\PingerPlot` and restore on start;
  *View → Resume targets on launch* re-arms the last session's monitors.

### Tabs and layout

A left **Targets** summary grid lists every monitored target; selecting one
drives the detail tabs on the right:

| Tab | Shows |
|---|---|
| **Overview** | Hop table (top) over a latency graph for the selected hop (bottom). Click a row to graph it; until you do, it follows the destination. |
| **Timeline** | All hops stacked on a shared time axis — the path-over-time view. Scrolls when there are many hops. |
| **Events** | Timestamped log of route changes, alert raises, and alert clears. |
| **Map** | Geo-located hops plotted on a world map with the path between them. See the privacy note below. |

### Map view & privacy

The Map tab geo-locates each **public** hop via [ipwho.is](https://ipwho.is)
(free, HTTPS, no key) and plots the path on a baked-in Natural Earth coastline
(no runtime dependency). This is the **only** part of the app that contacts a
third party, and it's **opt-in**: nothing is sent until you open the Map tab, and
**private/LAN hops are never looked up** (they have no public location anyway).
Lookups are rate-limited and cached. If you'd rather not send hop IPs anywhere,
just don't open the Map tab.

### Alerts

Set the thresholds in the second toolbar row: **loss ≥ X%**, **latency ≥ Y ms**,
evaluated **over the last N probes** (the window is what makes it "sustained" —
a single spike won't trip a 20-probe window). Set a threshold to `0` to disable
that check. **Sound** toggles the beep. A condition raises when it crosses the
threshold and clears automatically when it recovers; both are logged.

## How it works (and why no admin / no Npcap)

ICMP is sent through the Windows IP Helper API (`IcmpSendEcho` in
`iphlpapi.dll`) via `ctypes`. That API runs in user space, so unlike raw-socket
ping/traceroute tools it needs **no administrator rights and no packet-capture
driver**. The TTL is set per-probe through `IP_OPTION_INFORMATION`, and an
intermediate router that drops the packet answers with status
`IP_TTL_EXPIRED_TRANSIT` plus its own address — that is the whole trick.

IPv4, and the probe **backend** is Windows-only today because it's the Win32 ICMP
API — but that's the *only* platform-specific module (see [Platform](#platform)).
Auto-retrace, the timeline, alerts, MOS, multi-target, and save/load are all built
on this same user-space primitive, so the whole app runs **without administrator
rights** in ICMP mode.

## Probe modes

ICMP mode is built on the Windows IP Helper API (`IcmpSendEcho`) — the same call
`ping.exe` and `tracert.exe` use internally, which is why it needs no admin. The
other two modes:

| Mode | Needs admin? | Use it for |
|---|---|---|
| **ICMP** | No | Default. Full traceroute + monitoring. |
| **TCP, final-hop-only** | No | "Is this service up, and how fast?" — a TCP handshake to a port (443, 3389, a device's web UI…). No per-hop trace, just the destination. |
| **TCP / UDP, full traceroute** | **Yes** | Per-hop trace when ICMP is blocked/deprioritised. Sends via a normal socket with `IP_TTL`, captures routers' ICMP "TTL exceeded" via a raw `SIO_RCVALL` socket — which requires elevation. |

Without admin, the full TCP/UDP traceroute is refused with a clear message rather
than showing misleading all-timeout hops. (UDP note: an *open* UDP port stays
silent, so it reads as a timeout — prefer TCP for a definitive "service is up".)

> **TCP reply timeout.** Windows doesn't hand a connecting socket the RST the
> moment it arrives — it finishes retransmitting the SYN first, about 2 s.
> Below that, a *closed* port is indistinguishable from an unreachable host,
> which is the wrong answer in the commonest case of all ("the service is down
> but the box is fine"). TCP mode therefore raises the reply timeout to at
> least 3000 ms and says so in the Events tab. Shortening the window with
> `TCP_MAXRT` doesn't help: it replaces `WSAECONNREFUSED` with `WSAETIMEDOUT`
> and destroys the very distinction the probe exists to draw.

### DSCP and source interface

**DSCP** (Engine… → *DSCP*) marks each probe with a DiffServ code point, so a
path with QoS can be measured as the class you actually care about rather than
as best-effort. 46 is EF (voice), 34 is AF41 (video), 26 is AF31 (signalling),
0 is best-effort. The low two bits of the ToS byte are ECN and are never touched.

> ICMP mode marks via the IP Helper API and is the mode to use for this. Windows
> **silently ignores** `IP_TOS` on an ordinary socket unless the
> `DisableUserTOSSetting` registry value is cleared, so a TCP/UDP run may go out
> unmarked while `setsockopt` reports success — confirm with a capture before
> drawing a conclusion from one. That the byte reaches the wire has not been
> verified here; every code point is accepted without error, which is a
> weaker claim.

**Source IP** (Engine… → *Source IP*) pins the outgoing interface on a
multi-homed host. An address the machine doesn't hold is rejected with
`ERROR_INVALID_NETNAME` rather than silently falling back to the default
route — a silent fallback is the dangerous outcome, because the numbers look
fine and describe a path you didn't ask about.

Both are recorded in the status line, the report header, the CSV export header
and the saved session: a marked or pinned run measured a different thing from a
plain one, and two runs that don't say which aren't comparable.

## Platform

**Windows 10/11 — tested.** The ICMP backend (`icmp.py`) uses the Win32 IP
Helper API, so it needs no admin rights and no capture driver.

**Linux and macOS — implemented in 1.3.0, not yet field-tested.**
`icmp_posix.py` uses unprivileged `SOCK_DGRAM`/`IPPROTO_ICMP` sockets, the same
technique `mtr` uses. `icmp.ping()` dispatches, so the engine, GUI and headless
runner follow without knowing which backend answered.

Getting a traceroute out of POSIX takes two mechanisms where Windows takes one.
The echo reply arrives on the socket; the router's "TTL exceeded" does **not**,
because on Linux it's an *error* about the datagram we sent rather than a
message addressed to us — it goes to the socket's error queue (`IP_RECVERR` +
`recvmsg(MSG_ERRQUEUE)`). macOS has neither and delivers it as an ordinary
readable message. Both paths are implemented.

On Linux the unprivileged socket is gated by a sysctl. If PingerPlot reports the
backend unavailable, it will name this:

```bash
sudo sysctl -w net.ipv4.ping_group_range="0 2147483647"
```

Honest status: the packet building, error-queue decoding, IP-header detection
and status mapping are unit-tested on every platform in CI, and the live socket
path runs wherever CI permits it — but it has **not** been run against a real
multi-hop path on real hardware. Smoke-test it before trusting a trace:

```bash
python -m pingerplot.selftest 8.8.8.8
```

Bug reports from a real Linux or macOS box are very welcome.

## Requirements

- Windows 10/11 (tested), or Linux/macOS (beta — see [Platform](#platform))
- Python 3.10+ (tested on 3.12). Tkinter ships with the python.org installer.
- Nothing to `pip install`.
- ICMP and TCP-final-hop work as a normal user. **Full TCP/UDP traceroute needs
  an elevated (Run as administrator) session.**

## Get it

**Download (no Git needed)** — the easiest way:

1. Grab the latest `Source code (zip)` from the
   [**Releases**](https://github.com/jamesccupps/PingerPlot/releases/latest) page.
2. Right-click the `.zip` → **Extract All…** to anywhere (e.g. your Desktop).
3. Open the extracted folder and **double-click `PingerPlot.vbs`**. That's it —
   no install, no build, no `pip`.

**Or clone with Git:**

```bash
git clone https://github.com/jamesccupps/PingerPlot
cd PingerPlot
```

No build step and nothing to `pip install` — it's pure standard library.
(Optional: `pip install .` registers a `pingerplot` GUI entry point.)

## Run

**Double-click `PingerPlot.vbs`** — launches the GUI with no console window
(uses `pyw`/`pythonw`). That's the everyday launcher.

Other ways:

| | |
|---|---|
| `PingerPlot.vbs` | Double-click launch, no console window (recommended). |
| `Setup.cmd` | Menu: launch / launch-elevated / make shortcuts / enable-or-disable auto-start / status. |
| `python main.py` | From a terminal (console stays open — handy for seeing errors). |
| `run.bat` | Same as above, double-clickable. |

Enter a hostname or IP (e.g. `8.8.8.8`, `1.1.1.1`, `cloudflare.com`, or an
internal address like your gateway), set the interval, and press **Add / Start**.
Add more targets the same way — they stack in the left grid. Click a hop row to
graph it; click a target to switch the detail view. **Engine…** opens the probe
type/port/timeout/logging options; **Save…/Load…** persist a session.

### Shortcuts and auto-start

Run `Setup.cmd` (or `launch.ps1` directly) for one-time setup:

```powershell
.\launch.ps1 -CreateShortcuts    # Desktop + Start-menu icons (normal + an "(Admin)" one)
.\launch.ps1 -InstallAutostart   # auto-start at logon, elevated, with NO UAC prompt
.\launch.ps1 -UninstallAutostart # undo it
.\launch.ps1 -Status
```

`-InstallAutostart` registers a **Task Scheduler** logon task with *Run with
highest privileges*, so the app starts automatically every time you log in —
already elevated (so TCP/UDP traceroute works) and **without a UAC prompt**,
which an on-demand elevated launch can't avoid. It's opt-in: nothing is
registered unless you run that command, and one command removes it.

> **Security note — where you install matters.** Because that task launches
> PingerPlot **elevated with no prompt**, only enable auto-start from a folder
> standard users can't modify — e.g. `C:\Program Files\PingerPlot`. If the
> program folder *or* your Python install sits somewhere you can write *without*
> elevation (your `Downloads`, your home folder, most data drives), then any
> non-elevated process running as you could replace that code and have it run as
> Administrator at your next logon. `-InstallAutostart` checks for this and warns
> you, but doesn't block you. If you don't need persistence, prefer the one-off
> **(Admin)** launch below.

For a one-off elevated launch instead, use the **PingerPlot (Admin)** shortcut,
`Setup.cmd` → option 2, or `.\launch.ps1 -Elevated` (UAC prompts once).

## Headless / service mode (no GUI)

For unattended monitoring on a box with no desktop session, run the same engine
without the GUI — each target logs to its own crash-safe, size-capped CSV.

```bash
python -m pingerplot.headless --init monitor.json   # write a starter config, then edit it
python -m pingerplot.headless monitor.json          # run it (Ctrl-C to stop)
```

The config is JSON: an optional `status_interval` (seconds between console
status lines; `0` = silent), a `defaults` block, and a `targets` list where each
entry may override any default:

```json
{
  "status_interval": 60,
  "defaults": { "interval": 2.5, "alert_loss_pct": 20, "alert_latency_ms": 250 },
  "targets": [
    { "target": "8.8.8.8", "log_path": "logs/dns.csv" },
    { "target": "10.0.0.1", "log_path": "logs/gateway.csv", "final_hop_only": true,
      "webhook_url": "https://hooks.example/abc" }
  ]
}
```

Relative `log_path`s resolve next to the config file (so it works regardless of
the working directory), and the log directory is created if missing. To run it
at logon/boot under **Task Scheduler**, point a task at:

```
pythonw.exe -m pingerplot.headless C:\path\to\monitor.json
```

(or `python.exe` if you want the periodic status lines in a redirected log).
`pip install .` also registers a `pingerplot-headless` console script.

A target whose name doesn't resolve at start-up — the normal case for a task
that runs at boot, before DNS is up — is **restarted automatically**, with the
delay backing off from 30 s to 5 minutes while it keeps failing. Restarts are
logged and shown in the status lines, because a target flapping every few
minutes is something you want to see.

### One-shot report mode

For a ticket, a scheduled check, or a before/after around a change — collect a
fixed number of rounds, print a table, and exit:

```bash
python -m pingerplot.headless monitor.json --report 20
python -m pingerplot.headless monitor.json --report 20 --report-csv out.csv
```

The exit status is `0` only if every target reached its destination, so a
scheduled job can branch on it without parsing the output. The table names the
probe mode, interval, timeout and any DSCP or source binding — a report that
doesn't say how it was produced can't be compared with another one.

Add `--baseline` to diff against a session saved earlier (*File → Save
session…* in the GUI), which turns "what does this path look like" into "is it
worse than it was":

```bash
python -m pingerplot.headless monitor.json --report 20 --baseline last-week.json
```

```
example.net: now vs last-week
  route 3 -> 4 hops; 1 hop rerouted; 1 worse
  Hop  Address            Loss%     +/-   Avg ms     +/-  Verdict
  --------------------------------------------------------------------------
    1  192.168.1.1          0.0    +0.0      1.2    +0.0  same
    2  203.0.113.14         0.0    +0.0     18.4   +16.9  worse
    3  203.0.113.71        12.5              9.7          rerouted 203.0.113.9 -> 203.0.113.71
    4  198.51.100.20        0.0              9.9          new
```

A *rerouted* row deliberately shows no delta: hop 3 is a different box than it
was, so the difference between its latency and the old one's would not be a
number that means anything.

## Reading the results

- **Loss at an intermediate hop while the final hop stays healthy is normal.**
  Many routers rate-limit or ignore the ICMP "TTL exceeded" they're asked to
  generate. Only loss that *carries through to the destination* (the last row)
  indicates a real problem on the path.
- A latency *step* that appears at one hop and persists through every hop after
  it is where the delay is being introduced.
- Sub-millisecond hops show as `<1`; the Win32 RTT is integer-millisecond.

## Tests

```bash
python -m pytest
```

The pure logic (statistics, address encoding, status handling, MOS, alert state
machine, ICMP-error decoding, baseline comparison) is covered by a
platform-independent unit suite — the Win32 DLL access is guarded behind
`sys.platform`, so it runs on Linux/macOS too, and CI runs the matrix on both.

The TCP/UDP round loops are exercised against a **mock router**
(`tests/mock_router.py`): a loopback socket standing in for the `SIO_RCVALL`
capture socket, fed router-shaped ICMP errors, so `select()`, `recvfrom()` and
the timeouts are the real code paths. That covers reply-to-hop correlation,
out-of-order replies, cross-talk rejection, IP options in either header, and
socket cleanup — none of which any amount of parser testing would have caught.

CI runs `pytest -rs` so every skipped test and its reason appears in the log: a
group that silently skips on half the matrix isn't covering what it appears to.

The raw-socket and Tk layers need real hardware or a display; exercise them by
running the app or `python -m pingerplot.selftest <host>`.

## Project layout

```
main.py            app entry point (python main.py)
PingerPlot.vbs     no-console double-click launcher
launch.ps1         launcher engine: launch / elevate / shortcuts / auto-start / status
Setup.cmd          menu front-end for launch.ps1
run.bat            console launcher (for debugging)
pyproject.toml     packaging metadata + pytest config
pingerplot/
  icmp.py          Windows ICMP via ctypes (IcmpSendEcho/Echo2Ex); dispatches to icmp_posix
  icmp_posix.py    Linux/macOS ICMP: unprivileged SOCK_DGRAM + IP_RECVERR (the mtr technique)
  tcpudp.py        TCP/UDP probes via raw SIO_RCVALL capture (admin), parallel per round
  model.py         Hop / Sample / stats / MOS / CSV hardening (pure, tested)
  compare.py       baseline diff: this run vs a saved one (pure, tested)
  monitor.py       trace + continuous monitor engine, alerts, logging, save/load
  headless.py      no-GUI runner, target supervision, report mode
  gui.py           Tkinter UI: summary grid, hop table, graphs, events, map, compare
  geoip.py         lazy IP geolocation via ipwho.is (Map tab only)
  worldmap.py      baked Natural Earth coastlines (zero-dependency backdrop)
  selftest.py      headless trace/monitor for the console
tests/             pytest for the pure layers + a mock router for the socket layer
```

## Possible extensions

- Field-testing the POSIX backend on real Linux/macOS hardware, and adding a
  live multi-hop trace to CI if a runner can be made to allow it.
- IPv6 (`Icmp6SendEcho2` — different structs, needs a source address).
- Sub-millisecond RTT (would require raw sockets + self-timing).
- Email alert actions (webhook POSTs are already built in).

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
The hard rules: keep it dependency-free, and keep the Win32 access guarded so the
test suite keeps running on any OS.

## License

[MIT](LICENSE) © James Cupps.

PingerPlot is an independent, hobby open-source project and is not affiliated with
or endorsed by any commercial network-monitoring product. It is a **read-only
diagnostic**: it sends probes and reads replies; it never writes to or
reconfigures anything on the network.
