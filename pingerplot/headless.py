"""Headless / service mode — run monitors from a config file, no GUI.

For unattended monitoring on a box with no desktop session (a server, a VLAN
probe host). Drives the same engine as the GUI; each target logs to its own
crash-safe, size-capped CSV. Runs until Ctrl-C / SIGTERM, then stops cleanly —
designed to sit behind Windows Task Scheduler.

    python -m pingerplot.headless --init monitor.json   # write a starter config
    python -m pingerplot.headless monitor.json          # run it (Ctrl-C to stop)

Pure standard library (json + argparse + signal), like the rest of the app.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Tuple

from . import __version__, compare as _compare, icmp
from .model import csv_safe, mos, mos_label
from .monitor import Monitor

RESTART_DELAY_S = 30.0       # wait this long before restarting a stopped target
RESTART_DELAY_MAX_S = 300.0  # ...doubling to this ceiling while it keeps failing

SAMPLE_CONFIG = {
    "status_interval": 60,
    "defaults": {
        "interval": 2.5,
        "timeout_ms": 1000,
        "max_hops": 30,
        "packet_type": "icmp",
        "dscp": 0,
        "source_ip": "",
        "resolve_names": True,
        "final_hop_only": False,
        "alert_loss_pct": 20,
        "alert_latency_ms": 250,
        "alert_window": 20,
        "alert_mos": 0,
        "webhook_url": "",
    },
    "targets": [
        {"target": "8.8.8.8", "log_path": "logs/google-dns.csv"},
        {"target": "1.1.1.1", "log_path": "logs/cloudflare.csv", "final_hop_only": True},
    ],
}

# An Event rather than a bare global: the old flag was set by the signal
# handler and never cleared, so a second main() in the same process returned
# immediately. It also lets the run loop wait on it and exit the moment Ctrl-C
# lands instead of up to a quarter second later.
_STOP = threading.Event()


def request_stop() -> None:
    _STOP.set()


def reset_stop() -> None:
    _STOP.clear()


def should_stop() -> bool:
    return _STOP.is_set()


def _target_options(defaults: dict, target_cfg: dict) -> Tuple[str, dict]:
    """Merge ``defaults`` with a target's overrides and return
    ``(target, start_kwargs)``. Unknown keys are ignored; ``alert_sound`` is
    always off (no point beeping a headless box)."""
    opts = dict(defaults)
    opts.update(target_cfg)
    target = str(opts.get("target", "")).strip()
    kwargs = dict(
        interval=float(opts.get("interval", 2.5)),
        timeout_ms=int(opts.get("timeout_ms", 1000)),
        max_hops=int(opts.get("max_hops", 30)),
        resolve_names=bool(opts.get("resolve_names", True)),
        packet_size=int(opts.get("packet_size", 32)),
        send_delay_ms=int(opts.get("send_delay_ms", 0)),
        final_hop_only=bool(opts.get("final_hop_only", False)),
        packet_type=str(opts.get("packet_type", "icmp")),
        port=int(opts.get("port", 443)),
        dscp=int(opts.get("dscp", 0)),
        source_ip=str(opts.get("source_ip", "")),
        log_path=str(opts.get("log_path", "")),
        alert_enabled=bool(opts.get("alert_enabled", True)),
        alert_loss_pct=float(opts.get("alert_loss_pct", 20)),
        alert_latency_ms=float(opts.get("alert_latency_ms", 250)),
        alert_window=int(opts.get("alert_window", 20)),
        alert_mos=float(opts.get("alert_mos", 0)),
        alert_sound=False,
        webhook_url=str(opts.get("webhook_url", "")),
    )
    return target, kwargs


class Target:
    """A configured target, its Monitor, and what is needed to restart it.

    The options are kept because a restart must reproduce the configured run --
    starting again with defaults would silently drop the log path, the alert
    thresholds and the probe mode from the config file.
    """

    __slots__ = ("name", "monitor", "kwargs", "retry_at", "retry_delay", "restarts")

    def __init__(self, name: str, monitor: Monitor, kwargs: dict) -> None:
        self.name = name
        self.monitor = monitor
        self.kwargs = kwargs
        self.retry_at = 0.0            # 0 == not currently scheduled for a retry
        self.retry_delay = RESTART_DELAY_S
        self.restarts = 0


def supervise(targets: List[Target], now: float,
              log: Callable[[str], None] = print) -> None:
    """Restart any target whose monitor has stopped.

    Monitor._run exits with running = False on a name it cannot resolve, or on
    a TCP/UDP traceroute refused for want of Administrator. Nothing used to
    look at that, so a target whose DNS failed at start-up stayed dead for the
    life of the process -- which is the *normal* case for the documented
    deployment, a Task Scheduler job at logon or boot that routinely starts
    before DNS is up. An unattended monitor that silently monitors nothing is
    worse than one that never started: the CSV it should be filling just stays
    empty until somebody goes looking for the data.

    A stopped target is not restarted the instant it is noticed. The first pass
    schedules the retry, the next one past that time performs it, and the delay
    doubles up to RESTART_DELAY_MAX_S while it keeps failing -- so a genuinely
    bad hostname settles into a five-minute poll instead of resolving twice a
    minute forever.
    """
    for t in targets:
        mon = t.monitor
        if mon.running and mon.route_len > 0:
            # Actually monitoring: any earlier backoff is history. Resetting on
            # `running` alone would be wrong, since start() sets it before the
            # trace has had a chance to fail.
            t.retry_at = 0.0
            t.retry_delay = RESTART_DELAY_S
            continue
        if mon.running:
            continue               # started, still tracing -- give it time
        if t.retry_at == 0.0:
            log(f"    {t.name}: stopped ({mon.status}); "
                f"retrying in {t.retry_delay:.0f}s")
            t.retry_at = now + t.retry_delay
            continue
        if now < t.retry_at:
            continue
        t.restarts += 1
        log(f"    {t.name}: restarting (attempt {t.restarts})")
        try:
            mon.start(t.name, **t.kwargs)
        except (OSError, ValueError, TypeError) as exc:
            # One bad target must never take the others down with it.
            log(f"    {t.name}: restart failed: {exc}")
        t.retry_delay = min(t.retry_delay * 2, RESTART_DELAY_MAX_S)
        t.retry_at = now + t.retry_delay


def _build_monitors(cfg: dict, base_dir: Path) -> List[Target]:
    """Start a Monitor per configured target. Relative ``log_path``s resolve
    next to the config file (so it works regardless of the working directory).
    A malformed target is skipped with a warning rather than killing the run."""
    defaults = cfg.get("defaults", {}) or {}
    targets: List[Target] = []
    for tcfg in cfg.get("targets", []):
        try:
            target, kwargs = _target_options(defaults, tcfg)
            if not target:
                continue
            lp = kwargs.get("log_path", "")
            if lp:
                # The makedirs covers absolute paths too. It used to sit inside
                # the relative branch, so an absolute log_path under a
                # directory that did not exist failed to open and logged
                # nothing -- which, until now, said nothing either.
                if not os.path.isabs(lp):
                    kwargs["log_path"] = str(base_dir / lp)
                os.makedirs(os.path.dirname(kwargs["log_path"]) or ".", exist_ok=True)
            m = Monitor()
            m.start(target, **kwargs)
            targets.append(Target(target, m, kwargs))
        except (ValueError, TypeError, OSError) as exc:
            print(f"Skipping target {tcfg!r}: {exc}", file=sys.stderr)
    return targets


def print_status(targets: List[Target], log: Callable[[str], None] = print) -> None:
    """One console line per target - reuses the cheap Monitor.summary()."""
    log(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {len(targets)} target(s):")
    for t in targets:
        m = t.monitor
        n_hops, loss, avg, jitter, reached = m.summary()
        score = mos(avg, jitter, loss) if (n_hops and reached) else None
        loss_s = f"{loss:.0f}%" if loss is not None else "-"
        avg_s = f"{avg:.0f}ms" if avg is not None else "-"
        mos_s = f"{score:.1f}" if score is not None else "-"
        if not m.running:
            tail = f"  (stopped: {m.status})"
        elif not reached:
            tail = "  (no reply yet)"
        else:
            tail = f"  (restarted {t.restarts}x)" if t.restarts else ""
        log(f"    {t.name:<24} hops={n_hops:<3} loss={loss_s:<5} "
            f"avg={avg_s:<8} MOS={mos_s}{tail}")
    sys.stdout.flush()


def report_rows(monitor: Monitor) -> List[dict]:
    """The per-hop summary a report prints, as plain data.

    Kept separate from the formatting so the same numbers feed the console
    table, the CSV, and the tests, instead of each growing its own version.
    """
    views, _status, _ip, _in = monitor.snapshot()
    last_ttl = views[-1].ttl if views else None
    rows = []
    for v in views:
        rows.append({
            "hop": v.ttl,
            "ip": v.address or "*",
            "hostname": v.hostname or "",
            "loss_pct": v.loss_pct,
            "sent": v.sent,
            "last": v.current,
            "avg": v.avg,
            "best": v.best,
            "worst": v.worst,
            "jitter": v.jitter,
            # MOS describes the end-to-end path. On an intermediate router it
            # would be measuring how eagerly that box answers ICMP, not how
            # the path performs, so only the destination gets one.
            "mos": mos(v.avg, v.jitter, v.loss_pct) if v.ttl == last_ttl else None,
            "is_dest": v.ttl == last_ttl,
        })
    return rows


def _num(v, width=6, places=1) -> str:
    return f"{'-':>{width}}" if v is None else f"{v:>{width}.{places}f}"


def format_report(name: str, monitor: Monitor) -> List[str]:
    """An MTR-style table for one target, ready to print or paste in a ticket."""
    rows = report_rows(monitor)
    head = f"{name}  [{monitor.target_ip or '?'}]"
    proto = monitor.packet_type.upper()
    if monitor.packet_type != "icmp":
        proto += f":{monitor.port}"
    # A marked or interface-pinned run measured a different thing from a plain
    # one; the header has to say so or two reports cannot be compared.
    if monitor.dscp:
        proto += f", DSCP {monitor.dscp}"
    if monitor.source_ip:
        proto += f", from {monitor.source_ip}"
    lines = [
        "",
        head,
        f"  {proto}, {monitor.interval:g}s interval, {monitor.timeout_ms}ms timeout"
        f"{'' if monitor.reached_target else '   *** destination never answered ***'}",
        f"  {'Hop':>3}  {'Address':<16} {'Loss':>6} {'Sent':>5} {'Last':>6} "
        f"{'Avg':>6} {'Best':>6} {'Wrst':>6} {'Jitr':>6}  Hostname",
        "  " + "-" * 92,
    ]
    for r in rows:
        marker = "*" if r["is_dest"] else " "
        lines.append(
            f" {marker}{r['hop']:>3}  {r['ip']:<16} {r['loss_pct']:>5.1f}% "
            f"{r['sent']:>5} {_num(r['last'])} {_num(r['avg'])} {_num(r['best'])} "
            f"{_num(r['worst'])} {_num(r['jitter'])}  {r['hostname']}"
        )
    dest = rows[-1] if rows else None
    if dest and dest["mos"] is not None:
        lines.append(f"  destination MOS {dest['mos']:.2f} ({mos_label(dest['mos'])})")
    elif not rows:
        lines.append("  (no hops - nothing answered)")
    return lines


def write_report_csv(path: str, targets: List[Target]) -> None:
    """All targets' rows in one CSV, with a target column so several runs can
    be concatenated and filtered."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["target", "target_ip", "hop", "ip", "hostname", "loss_pct",
                    "sent", "last_ms", "avg_ms", "best_ms", "worst_ms",
                    "jitter_ms", "mos"])
        for t in targets:
            for r in report_rows(t.monitor):
                w.writerow([
                    csv_safe(t.name), t.monitor.target_ip or "",
                    r["hop"], csv_safe(r["ip"]), csv_safe(r["hostname"]),
                    f"{r['loss_pct']:.1f}", r["sent"],
                    *[("" if r[k] is None else f"{r[k]:.1f}")
                      for k in ("last", "avg", "best", "worst", "jitter")],
                    "" if r["mos"] is None else f"{r['mos']:.2f}",
                ])


def run_report(targets: List[Target], rounds: int, log: Callable[[str], None] = print,
               poll: float = 0.25, deadline: float = 0.0) -> int:
    """Collect ``rounds`` monitoring rounds, print a table per target, exit.

    The counterpart to the run-forever loop: something a scheduled job or a
    person writing up a ticket can invoke, get a fixed amount of evidence from,
    and have exit. Returns 0 only if every target reached its destination, so a
    script can branch on it.

    The deadline exists because a target that cannot resolve never advances a
    round at all; without one, a single bad hostname would hang the report
    forever. Anything collected by then is still reported.
    """
    for t in targets:
        t.monitor._round = 0
    if deadline <= 0.0:
        slowest = max((t.monitor.interval for t in targets), default=1.0)
        longest = max((t.monitor.timeout_ms for t in targets), default=1000) / 1000.0
        deadline = rounds * (slowest + longest) + longest * 2 + 15.0
    log(f"Collecting {rounds} round(s) from {len(targets)} target(s)...")

    end = time.monotonic() + deadline
    while not should_stop() and time.monotonic() < end:
        if all(t.monitor._round >= rounds or not t.monitor.running for t in targets):
            break
        _STOP.wait(poll)

    incomplete = [t.name for t in targets if t.monitor._round < rounds]
    if incomplete:
        log(f"  (short of {rounds} rounds: {', '.join(incomplete)})")
    for t in targets:
        for line in format_report(t.name, t.monitor):
            log(line)
    return 0 if all(t.monitor.reached_target for t in targets) else 1


def print_comparison(targets: List[Target], baseline_path: str,
                     log: Callable[[str], None] = print) -> None:
    """Diff each target against a saved session.

    One baseline file against several targets is deliberate: the usual shape is
    one saved session per path, so a mismatch is normal and quiet -- a target
    with nothing to compare against simply says so instead of erroring the run.
    """
    try:
        with open(baseline_path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        log(f"Cannot read baseline {baseline_path}: {exc}")
        return
    base_stats = _compare.stats_from_session(data)
    base_name = str(data.get("target_input") or baseline_path)
    if not base_stats:
        log(f"Baseline {baseline_path} has no hop data to compare against.")
        return
    for t in targets:
        now = [_compare.stats_from_hop(h) for h in t.monitor._hops]
        if not now:
            log("")
            log(f"{t.name}: nothing to compare (no hops yet)")
            continue
        cmp_ = _compare.compare(base_stats, now, target=t.name)
        for line in _compare.format_comparison(cmp_, base_name):
            log(line)


def _warn_if_icmp_unusable(targets: List[Target], out=None) -> bool:
    """Say so when ICMP targets are configured and the backend cannot probe.

    The GUI puts this in a dialog. A headless box is where it matters more,
    because nobody is watching one -- every probe raises inside icmp.ping, gets
    swallowed by _gather_range and is recorded as a timeout, so the operator
    gets a clean-looking report saying the destination never answered and an
    exit status of 1. That points them at the network when the actual fix, on
    Linux, is one sysctl.

    Only fires when a target actually needs the backend: TCP and UDP modes do
    not use it, and warning a TCP-only config would be noise.
    """
    if icmp.is_available():
        return False
    using = [t.name for t in targets if t.monitor.packet_type == "icmp"]
    if not using:
        return False
    out = out if out is not None else sys.stderr
    print(f"WARNING: ICMP backend unavailable - {icmp.unavailable_reason()}",
          file=out)
    print(f"         {len(using)} ICMP target(s) will report no replies: "
          f"{', '.join(using)}", file=out)
    print("         TCP and UDP probe modes do not use this backend.", file=out)
    return True


def _install_signal_handlers() -> None:
    def _handle(_signum, _frame):
        request_stop()
    signal.signal(signal.SIGINT, _handle)
    try:
        signal.signal(signal.SIGTERM, _handle)   # not deliverable on every platform
    except (ValueError, AttributeError, OSError):
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="pingerplot.headless",
        description="Run PingerPlot monitors from a config file, no GUI.")
    ap.add_argument("config", help="path to a JSON config file")
    ap.add_argument("--init", action="store_true",
                    help="write a starter config to CONFIG and exit")
    ap.add_argument("--status-interval", type=float, default=None,
                    help="seconds between status lines (overrides config; 0 = silent)")
    ap.add_argument("--report", type=int, metavar="N", default=None,
                    help="collect N rounds, print a summary table per target, then "
                         "exit. Exit status is 0 only if every target reached its "
                         "destination, so a script can branch on it.")
    ap.add_argument("--report-csv", metavar="PATH", default=None,
                    help="with --report, also write the tables to a CSV file")
    ap.add_argument("--baseline", metavar="PATH", default=None,
                    help="with --report, also diff each target against a saved "
                         "session file (File > Save session in the GUI). Answers "
                         "'is this path worse than it was' rather than only "
                         "'what does it look like now'.")
    args = ap.parse_args(argv)
    cfg_path = Path(args.config)

    if args.init:
        if cfg_path.exists():
            print(f"Refusing to overwrite existing {cfg_path}", file=sys.stderr)
            return 1
        cfg_path.write_text(json.dumps(SAMPLE_CONFIG, indent=2), encoding="utf-8")
        print(f"Wrote starter config to {cfg_path} - edit it, then run without --init.")
        return 0

    try:
        # utf-8-sig, not utf-8: this file is hand-authored on Windows, where
        # PowerShell 5.1's `Set-Content -Encoding utf8` and a number of editors
        # write a byte-order mark. Plain utf-8 rejects it outright, so the
        # service refused to start on a config that looks perfectly fine in
        # every editor that produced it. Reading as utf-8-sig strips a BOM if
        # present and is identical to utf-8 otherwise.
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"Cannot read config {cfg_path}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(cfg, dict) or not cfg.get("targets"):
        print("Config must be a JSON object with a non-empty 'targets' list.", file=sys.stderr)
        return 1

    status_interval = (args.status_interval if args.status_interval is not None
                       else float(cfg.get("status_interval", 60)))

    print(f"PingerPlot {__version__} headless - starting "
          f"{len(cfg['targets'])} target(s). Ctrl-C to stop.")
    targets = _build_monitors(cfg, cfg_path.resolve().parent)
    if not targets:
        print("No valid targets to monitor.", file=sys.stderr)
        return 1
    _warn_if_icmp_unusable(targets)

    reset_stop()                 # a previous run in this process must not linger
    _install_signal_handlers()

    if args.report is not None:
        if args.report < 1:
            print("--report needs a round count of at least 1", file=sys.stderr)
            for t in targets:
                t.monitor.shutdown()
            return 2
        try:
            rc = run_report(targets, args.report)
            if args.baseline:
                print_comparison(targets, args.baseline)
            if args.report_csv:
                write_report_csv(args.report_csv, targets)
                print(f"\nWrote {args.report_csv}")
        except OSError as exc:
            print(f"Report failed: {exc}", file=sys.stderr)
            rc = 1
        finally:
            for t in targets:
                t.monitor.shutdown()
        return rc

    last_status = 0.0
    try:
        while not should_stop():
            now = time.monotonic()
            # Bring back anything that stopped -- a target whose name did not
            # resolve at boot would otherwise stay dead for the whole run.
            supervise(targets, now)
            if status_interval > 0 and now - last_status >= status_interval:
                print_status(targets)
                last_status = now
            _STOP.wait(0.25)     # exits the moment Ctrl-C lands
    except KeyboardInterrupt:
        pass
    finally:
        print("Stopping...")
        for t in targets:
            t.monitor.shutdown()
        print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
