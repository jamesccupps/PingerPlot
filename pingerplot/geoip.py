"""Lazy IP geolocation for the map view.

Looks up public hop addresses via ipwho.is (free, HTTPS, no key) on a background
thread, caching results. This is the only part of the app that talks to a third
party — it is opt-in (only runs when the Map view is active) and skips private /
reserved addresses, which have no meaningful location anyway.
"""

from __future__ import annotations

import ipaddress
import json
import queue
import threading
import urllib.request
from dataclasses import dataclass

_URL = "https://ipwho.is/{ip}?fields=success,latitude,longitude,city,region,country,connection"
_MIN_INTERVAL = 0.8   # seconds between requests (be polite to the free API)


@dataclass(slots=True)
class GeoInfo:
    lat: float
    lon: float
    city: str
    region: str
    country: str
    isp: str


def _is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_multicast or addr.is_reserved or addr.is_unspecified)


class GeoResolver:
    def __init__(self) -> None:
        self.cache: dict[str, GeoInfo | None] = {}   # None == looked up, no data
        self._q: queue.Queue[str] = queue.Queue()
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def request(self, ip: str | None) -> None:
        if not ip or ip in self._seen:
            return
        self._seen.add(ip)
        if not _is_public(ip):
            with self._lock:
                self.cache[ip] = None
            return
        self._q.put(ip)

    def get(self, ip: str | None) -> GeoInfo | None:
        if not ip:
            return None
        with self._lock:
            return self.cache.get(ip)

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                ip = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            info = self._lookup(ip)
            with self._lock:
                self.cache[ip] = info
            self._stop.wait(_MIN_INTERVAL)

    def _lookup(self, ip: str) -> GeoInfo | None:
        try:
            req = urllib.request.Request(_URL.format(ip=ip),
                                         headers={"User-Agent": "PingerPlot"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except (OSError, ValueError):
            return None
        if not data.get("success"):
            return None
        try:
            conn = data.get("connection") or {}
            return GeoInfo(
                lat=float(data["latitude"]),
                lon=float(data["longitude"]),
                city=data.get("city") or "",
                region=data.get("region") or "",
                country=data.get("country") or "",
                isp=conn.get("isp") or conn.get("org") or "",
            )
        except (KeyError, TypeError, ValueError):
            return None
