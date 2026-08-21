"""Monitoring engine: discover the route, then probe every hop continuously.

Mirrors the MTR model — each round re-probes the destination with an
increasing TTL (1..route_len). The router at each TTL answers "TTL exceeded",
so one round is effectively a full traceroute, and we accumulate latency/loss
per hop over time. The final hop (TTL == route_len) is the destination itself.

Because every round is a full retrace, route changes are detected continuously:
  * a hop whose responding address changes  -> reroute (logged)
  * the destination answering at a lower TTL -> route shortened
  * the destination no longer answering      -> probe deeper for a longer route

Sustained-degradation alerts are evaluated on the *destination* hop only — the
one measurement that isn't muddied by routers that deprioritise/ignore ICMP.
An alert is gated on the destination having actually been reached at least once,
so a target that simply blocks ICMP does not raise a false alarm.

Threading model:
  * One worker thread runs :meth:`_run` (trace, then the round loop).
  * Probes within a round fan out across a small thread pool.
  * Reverse-DNS lookups run on their own pool; they never block probing.
  * All shared state is guarded by ``self._lock`` (re-entrant). The GUI never
    touches Hop objects directly — it calls the snapshot accessors, which copy.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Deque, Dict, List, Optional, Tuple

try:
    import winsound  # Windows stdlib; used for the alert beep
except ImportError:  # pragma: no cover - non-Windows
    winsound = None  # type: ignore[assignment]

from . import __version__, icmp, tcpudp
from .model import DEFAULT_HISTORY, Event, Hop, HopView, Sample, mos, mos_label

GROW_PROBE_SPAN = 8       # extra TTLs to probe when looking for a longer route
UNREACHED_BEFORE_GROW = 3  # consecutive dest-miss rounds before probing deeper
MAX_HOPS_CEILING = 64      # hard cap on max_hops, and so on the probe pool width
FINAL_HOP_TTL = 255        # TTL used to ping the destination directly
# Windows does not hand a connecting socket the RST the instant it arrives — it
# finishes retransmitting the SYN first, then surfaces WSAECONNREFUSED. Measured
# at ~2.0 s on Windows 11. Below that a *closed* port is indistinguishable from
# an unreachable host, which is the wrong answer in the most common diagnostic
# case ("the service is down but the box is fine"). Shortening the window with
# TCP_MAXRT does not help: it replaces WSAECONNREFUSED with WSAETIMEDOUT and
# destroys the very distinction the probe exists to draw. So we wait it out.
TCP_REFUSAL_FLOOR_MS = 3000
MAX_LOAD_HISTORY = 50_000  # cap a loaded session's per-hop ring buffer (DoS guard)
MAX_LOG_BYTES = 25 * 1024 * 1024  # roll the probe CSV past ~25 MB (one backup kept)
MAX_LOAD_HOPS = 1024       # cap hops loaded from a session file (defense-in-depth)


def _build_payload(size: int) -> bytes:
    """An ICMP data payload of ``size`` bytes (repeating, recognisable text)."""
    if size <= 0:
        return b""
    base = b"PingerPlot probe "
    return (base * (size // len(base) + 1))[:size]


def _send_webhook(url: str, payload: dict) -> bool:
    """POST ``payload`` as JSON to ``url`` (http/https only). Best-effort: never
    raises, returns True on a 2xx/3xx response. Lets the monitor *tell* you when
    the destination degrades instead of only beeping."""
    if not url.lower().startswith(("http://", "https://")):
        return False
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "PingerPlot"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return 200 <= getattr(resp, "status", 200) < 400
    except Exception:
        return False


class Monitor:
    def __init__(self, on_update: Optional[Callable[[], None]] = None) -> None:
        self.on_update = on_update

        # configuration (set on start)
        self.target_input = ""
        self.target_ip: Optional[str] = None
        self.interval = 2.5
        self.timeout_ms = 1000
        self.max_hops = 30
        self.resolve_names = True
        self.packet_size = 32
        self.send_delay_ms = 0
        self.final_hop_only = False
        self.packet_type = "icmp"   # "icmp" | "tcp" | "udp"
        self.port = 443
        self.dscp = 0               # DiffServ code point (0-63); 0 = best effort
        self.source_ip = ""         # pin the outgoing interface; "" = let the stack pick
        self._local_ip = "0.0.0.0"
        self._tos = 0               # derived from dscp; the byte that goes on the wire
        self._payload = icmp.DEFAULT_PAYLOAD
        self.log_path = ""
        self.timeout_note = ""   # set when start() raises a too-short timeout
        self.log_note = ""       # set when the probe log could not be opened
        self._log_fh = None
        self._round = 0   # 0 = initial trace; 1,2,3… = monitoring rounds
        self.alert_enabled = True
        self.alert_loss_pct = 20.0
        self.alert_latency_ms = 250.0
        self.alert_window = 20
        self.alert_sound = True
        self.alert_mos = 0.0        # 0 disables; MOS alerts BELOW this score
        self.webhook_url = ""

        # live state
        self.status = "Idle"
        self.route_len = 0
        self.reached_target = False
        self.running = False
        self.paused = False

        self._hops: List[Hop] = []
        self._events: Deque[Event] = deque(maxlen=1000)
        self._event_seq = 0
        self._active_alerts: Dict[Tuple[int, str], str] = {}
        self._unreached = 0

        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._generation = 0   # bumped each start(); a worker ignores stale gens
        # Pools are created on start() and torn down on shutdown(); a Monitor
        # that never starts (e.g. the GUI's placeholder) allocates none.
        self._ping_pool: Optional[ThreadPoolExecutor] = None
        self._ping_workers = 0   # width of the live probe pool (see _ensure_pools)
        self._dns_pool: Optional[ThreadPoolExecutor] = None

    def _ensure_pools(self) -> None:
        """(Re)create the probe and DNS thread pools if absent. Lets a Monitor
        be reused after shutdown(), and keeps a never-started one pool-free.

        The probe pool is sized to ``max_hops`` so a whole route fits in ONE
        wave. It used to be a flat 16 while max_hops defaults to 30 and may go
        to 64, so any route past 16 hops silently split into two or more waves
        — and a wave costs a full reply timeout, because a silent router never
        answers. That turned the engine's core promise (a round costs about one
        timeout, not the sum) into a 2x-4x overrun on ordinary internet paths.

        Sizing to the ceiling is free: ThreadPoolExecutor spawns its threads
        lazily as work is submitted, so a 6-hop route still only ever runs six.
        """
        want = max(1, min(int(self.max_hops), MAX_HOPS_CEILING))
        if self._ping_pool is not None and self._ping_workers < want:
            # A later start() asked for a longer route than the live pool can
            # probe at once. Replace it rather than quietly serialising; the
            # old one drains its in-flight probes and exits.
            self._ping_pool.shutdown(wait=False)
            self._ping_pool = None
        if self._ping_pool is None:
            self._ping_pool = ThreadPoolExecutor(max_workers=want, thread_name_prefix="ping")
            self._ping_workers = want
        if self._dns_pool is None:
            self._dns_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="dns")

    # --- lifecycle ---------------------------------------------------------
    def start(
        self,
        target: str,
        interval: float = 2.5,
        timeout_ms: int = 1000,
        max_hops: int = 30,
        resolve_names: bool = True,
        packet_size: int = 32,
        send_delay_ms: int = 0,
        final_hop_only: bool = False,
        packet_type: str = "icmp",
        port: int = 443,
        dscp: int = 0,
        source_ip: str = "",
        log_path: str = "",
        alert_enabled: bool = True,
        alert_loss_pct: float = 20.0,
        alert_latency_ms: float = 250.0,
        alert_window: int = 20,
        alert_sound: bool = True,
        alert_mos: float = 0.0,
        webhook_url: str = "",
    ) -> None:
        self.stop()
        self.target_input = target.strip()
        self.interval = max(0.25, float(interval))
        self.timeout_ms = max(100, int(timeout_ms))
        self.max_hops = max(1, min(int(max_hops), MAX_HOPS_CEILING))
        self.resolve_names = bool(resolve_names)
        self.packet_size = max(0, min(int(packet_size), 1472))
        self.send_delay_ms = max(0, min(int(send_delay_ms), 1000))
        self.final_hop_only = bool(final_hop_only)
        self.packet_type = packet_type if packet_type in ("icmp", "tcp", "udp") else "icmp"
        self.port = max(1, min(int(port), 65535))
        self.dscp = max(0, min(int(dscp), 63))
        self._tos = icmp.dscp_to_tos(self.dscp)
        self.source_ip = (source_ip or "").strip()
        self._payload = _build_payload(self.packet_size)
        self.timeout_note = ""
        if self.packet_type == "tcp" and self.timeout_ms < TCP_REFUSAL_FLOOR_MS:
            # Raise it rather than let the probe report a closed port as an
            # unreachable host. Silently overriding a setting the user typed
            # would be its own bug, so record why for the status line.
            self.timeout_note = (
                f"reply timeout raised {self.timeout_ms} -> {TCP_REFUSAL_FLOOR_MS} ms: "
                f"below that, Windows has not yet surfaced a TCP reset and a closed "
                f"port is indistinguishable from an unreachable host"
            )
            self.timeout_ms = TCP_REFUSAL_FLOOR_MS
        self.log_path = (log_path or "").strip()
        self.alert_enabled = bool(alert_enabled)
        self.alert_loss_pct = max(0.0, float(alert_loss_pct))
        self.alert_latency_ms = max(0.0, float(alert_latency_ms))
        self.alert_window = max(1, int(alert_window))
        self.alert_sound = bool(alert_sound)
        # MOS runs 1.0-5.0; anything above 5 would alert permanently.
        self.alert_mos = max(0.0, min(float(alert_mos), 5.0))
        self.webhook_url = (webhook_url or "").strip()
        with self._lock:
            self._generation += 1
            gen = self._generation
            self._hops = []
            self._events.clear()
            self._active_alerts.clear()
            self._event_seq = 0
            self._unreached = 0
            self.route_len = 0
            self.reached_target = False
            self.target_ip = None
            self.paused = False
        self._open_log()
        self._ensure_pools()
        self.running = True
        self._thread = threading.Thread(target=self._run, args=(gen,), name="monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=self.timeout_ms / 1000 + 2)
        self._thread = None
        self._close_log()

    def shutdown(self) -> None:
        self.stop()
        for pool in (self._ping_pool, self._dns_pool):
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
        self._ping_pool = None
        self._ping_workers = 0
        self._dns_pool = None

    def pause(self) -> None:
        """Halt probing but keep the worker thread and accumulated history.
        resume() continues without the reset a fresh start() would do."""
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    # --- snapshots for the UI thread --------------------------------------
    def snapshot(self) -> Tuple[List[HopView], str, Optional[str], str]:
        with self._lock:
            views = [HopView.of(h) for h in self._hops]
            return views, self.status, self.target_ip, self.target_input

    def summary(self) -> Tuple[int, Optional[float], Optional[float], Optional[float], bool]:
        """Cheap sidebar snapshot: only the destination hop's stats, with no
        per-hop HopView allocation (the grid doesn't need the intermediate hops).
        Returns ``(n_hops, dest_loss, dest_avg, dest_jitter, reached)``; the
        dest values are None when there are no hops yet."""
        with self._lock:
            n = len(self._hops)
            if n == 0:
                return (0, None, None, None, self.reached_target)
            _, _, loss, _, avg, _, _, jitter = self._hops[-1].compute()
            return (n, loss, avg, jitter, self.reached_target)

    def samples_for(self, ttl: int) -> List[Sample]:
        with self._lock:
            for h in self._hops:
                if h.ttl == ttl:
                    return list(h.samples)
        return []

    def all_samples(self) -> Dict[int, List[Sample]]:
        with self._lock:
            return {h.ttl: list(h.samples) for h in self._hops}

    def events_after(self, seq: int) -> Tuple[List[Event], int]:
        """Events with id > ``seq``, plus the newest id seen (for the caller)."""
        with self._lock:
            new = [e for e in self._events if e.seq > seq]
            last = self._event_seq - 1 if self._event_seq else seq
            return new, last

    def active_alerts(self) -> List[str]:
        with self._lock:
            return list(self._active_alerts.values())

    def run_summary(self) -> dict:
        """Run conditions + the wall-clock window the current samples cover, so
        an export can document exactly how it was produced (the missing context
        that made two runs impossible to compare after the fact)."""
        with self._lock:
            times = [s.t for h in self._hops for s in h.samples]
            return {
                "version": __version__,
                "target_input": self.target_input,
                "target_ip": self.target_ip,
                "packet_type": self.packet_type,
                "port": self.port,
                "interval": self.interval,
                "timeout_ms": self.timeout_ms,
                "packet_size": self.packet_size,
                "send_delay_ms": self.send_delay_ms,
                "max_hops": self.max_hops,
                "dscp": self.dscp,
                "source_ip": self.source_ip,
                "final_hop_only": self.final_hop_only,
                "samples": max((h.sent for h in self._hops), default=0),
                "window_start": min(times) if times else None,
                "window_end": max(times) if times else None,
            }

    # --- session persistence ----------------------------------------------
    def to_dict(self) -> dict:
        """Serialisable snapshot of the whole session (config + per-hop samples
        + events) for Save. The GUI json-dumps this."""
        with self._lock:
            return {
                "app": "PingerPlot",
                "version": __version__,
                "saved_at": time.time(),
                "target_input": self.target_input,
                "target_ip": self.target_ip,
                "route_len": self.route_len,
                "reached_target": self.reached_target,
                "final_hop_only": self.final_hop_only,
                "config": {
                    "interval": self.interval,
                    "timeout_ms": self.timeout_ms,
                    "max_hops": self.max_hops,
                    "packet_size": self.packet_size,
                    "send_delay_ms": self.send_delay_ms,
                    "packet_type": self.packet_type,
                    "port": self.port,
                    "dscp": self.dscp,
                    "source_ip": self.source_ip,
                },
                "hops": [
                    {
                        "ttl": h.ttl,
                        "address": h.address,
                        "hostname": h.hostname,
                        "last_status": h.last_status,
                        "samples": [[round(s.t, 3), s.rtt] for s in h.samples],
                    }
                    for h in self._hops
                ],
                "events": [[e.seq, e.t, e.kind, e.text] for e in self._events],
            }

    def load_dict(self, data: dict) -> None:
        """Replace live state with a saved session (for offline review). Stops
        any running monitor first; the result renders through the normal
        snapshot path, just with ``running == False``."""
        self.stop()
        with self._lock:
            self.target_input = str(data.get("target_input", ""))
            self.target_ip = data.get("target_ip")
            self.route_len = int(data.get("route_len", 0) or 0)
            self.reached_target = bool(data.get("reached_target", False))
            self.final_hop_only = bool(data.get("final_hop_only", False))
            cfg = data.get("config", {})
            self.interval = float(cfg.get("interval", self.interval))
            self.timeout_ms = int(cfg.get("timeout_ms", self.timeout_ms))
            self.max_hops = int(cfg.get("max_hops", self.max_hops))
            self.packet_size = int(cfg.get("packet_size", self.packet_size))
            self.send_delay_ms = int(cfg.get("send_delay_ms", self.send_delay_ms))
            self.packet_type = str(cfg.get("packet_type", self.packet_type))
            self.port = int(cfg.get("port", self.port))
            self.dscp = int(cfg.get("dscp", self.dscp))
            self.source_ip = str(cfg.get("source_ip", self.source_ip) or "")
            self._hops = []
            for hd in (data.get("hops", []) or [])[:MAX_LOAD_HOPS]:
                try:
                    samples = hd.get("samples", []) or []
                    # Clamp the ring buffer so a crafted/corrupt session can't
                    # blow up memory; keep the most recent samples.
                    cap = min(MAX_LOAD_HISTORY, max(DEFAULT_HISTORY, len(samples)))
                    hop = Hop(int(hd["ttl"]), history=cap)
                    hop.address = hd.get("address")
                    hop.hostname = hd.get("hostname")
                    hop.last_status = hd.get("last_status")
                    for pair in samples[-cap:]:
                        rtt = pair[1]
                        hop.samples.append(Sample(float(pair[0]), None if rtt is None else float(rtt)))
                    self._hops.append(hop)
                except (KeyError, ValueError, TypeError, IndexError):
                    continue  # skip a malformed hop rather than crash the load
            self._events.clear()
            self._event_seq = 0
            for ev in data.get("events", []):
                try:
                    seq = int(ev[0])
                    self._events.append(Event(seq, float(ev[1]), str(ev[2]), str(ev[3])))
                    self._event_seq = max(self._event_seq, seq + 1)
                except (IndexError, ValueError, TypeError):
                    continue
            self._active_alerts.clear()
            self.status = f"Loaded session: {self.target_input} [{self.target_ip}] - {len(self._hops)} hops"
        self._notify()

    # --- crash-safe probe log ---------------------------------------------
    def _open_log(self) -> None:
        self._close_log()
        self.log_note = ""
        if not self.log_path:
            return
        try:
            fresh = (not os.path.exists(self.log_path)) or os.path.getsize(self.log_path) == 0
            self._log_fh = open(self.log_path, "a", encoding="utf-8", newline="")
            if fresh:
                self._log_fh.write("epoch,iso_time,round,hop,ip,rtt_ms,status\n")
                self._log_fh.flush()
        except OSError as exc:
            self._log_fh = None
            # NOT self.status: _open_log runs inside start(), and the first
            # thing the worker does is set "Resolving ...", so this survived a
            # few milliseconds and was gone. The run then looked entirely
            # normal while writing nothing -- which is the failure the
            # unattended mode exists to avoid. Same treatment as timeout_note:
            # timestamped in the Events tab, where it stays.
            self.log_note = (f"Probe logging DISABLED - cannot open "
                             f"{self.log_path}: {exc}")

    def _close_log(self) -> None:
        fh = self._log_fh
        self._log_fh = None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    def _log_probe(self, ttl: int, r: icmp.PingResult) -> None:
        fh = self._log_fh
        if fh is None:
            return
        now = time.time()
        iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
        rtt = "" if r.rtt_ms is None else f"{r.rtt_ms:.0f}"
        try:
            fh.write(f"{now:.3f},{iso},{self._round},{ttl},{r.address or ''},{rtt},{r.status}\n")
            fh.flush()
        except (OSError, ValueError):
            # ValueError is "I/O operation on closed file". stop() joins the
            # worker with a bounded timeout and then closes the log regardless,
            # so a probe that outlived the join can still be holding this
            # handle. Losing one line to a shutdown race is fine; letting it
            # escape is not — it aborts the round and surfaces as
            # "Monitor error" for what is a benign teardown.
            return
        self._rotate_log_if_needed()

    def _rotate_log_if_needed(self) -> None:
        """Roll the probe CSV over once it passes MAX_LOG_BYTES so an unattended
        multi-week run can't fill the disk. Keeps a single backup
        (``log.csv`` -> ``log.1.csv``); best-effort, skipped on any error."""
        fh = self._log_fh
        if fh is None:
            return
        try:
            if fh.tell() < MAX_LOG_BYTES:
                return
        except (OSError, ValueError):
            return   # same shutdown race as _log_probe: tell() on a closed file
        self._close_log()
        try:
            root, ext = os.path.splitext(self.log_path)
            backup = f"{root}.1{ext}"
            if os.path.exists(backup):
                os.remove(backup)
            os.replace(self.log_path, backup)
        except OSError:
            pass
        self._open_log()   # reopen fresh (writes a new header)

    # --- worker ------------------------------------------------------------
    def _notify(self) -> None:
        if self.on_update is not None:
            try:
                self.on_update()
            except Exception:
                pass

    def _set_status(self, text: str) -> None:
        self.status = text
        self._notify()

    def _log_event(self, kind: str, text: str) -> None:
        with self._lock:
            ev = Event(self._event_seq, time.time(), kind, text)
            self._event_seq += 1
            self._events.append(ev)
        if kind == "alert":
            self._beep()
        if kind in ("alert", "clear"):
            self._fire_webhook(kind, text)

    def _beep(self) -> None:
        if self.alert_sound and winsound is not None:
            try:
                winsound.MessageBeep(winsound.MB_ICONHAND)
            except Exception:
                pass

    def _fire_webhook(self, kind: str, text: str) -> None:
        """Notify a configured webhook (off the worker thread) when a
        destination alert raises or clears."""
        url = self.webhook_url
        if not url:
            return
        payload = {
            "app": "PingerPlot",
            "event": kind,            # "alert" | "clear"
            "target": self.target_input,
            "target_ip": self.target_ip,
            "text": text,
            "time": time.time(),
        }
        threading.Thread(target=_send_webhook, args=(url, payload),
                         name="webhook", daemon=True).start()

    def _alive(self, gen: int) -> bool:
        """True while ``gen`` is still the active run (and we haven't stopped).
        Workers test this instead of ``running`` so a slow worker from a prior
        start() can't keep mutating state once a new run has begun."""
        return self.running and gen == self._generation

    def _run(self, gen: int) -> None:
        try:
            self._set_status(f"Resolving {self.target_input}...")
            try:
                self.target_ip = socket.gethostbyname(self.target_input)
            except OSError as exc:
                self._set_status(f"Cannot resolve '{self.target_input}': {exc.strerror or exc}")
                if gen == self._generation:
                    self.running = False
                return

            if self.timeout_note:
                # Timestamped in the Events tab rather than appended to the
                # status line, which would repeat it on every round.
                self._log_event("info", self.timeout_note)
            if self.log_note:
                self._log_event("warn", self.log_note)

            # An explicit source pins the outgoing interface -- the point of
            # the setting on a multi-homed box, where the route table would
            # otherwise decide for you and you could not ask "what does this
            # path look like from the other VLAN".
            self._local_ip = self.source_ip or tcpudp.local_ip_for(self.target_ip)
            needs_capture = self.packet_type != "icmp" and not (
                self.final_hop_only and self.packet_type == "tcp"
            )
            if needs_capture and not tcpudp.capture_supported(self._local_ip):
                self._set_status(
                    f"{self.packet_type.upper()} traceroute needs Administrator (raw capture). "
                    "Re-run elevated, switch to ICMP, or use TCP with 'Final hop only'."
                )
                if gen == self._generation:
                    self.running = False
                return

            if self.final_hop_only:
                with self._lock:
                    self._ensure_hop(1)
                    self.route_len = 1
                route_len = 1
            else:
                self._set_status(f"Tracing route to {self.target_input} [{self.target_ip}]...")
                route_len = self._trace(gen)
                if not self._alive(gen):
                    return
                if route_len == 0:
                    self._set_status(f"No reply from {self.target_input} - target may block ICMP or be down.")
                    if gen == self._generation:
                        self.running = False
                    return

            while self._alive(gen):
                t0 = time.perf_counter()
                if self.paused:
                    self._set_status(f"Paused - {self.target_input} [{self.target_ip}] "
                                     f"({len(self._hops)} hops, history kept)")
                    self._notify()
                    self._interruptible_sleep(self.interval, gen)
                    continue
                route_len = self._probe_round(route_len, gen)
                self._evaluate_alerts(route_len)
                self._set_status(self._monitor_status(route_len))
                self._notify()
                self._interruptible_sleep(self.interval - (time.perf_counter() - t0), gen)
        except Exception as exc:  # never let the worker die silently
            self._set_status(f"Monitor error: {exc!r}")
        finally:
            if gen == self._generation:   # don't clobber a newer run's flag
                self.running = False
            self._notify()

    def _monitor_status(self, route_len: int) -> str:
        proto = self.packet_type.upper()
        if self.packet_type != "icmp":
            proto += f":{self.port}"
        if self.dscp:
            proto += f" DSCP {self.dscp}"
        if self.source_ip:
            proto += f" from {self.source_ip}"
        scope = "destination only" if self.final_hop_only else f"{route_len} hops"
        base = f"Monitoring {self.target_input} [{self.target_ip}] via {proto} - {scope}"
        if not self.reached_target:
            base += " (destination not answering)"
        n_alerts = len(self._active_alerts)
        if n_alerts:
            base += f"  -  {n_alerts} active alert{'s' if n_alerts != 1 else ''}"
        return base

    def _ensure_hop(self, ttl: int) -> Hop:
        # Caller holds the lock. Hops are stored densely, index == ttl-1.
        while len(self._hops) < ttl:
            self._hops.append(Hop(len(self._hops) + 1))
        return self._hops[ttl - 1]

    def _apply_probe(self, ttl: int, r: icmp.PingResult, gen: int) -> None:
        """Record one probe result and react to any route change at this hop.
        A result carrying a superseded generation is dropped, so a slow worker
        from a previous start() cannot corrupt the freshly-reset new run."""
        with self._lock:
            if gen != self._generation:
                return
            hop = self._ensure_hop(ttl)
            old = hop.address
            hop.record(r.rtt_ms, r.address, r.status)
        self._log_probe(ttl, r)
        if r.reached:
            self.reached_target = True
        if old and r.address and r.address != old:
            self._log_event("route", f"Hop {ttl}: path changed {old} -> {r.address}")
        if r.address:
            self._maybe_resolve(ttl, r.address)

    def _trace(self, gen: int) -> int:
        """Walk TTLs upward until the destination answers. Returns route length
        (the TTL that reached the target), the deepest responding TTL if the
        destination never replies, or 0 if nothing answered at all."""
        self._round = 0
        reached_ttl = 0
        deepest = 0
        for ttl in range(1, self.max_hops + 1):
            if not self._alive(gen):
                break
            r = self._do_probe(ttl)
            self._apply_probe(ttl, r, gen)
            if r.address:
                deepest = ttl
            self._set_status(f"Tracing route... hop {ttl}: {r.address or '*'}")
            if r.reached:
                reached_ttl = ttl
                break

        route_len = reached_ttl or deepest
        with self._lock:
            del self._hops[route_len:]  # trim trailing all-timeout hops
            self.route_len = route_len
        return route_len

    def _do_probe(self, ttl: int, ip_ttl: Optional[int] = None) -> icmp.PingResult:
        """One probe via the configured backend. ``ip_ttl`` overrides the IP TTL
        (used for final-hop-only, which pings the destination at full TTL)."""
        actual_ttl = ttl if ip_ttl is None else ip_ttl
        if self.packet_type == "icmp":
            return icmp.ping(self.target_ip, actual_ttl, self.timeout_ms,
                             self._payload, self._tos, self.source_ip or None)
        return tcpudp.probe(
            self.target_ip, actual_ttl, self.timeout_ms,
            self.packet_type, self.port, self._local_ip, self._payload, self._tos,
        )

    def _gather_range(self, lo: int, hi: int) -> Dict[int, icmp.PingResult]:
        """Probe TTLs lo..hi. ICMP fans out across the pool (honouring send
        delay); TCP/UDP go through one parallel round on a shared capture socket
        (~one timeout total instead of the sum)."""
        if self.packet_type != "icmp":
            return tcpudp.probe_path(self.target_ip, range(lo, hi + 1), self.timeout_ms,
                                     self.packet_type, self.port, self._local_ip,
                                     self._payload, self._tos)
        results: Dict[int, icmp.PingResult] = {}
        if self._ping_pool is None:     # pools torn down (e.g. mid-shutdown)
            return {ttl: icmp.PingResult(icmp.IP_REQ_TIMED_OUT, None, None, False)
                    for ttl in range(lo, hi + 1)}
        futures = {}
        for ttl in range(lo, hi + 1):
            if not self.running:        # abort promptly on stop()
                break
            futures[ttl] = self._ping_pool.submit(self._do_probe, ttl)
            if self.send_delay_ms and ttl < hi:
                time.sleep(self.send_delay_ms / 1000.0)
        for ttl, fut in futures.items():
            try:
                results[ttl] = fut.result()
            except Exception:
                results[ttl] = icmp.PingResult(icmp.IP_REQ_TIMED_OUT, None, None, False)
        return results

    def _probe_round(self, route_len: int, gen: int) -> int:
        """One monitoring round across all hops. Returns the (possibly adjusted)
        route length so the caller tracks grow/shrink continuously."""
        self._round += 1
        if self.final_hop_only:
            r = self._do_probe(1, ip_ttl=FINAL_HOP_TTL)
            self._apply_probe(1, r, gen)
            return 1

        results = self._gather_range(1, route_len)
        min_reached: Optional[int] = None
        for ttl in range(1, route_len + 1):
            if not self._alive(gen):    # stop()/superseded during the round:
                break                   # don't keep recording a stale round
            r = results.get(ttl)
            if r is None:
                r = icmp.PingResult(icmp.IP_REQ_TIMED_OUT, None, None, False)
            self._apply_probe(ttl, r, gen)
            if r.reached and min_reached is None:
                min_reached = ttl

        if min_reached is not None:
            self._unreached = 0
            if min_reached < route_len:
                return self._shrink_route(route_len, min_reached)
            return route_len

        # Destination missed this round. If we have ever reached it, the route
        # may have grown; otherwise it is ordinary loss and the alert path deals
        # with it.
        if self.reached_target:
            self._unreached += 1
            if self._unreached >= UNREACHED_BEFORE_GROW:
                grown = self._grow_route(route_len, gen)
                if grown:
                    self._unreached = 0
                    return grown
        return route_len

    def _shrink_route(self, old_len: int, new_len: int) -> int:
        with self._lock:
            del self._hops[new_len:]
            self.route_len = new_len
        self._log_event("route", f"Route shortened {old_len} -> {new_len} hops (destination now closer)")
        return new_len

    def _grow_route(self, route_len: int, gen: int) -> Optional[int]:
        """Probe past the current end to find the destination's new distance."""
        upper = min(self.max_hops, route_len + GROW_PROBE_SPAN)
        if upper <= route_len:
            return None
        results = self._gather_range(route_len + 1, upper)
        reached = [ttl for ttl, r in results.items() if r.reached]
        if not reached and upper < self.max_hops:
            results.update(self._gather_range(upper + 1, self.max_hops))
            reached = [ttl for ttl, r in results.items() if r.reached]
        if not reached:
            return None
        new_len = min(reached)
        for ttl in range(route_len + 1, new_len + 1):
            r = results.get(ttl)
            if r is None:
                r = self._do_probe(ttl)
            self._apply_probe(ttl, r, gen)
        with self._lock:
            del self._hops[new_len:]
            self.route_len = new_len
        self._log_event("route", f"Route lengthened {route_len} -> {new_len} hops (destination now farther)")
        return new_len

    # --- alerts ------------------------------------------------------------
    def _evaluate_alerts(self, route_len: int) -> None:
        if not self.alert_enabled or not self.reached_target or route_len < 1:
            self._clear_all_alerts()
            return
        with self._lock:
            if route_len > len(self._hops):
                return
            dest = self._hops[route_len - 1]
            sent_window = min(self.alert_window, dest.sent)
            ever = dest.received
            # One pass for all three: the MOS alert needs jitter as well, and
            # the E-model wants latency, jitter and loss from the same window.
            r_loss, r_avg, r_jitter = dest.recent_stats(self.alert_window)
            ttl = dest.ttl
            name = dest.hostname or dest.address or "?"

        # Alerts are keyed by TTL, and only the destination's TTL is ever
        # evaluated. A reroute that changes the path length moves the
        # destination to a different TTL, orphaning the previous key: nothing
        # would revisit it, so the banner kept reporting loss on a hop that no
        # longer exists. Retire those explicitly, before the history check
        # below — the new destination not having enough history yet is a reason
        # to say nothing about it, not a reason to keep showing the old one.
        self._retire_alerts(keep_ttl=ttl, reason="route changed")

        if sent_window < self.alert_window:
            return  # not enough history yet to call anything "sustained"

        loss_active = ever > 0 and self.alert_loss_pct > 0 and r_loss >= self.alert_loss_pct
        self._set_alert(
            (ttl, "loss"),
            loss_active,
            f"Hop {ttl} ({name}): {r_loss:.0f}% packet loss over last {self.alert_window} probes",
        )

        lat_active = self.alert_latency_ms > 0 and r_avg is not None and r_avg >= self.alert_latency_ms
        self._set_alert(
            (ttl, "lat"),
            lat_active,
            f"Hop {ttl} ({name}): avg {r_avg:.0f} ms over last {self.alert_window} probes"
            if r_avg is not None else "",
        )

        # MOS folds latency, jitter and loss into one number, so it catches the
        # combination that ruins a call while each ingredient sits under its own
        # threshold. Note the direction: 1.0-5.0, and *lower* is worse, so this
        # one fires at or BELOW its threshold. Gated on ever > 0 like the loss
        # alert — a destination that has never answered has no score to judge.
        r_mos = mos(r_avg, r_jitter, r_loss) if ever > 0 else None
        mos_active = self.alert_mos > 0 and r_mos is not None and r_mos <= self.alert_mos
        self._set_alert(
            (ttl, "mos"),
            mos_active,
            f"Hop {ttl} ({name}): MOS {r_mos:.1f} ({mos_label(r_mos)}) "
            f"over last {self.alert_window} probes" if r_mos is not None else "",
        )

    def _set_alert(self, key: Tuple[int, str], active: bool, text: str) -> None:
        with self._lock:
            present = key in self._active_alerts
            if active and not present:
                self._active_alerts[key] = text
                fire = True
            elif active and present:
                self._active_alerts[key] = text  # refresh the live value
                fire = False
            elif (not active) and present:
                cleared = self._active_alerts.pop(key)
                self._log_event("clear", f"Cleared: {cleared}")
                return
            else:
                return
        if fire:
            self._log_event("alert", f"ALERT: {text}")

    def _clear_all_alerts(self) -> None:
        self._retire_alerts(keep_ttl=None)

    def _retire_alerts(self, keep_ttl: Optional[int], reason: str = "") -> None:
        """Clear active alerts that will never be evaluated again.

        ``keep_ttl=None`` clears everything (alerts switched off, destination
        no longer reachable). Otherwise only ``keep_ttl`` survives — every
        other key belongs to a hop that is no longer the destination.

        The lock is released between the pop and the log, because _log_event
        beeps and fires the webhook and neither should happen holding it.
        """
        with self._lock:
            stale = [k for k in self._active_alerts
                     if keep_ttl is None or k[0] != keep_ttl]
        suffix = f" ({reason})" if reason else ""
        for key in stale:
            with self._lock:
                cleared = self._active_alerts.pop(key, None)
            if cleared:
                self._log_event("clear", f"Cleared{suffix}: {cleared}")

    # --- reverse DNS (best effort, off the probe path) --------------------
    def _maybe_resolve(self, ttl: int, address: str) -> None:
        if not self.resolve_names or self._dns_pool is None:
            return
        with self._lock:
            if ttl > len(self._hops):
                return
            hop = self._hops[ttl - 1]
            if hop.resolving or (hop.hostname is not None and hop.address == address):
                return
            hop.resolving = True
        self._dns_pool.submit(self._resolve_worker, ttl, address)

    def _resolve_worker(self, ttl: int, address: str) -> None:
        try:
            name = socket.gethostbyaddr(address)[0]
        except OSError:
            name = ""  # no PTR record; remember that so we don't retry forever
        with self._lock:
            if ttl <= len(self._hops):
                hop = self._hops[ttl - 1]
                if hop.address == address:
                    hop.hostname = name
                hop.resolving = False

    def _interruptible_sleep(self, seconds: float, gen: int) -> None:
        end = time.perf_counter() + max(0.0, seconds)
        while self._alive(gen):
            remaining = end - time.perf_counter()
            if remaining <= 0:
                break
            time.sleep(min(0.1, remaining))
