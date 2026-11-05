# SourceForge mirror

Everything needed to put PingerPlot on SourceForge: the project's settings, the listing text to paste, and how
releases get there. GitHub stays the source of truth. SourceForge mirrors the release files; the code, the issues
and the in-app updater all stay on GitHub (installed copies update from GitHub's `latest.json`, never from
SourceForge).

## 1. Create the project

Go to <https://sourceforge.net/p/add_project> (sign in or register first; a new account may be asked for phone
verification).

| Field | Enter |
|---|---|
| Project Name | `PingerPlot` |
| URL name | `pingerplot` (free as of 2026-11-04; 3-30 letters, digits and dashes, and it can't be changed later without a support ticket) |
| Tools | **Files** and **Reviews** only. Leave out Code, Tickets, Discussion and Wiki: those live on GitHub, and a second copy would drift. |

The project will be at `https://sourceforge.net/projects/pingerplot/`.

Don't use the GitHub *Importer* to create it. It makes a one-time copy of the code, issues and wiki, and that copy
then goes stale. Step 3 brings in the releases only, and keeps them coming.

## 2. Fill in the listing

**Admin → Metadata** (on the project page, click the lock icon to unlock the menu, then **Admin**):

| Field | Enter |
|---|---|
| Name | `PingerPlot` |
| Home Page | `https://jamesccupps.github.io/PingerPlot/` |
| Support Page | `https://github.com/jamesccupps/PingerPlot/issues` |
| Project Logo | `logo.png` from this folder (128 x 128, drawn by the app's own icon code) |
| Video | leave empty |

**Short Summary** (70 characters, SourceForge's limit):

```
Continuous traceroute: per-hop loss, latency, jitter and MOS over time
```

**Full Description** (1,000 characters, SourceForge's limit; this is 962):

```
PingerPlot shows where along a network path a connection goes wrong. Like mtr or WinMTR, it finds every router between you and a target and keeps probing all of them, so loss, latency, jitter and a voice-quality score (MOS) build up per hop over minutes or days. A problem shows up at the hop where it starts and every hop after it.

Alerts judge the destination only, so a router that deprioritises ICMP never false-alarms: a banner, a desktop notification, a sound and an optional webhook (Slack, Teams, ntfy, Home Assistant). Route changes are caught within a round, and a map places the public hops.

ICMP needs no admin rights; TCP, UDP and DSCP marking are there too. Sessions save, reopen and compare, and a headless runner monitors from a server. Windows: installer or portable zip with signed updates. Linux: pipx.

Read-only: it never changes anything on the network. Pure Python, no dependencies, MIT. Source: https://github.com/jamesccupps/PingerPlot
```

**Features** (Admin → Features, if the project shows that page; one per line):

```
Continuous traceroute: every hop's loss, latency, jitter and MOS over time
Alerts on the destination only: banner, Windows notification, sound, webhook
Route-change detection, live
Timeline of every hop on one time axis; world map of the public hops
ICMP without administrator rights; TCP and UDP probes; DSCP marking
Many targets at once, each with its own settings
Probe log to CSV, saved sessions, and comparison with an earlier run
Runs in the notification area; Start with Windows
Signed one-click in-app updates
Headless runner and one-shot reports for servers and Task Scheduler
Installer (no admin) or portable zip; light and dark themes that follow Windows
```

**Categories** (Admin → Categorization; pick the nearest SourceForge offers):

| Category | Choose |
|---|---|
| License | MIT License |
| Operating System | Windows (and Windows 10 / Windows 11 where listed) |
| Programming Language | Python |
| User Interface | Tk; Win32 (MS Windows) |
| Intended Audience | System Administrators; Information Technology; End Users/Desktop |
| Topic | Networking; Monitoring; Internet |
| Development Status | 5 - Production/Stable |
| Translations | English |

**Screenshots** (Admin → Screenshots). Upload from `site/images/`, with these captions:

| File | Caption |
|---|---|
| `hops-dark.png` | A target alerting: the hop table shows the trouble starting at hop 4 and carrying through to the destination |
| `timeline-dark.png` | Timeline: every hop on one time axis |
| `hops-light.png` | The light theme |
| `events-light.png` | Events: route changes and alerts as they happen |
| `map-dark.png` | Map: the path's public hops |
| `settings-dark.png` | Settings: defaults for new targets, every option described |

They come from the app's demo mode (simulated paths, documentation addresses only), never a real network.

## 3. Bring in the releases

On the project page: click the lock icon to unlock the menu, then the gear icon next to **Files** → **GitHub
Integration**. Enter `jamesccupps/PingerPlot` and approve the connection.

From then on, every new GitHub release's files are imported automatically shortly after it's published.

- **1.4.0 was published before the integration existed**, so it isn't imported by itself. SourceForge re-checks a
  release when its text is edited: edit the v1.4.0 release's notes on GitHub (for example, add the line
  `Also on SourceForge: https://sourceforge.net/projects/pingerplot/`) and save.
- Leave the 1.3.x releases out. They are two bare executables with no updater; 1.4.0 is the first release in the
  shape SourceForge should offer.
- SourceForge doesn't notice files added to a release after it was published. The release workflow attaches every
  file in one go right after creating the release (`latest.json` last, seconds later), so that's fine as long as
  releases aren't hand-edited to add files. If an import ever misses a file, editing the release notes again
  re-triggers it.

## 4. The default download

Once 1.4.0's files are in **Files**, hover over `PingerPlot-Setup.exe`, click its info (**i**) button, and tick it
as the default download for **Windows**. The big green button on the project page then offers the installer. It
keeps offering the file you chose, so choose the new installer after each release.

## 5. Afterwards

- The download counts take a few hours to start.
- Point people at GitHub for everything but the files: the Support Page above does that; if Reviews ask questions,
  answer with a link to the issues.
- Add the SourceForge link to the README's download line and the site footer once the project is live.
