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
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Tuple

from . import __version__
from .model import mos
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
        "resolve_names": True,
        "final_hop_only": False,
        "alert_loss_pct": 20,
        "alert_latency_ms": 250,
        "alert_window": 20,
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
        log_path=str(opts.get("log_path", "")),
        alert_enabled=bool(opts.get("alert_enabled", True)),
        alert_loss_pct=float(opts.get("alert_loss_pct", 20)),
        alert_latency_ms=float(opts.get("alert_latency_ms", 250)),
        alert_window=int(opts.get("alert_window", 20)),
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
            if lp and not os.path.isabs(lp):
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
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
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

    reset_stop()                 # a previous run in this process must not linger
    _install_signal_handlers()
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
