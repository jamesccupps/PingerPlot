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

- `PingerPlot-Setup.exe`, `PingerPlot-win64.zip` and `SHA256SUMS.txt` from the
  [releases](https://github.com/jamesccupps/PingerPlot/releases), built by this repository's GitHub Actions
  workflow from the tagged commit, with build tools pinned by hash. From 1.4.4 every exe in them is code-signed by
  James Cupps (Azure Artifact Signing); a missing or wrong signature in a current release is in scope;
- what the app does with data from outside it: ICMP, TCP and UDP replies (parsed from raw bytes), reverse-DNS names
  (shown, and written to CSV with formula injection defused), session files and headless configs (every field
  type-checked), geolocation replies from ipwho.is, and webhook addresses (never logged or shown in full);
- running elevated from source: `launch.ps1 -Elevated` and the shortcuts run the Python the `py` launcher resolves
  to, found unelevated, with `-E -s`, and an elevated copy takes no log path from `settings.json`. The elevated
  logon task older versions could install is retired (1.4.3); `launch.ps1 -UninstallAutostart` removes one.

Only the latest release gets fixes.
