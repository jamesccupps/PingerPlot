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

**Full Description** (1,000 characters, SourceForge's limit, each line break counting as two; this is 974, as live since 1.5.0):

```
PingerPlot shows where along a network path a connection goes wrong. Like mtr or WinMTR, it finds every router between you and a target and keeps probing all of them, so loss, latency, jitter and a voice-quality score (MOS) build up per hop. It shows where each router is and whose network it's on, and where on the path the delay is added and loss begins.

Everything is recorded on your PC: every probe and the router that answered it, alerts, reroutes and DNS moves. A History page shows any hour of the last year: which route was slow, which router dropped probes.

Alerts judge the destination only, so a router that deprioritizes ICMP never false-alarms: a banner, a notification, a sound and a webhook (Slack, Teams, ntfy, Home Assistant). ICMP needs no admin rights; TCP, UDP and DSCP too.

Windows: code-signed installer or portable zip with signed updates, or Scoop. Linux: pipx. Read-only, pure Python, MIT. Source: https://github.com/jamesccupps/PingerPlot
```

**Features** (the Features rows on Admin → Metadata; a new row appears only when you type in the last one):

```
Continuous traceroute: every hop's loss, latency, jitter and MOS over time
Everything recorded; a History page for any hour of the last year
Reroutes reported once, load balancing told apart, DNS moves followed
Where each router is and whose network it's on; where the trouble starts
Alerts on the destination only: banner, notification, sound, webhook
Timeline of every hop on one time axis; a map of the path
ICMP without administrator rights; TCP and UDP probes; DSCP marking
Many targets at once, each with its own settings
CSV export of any range, saved sessions, comparison with an earlier run
Code-signed downloads and signed one-click in-app updates
Headless runner and one-shot reports for servers and Task Scheduler
Installer (no admin), portable zip or Scoop; runs in the notification area
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

**Screenshots** (Admin → Screenshots; SourceForge allows 6, and offers the upload form only below that). Upload
from `site/images/`, with these captions:

| File | Caption |
|---|---|
| `hops-dark.png` | A target alerting: each router's location and network, and the trouble starting at hop 4 |
| `history-light.png` | History: any hour of the last year; the detour through route 2 was slower and lost probes |
| `map-dark.png` | The path on a map, with where the delay is added and where loss begins |
| `timeline-dark.png` | Timeline: every hop on one time axis |
| `events-light.png` | Events: reroutes, alerts raised and cleared |
| `settings-dark.png` | Settings: defaults for new targets, recording, every option described |

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
