# Security

PingerPlot sends probes on your network, reads what comes back, and can run elevated (TCP/UDP traceroute needs
Administrator on Windows), so a security problem in it matters. Please report one privately, not in a public issue.

## Reporting a problem

Use GitHub's private reporting:
**[Report a vulnerability](https://github.com/jamesccupps/PingerPlot/security/advisories/new)** (the repository's
Security tab). Only the maintainer sees it. Please include:

- what's affected, and what someone could do with it;
- how to make it happen (a session file, a headless config, or a capture of the reply that shows it helps);
- the version (**Settings → About** in the window, or `pingerplot-headless --version`) and which copy: the
  installer, the portable zip, pipx on Linux, or from source on Windows, Linux or macOS.

Once it's fixed, the release notes say what was fixed (and who found it, if you'd like that).

## What's covered

- `PingerPlot.exe`, `pingerplot-headless.exe` and `SHA256SUMS.txt` from the
  [releases](https://github.com/jamesccupps/PingerPlot/releases), built by this repository's GitHub Actions
  workflow from the tagged commit, with build tools pinned by hash;
- what the app does with data from outside it: ICMP, TCP and UDP replies (parsed from raw bytes), reverse-DNS names
  (shown, and written to CSV with formula injection defused), session files and headless configs (every field
  type-checked), geolocation replies from ipwho.is, and webhook addresses (never logged or shown in full);
- `launch.ps1`: the logon task it can install runs the app elevated with no UAC prompt, and it checks that what it
  registers can't be changed without elevation. One known limit is documented in the README: the elevated app still
  reads `%APPDATA%\PingerPlot\settings.json`, which you can write without elevation.

Only the latest release gets fixes.
