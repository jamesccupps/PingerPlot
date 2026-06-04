"""Headless trace + monitor, printed to the console.

Useful for verifying the ICMP backend without launching the GUI:

    python -m pingerplot.selftest 8.8.8.8
    python -m pingerplot.selftest cloudflare.com --rounds 5 --interval 1
"""
from __future__ import annotations

import argparse
import time

from . import icmp
from .model import HopView
from .monitor import Monitor


def _ms(v):
    return "   *" if v is None else f"{v:6.0f}"


def _print_table(views):
    print(f"\n{'Hop':>3}  {'IP':<16} {'Loss':>5} {'Sent':>4} {'Cur':>6} {'Avg':>6} {'Min':>6} {'Max':>6}  Host")
    print("-" * 86)
    for v in views:  # type: HopView
        print(
            f"{v.ttl:>3}  {(v.address or '*'):<16} {v.loss_pct:>4.0f}% {v.sent:>4} "
            f"{_ms(v.current)} {_ms(v.avg)} {_ms(v.best)} {_ms(v.worst)}  {v.hostname or ''}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Headless PingerPlot trace/monitor.")
    ap.add_argument("target", help="hostname or IPv4 address")
    ap.add_argument("--rounds", type=int, default=3, help="monitor rounds to run (default 3)")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between rounds")
    ap.add_argument("--max-hops", type=int, default=30)
    ap.add_argument("--no-resolve", action="store_true", help="skip reverse DNS")
    args = ap.parse_args()

    if not icmp.is_available():
        print("ICMP backend requires Windows.")
        return 2

    mon = Monitor()
    mon.start(
        args.target,
        interval=args.interval,
        max_hops=args.max_hops,
        resolve_names=not args.no_resolve,
    )

    # Wait for the trace to finish (status flips to "Monitoring …" or stops).
    while mon.running and not mon.status.startswith("Monitoring"):
        time.sleep(0.1)
        if not mon.running:
            print(mon.status)
            return 1
    print(mon.status)

    deadline = time.perf_counter() + args.rounds * args.interval + 2
    seen = -1
    while mon.running and time.perf_counter() < deadline:
        views, *_ = mon.snapshot()
        if views and views[0].sent != seen:
            seen = views[0].sent
            _print_table(views)
            if seen >= args.rounds + 1:  # +1 for the trace probe
                break
        time.sleep(0.2)

    mon.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
