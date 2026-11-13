# Roadmap

What's planned or been asked for, roughly in order. Ideas welcome as
[issues](https://github.com/jamesccupps/PingerPlot/issues/new?template=feature_request.yml).

## Next

1.4 (an installer and a portable zip, signed in-app updates, the notification area, Start with Windows, the
new window, a project page) is done; see the [CHANGELOG](CHANGELOG.md). 1.5 (everything recorded, the History
page, reroutes and DNS moves tracked, where routers are and whose, the map's "where the trouble is") is on `main`,
unreleased. Next up:

- **winget and SourceForge** listings, as the sibling projects have, so `winget install PingerPlot` works. The
  SourceForge listing is ready to paste ([packaging/sourceforge](packaging/sourceforge/README.md)).

## Later, or if asked

- **"Where is the problem?" in alerts and webhooks**: the Map's verdict (where loss reaching the destination starts,
  where the delay is added) in the alert text and the webhook payload too.
- **History in the headless runner's reports**: the History page's routes and per-place verdict from the command
  line, for a scheduled check.
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
