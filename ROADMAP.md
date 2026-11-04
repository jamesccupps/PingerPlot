# Roadmap

What's planned or been asked for, roughly in order. Ideas welcome as
[issues](https://github.com/jamesccupps/PingerPlot/issues/new?template=feature_request.yml).

## Next: 1.4, a proper Windows app

Bringing PingerPlot up to the same standard as its sibling projects (Slate, UniFi Stock Watcher):

- **An installer and a portable zip** instead of two bare executables: installs for your Windows user with no admin
  prompt, Start menu shortcut, uninstalls from Settings → Apps; the zip keeps its settings beside it.
- **Signed in-app updates**: a daily check of this repository's releases, one-click install, and a download is
  installed only if it carries the release signature built into the app.
- **Runs in the background**: a notification-area icon that shows whether any target is alerting, closing the window
  keeps monitoring, one copy at a time, and *Start with Windows* without elevation.
- **Follows Windows**: light or dark to match the system theme, at any display scale.
- **A project page** on GitHub Pages, with screenshots taken from a demo mode (made-up addresses, never a real path).

## Later, or if asked

- **"Where is the problem?"** A one-line verdict from the hop statistics: the first hop whose loss or latency carries
  through to the destination ("problem starts at hop 4: +38 ms, 12% loss"), and which loss to ignore because it stops
  at an intermediate router. For the status line, alerts and webhooks.
- **Long-term history**: per-minute summaries kept for months in `sqlite3` (standard library), with a time range on
  the graphs, so "what did the path do at 3 a.m. Tuesday?" has an answer without a CSV and a spreadsheet.
- **Webhook formats**: ntfy (title, priority, tags), Slack/Teams, or the current generic JSON; a *Send test* button;
  an optional "still alerting" reminder.
- **Service checks in final-hop mode**: BACnet ReadProperty (UDP 47808), DNS query time and HTTPS time to first
  byte, through the existing probe interface so statistics, alerts and logging work unchanged.
- **Path MTU discovery**: find the largest packet that gets through unfragmented, to catch MTU black holes on VPN and
  PPPoE links.
- **A metrics endpoint for headless mode** (Prometheus format, localhost only by default) for Grafana or Home
  Assistant.
- **Shared hops across targets**: when every target degrades at the same hop at once, it's your side of the path.
- **A multi-hop network-namespace lab in CI**, so traces and reroutes are tested on every push, not just loopback
  and the first hop.
- **macOS** testing on real hardware (the backend is written for it but has never run on a Mac).
- Sub-millisecond RTT for Windows ICMP, which `IcmpSendEcho` reports in whole milliseconds.
- IPv6.

## Not planned

- Raw sockets or Npcap for ICMP: needing no administrator rights and no capture driver is the point.
- Anything that changes the network it measures. PingerPlot only sends probes and reads replies.
