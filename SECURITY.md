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

## Running as administrator: what it trusts

Full TCP and UDP traceroute needs PingerPlot to run as administrator (**Restart as administrator**, or
`launch.ps1 -Elevated` from source). A copy running that way trusts its own folder and every program running as
you:

- The installer puts PingerPlot in `%LOCALAPPDATA%\Programs\PingerPlot`, a folder you can write; a portable copy is
  wherever you unzipped it; from source it's your checkout. A program running as you, not elevated, can change
  files there, and the next elevated start runs them. Windows asks first and names the publisher from
  `PingerPlot.exe`'s signature, but nothing checks the files beside it (`_internal`: the Python runtime, the
  program's code, Tk) when it starts.
- Windows doesn't treat the line between you and your own elevated programs as a security boundary either.

What PingerPlot does control, it keeps closed: an elevated copy takes no log path from `settings.json`, won't write
its history through a link or junction, and doesn't install updates in place (start it normally to update). If
you need the boundary to hold, run the elevated copy only from a folder only administrators can write, or use ICMP,
or TCP with **Destination only**: neither needs administrator rights.

Only the latest release gets fixes.
