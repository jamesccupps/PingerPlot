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
import time
from pathlib import Path
from typing import List, Tuple

from . import __version__
from .model import mos
from .monitor import Monitor

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

_STOP = False


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


def _build_monitors(cfg: dict, base_dir: Path) -> List[Tuple[str, Monitor]]:
    """Start a Monitor per configured target. Relative ``log_path``s resolve
    next to the config file (so it works regardless of the working directory).
    A malformed target is skipped with a warning rather than killing the run."""
    defaults = cfg.get("defaults", {}) or {}
    monitors: List[Tuple[str, Monitor]] = []
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
            monitors.append((target, m))
        except (ValueError, TypeError, OSError) as exc:
            print(f"Skipping target {tcfg!r}: {exc}", file=sys.stderr)
    return monitors


def _print_status(monitors: List[Tuple[str, Monitor]]) -> None:
    """One console line per target — reuses the cheap Monitor.summary()."""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {len(monitors)} target(s):")
    for name, m in monitors:
        n_hops, loss, avg, jitter, reached = m.summary()
        score = mos(avg, jitter, loss) if (n_hops and reached) else None
        loss_s = f"{loss:.0f}%" if loss is not None else "-"
        avg_s = f"{avg:.0f}ms" if avg is not None else "-"
        mos_s = f"{score:.1f}" if score is not None else "-"
        tail = "" if reached else "  (no reply yet)"
        print(f"    {name:<24} hops={n_hops:<3} loss={loss_s:<5} avg={avg_s:<8} MOS={mos_s}{tail}")
    sys.stdout.flush()


def _install_signal_handlers() -> None:
    def _handle(_signum, _frame):
        global _STOP
        _STOP = True
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
    monitors = _build_monitors(cfg, cfg_path.resolve().parent)
    if not monitors:
        print("No valid targets to monitor.", file=sys.stderr)
        return 1

    _install_signal_handlers()
    last_status = 0.0
    try:
        while not _STOP:
            now = time.monotonic()
            if status_interval > 0 and now - last_status >= status_interval:
                _print_status(monitors)
                last_status = now
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        print("Stopping...")
        for _name, m in monitors:
            m.shutdown()
        print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
