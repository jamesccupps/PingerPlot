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
import time
import urllib.request
from dataclasses import dataclass

_URL = "https://ipwho.is/{ip}?fields=success,latitude,longitude,city,region,country,connection"
_MIN_INTERVAL = 0.8   # seconds between requests (be polite to the free API)
_MAX_BODY = 64 * 1024  # a location object is ~300 bytes; anything near this is wrong
_RETRY_S = 60.0       # after a failed lookup, wait this long before asking again


class LookupFailed(Exception):
    """The service could not be asked (offline, timeout, DNS, HTTP error, a
    captive portal's HTML). Says nothing about the address, so it is retried
    rather than remembered as "no location"."""


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
        self.failures = 0                            # lookups that raised, not just missed
        self._q: queue.Queue[str] = queue.Queue()
        self._seen: set[str] = set()
        self._retry_at: dict[str, float] = {}        # ip -> monotonic time it may be retried
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
        if not ip:
            return
        with self._lock:
            if ip in self._seen or self._retry_at.get(ip, 0.0) > time.monotonic():
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

    def count(self) -> int:
        """How many lookups have resolved so far.

        Results arrive on the worker thread, seconds after the Map view asked
        for them. The GUI folds this into its redraw key so newly-located hops
        actually appear: nothing else about the app changes when a lookup
        lands, so without it the map never repaints to show them.
        """
        with self._lock:
            return len(self.cache)

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                ip = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            self._process(ip)
            self._stop.wait(_MIN_INTERVAL)

    def _process(self, ip: str) -> None:
        try:
            info = self._lookup(ip)
        except LookupFailed:
            # Not an answer about this address. Caching it as "no location"
            # left every hop looked up while offline blank until restart, with
            # the failure counter still at 0. Count it, and let a later redraw
            # ask again once the back-off has passed.
            with self._lock:
                self.failures += 1
                self._seen.discard(ip)
                self._retry_at[ip] = time.monotonic() + _RETRY_S
            return
        except Exception:
            # This thread is started once and never restarted, so anything
            # that escapes _lookup ends geolocation for the rest of the
            # session -- silently, because nothing is watching it. One
            # malformed reply from a third party must not cost the feature.
            info = None
            with self._lock:
                self.failures += 1
        with self._lock:
            self.cache[ip] = info

    def _lookup(self, ip: str) -> GeoInfo | None:
        try:
            req = urllib.request.Request(_URL.format(ip=ip),
                                         headers={"User-Agent": "PingerPlot"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                # Capped: this is one small JSON object from a third party, not
                # a download. urlopen's timeout applies to socket inactivity
                # rather than total transfer, so a slow-drip large body would
                # otherwise be read in full and then handed to json.loads.
                data = json.loads(resp.read(_MAX_BODY).decode("utf-8", "replace"))
        except (OSError, ValueError) as exc:
            # OSError covers URLError, HTTPError (a 429 included) and timeouts;
            # ValueError is a body that is not JSON at all, e.g. a captive
            # portal's login page.
            raise LookupFailed(str(exc)) from exc
        # json.loads returns whatever the body held: a captive portal, a proxy
        # error page or a CDN interstitial can all be valid JSON that is not an
        # object. Only an object has the fields below.
        if not isinstance(data, dict) or not data.get("success"):
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
