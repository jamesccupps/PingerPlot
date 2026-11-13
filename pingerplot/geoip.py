"""Where a hop's router is and whose network it's on, from ipwho.is.

Public hop addresses are looked up (free, HTTPS, no key) on a background
thread, one at a time and 0.8 s apart. Since 1.5 this runs while monitoring,
not only when the Map is open, so the hop table, the History page and the map
can say where a router is and whose it is; Settings -> "Look up where routers
are" turns it off. Only router addresses are sent: private, LAN, carrier-NAT
and other non-global addresses never are.

Answers are kept on disk (``lookups.json`` in the settings folder) and asked
again after 30 days, so a router is looked up about once a month, however
long you monitor. The free service's limits aren't well published, so the
resolver also stops itself at 500 lookups a day and, told it's asking too
often (HTTP 429), waits an hour.
"""

from __future__ import annotations

import ipaddress
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

_URL = "https://ipwho.is/{ip}?fields=success,latitude,longitude,city,region,country,connection"
_MIN_INTERVAL = 0.8   # seconds between requests (be polite to the free API)
_MAX_BODY = 64 * 1024  # a location object is ~300 bytes; anything near this is wrong
_RETRY_S = 60.0       # after a failed lookup, wait this long before asking again
_KEEP_S = 30 * 86400  # an answer is asked again after this long
_DAILY_CAP = 500      # lookups a day at most, whatever is being monitored
_BACKOFF_429_S = 3600.0
_SAVE_EVERY_S = 10.0
_MAX_KEPT = 20_000    # entries in the file; the oldest go first
CACHE_NAME = "lookups.json"


class LookupFailed(Exception):
    """The service could not be asked (offline, timeout, DNS, HTTP error, a
    captive portal's HTML). Says nothing about the address, so it is retried
    rather than remembered as "no location"."""


class RateLimited(LookupFailed):
    """HTTP 429: the service says we've asked too often."""


@dataclass(slots=True)
class GeoInfo:
    lat: float
    lon: float
    city: str
    region: str
    country: str
    isp: str
    asn: Optional[int] = None    # the network's autonomous system number
    org: str = ""                # who that network belongs to

    def network(self) -> str:
        """"AS8075 Example Corp", or what's known of it."""
        name = self.org or self.isp
        if self.asn:
            return f"AS{self.asn} {name}".strip()
        return name

    def place(self) -> str:
        return ", ".join(x for x in (self.city, self.region, self.country) if x)


def _is_public(ip: str) -> bool:
    """Only globally routable unicast addresses go to ipwho.is.

    Excluding what is_private covers wasn't enough: carrier-grade NAT
    (100.64.0.0/10, which is also Tailscale's range) is neither private nor
    global, so a Starlink or mobile-carrier gateway or a tailnet peer went
    out. IPv6 site-local (fec0::/10) even counts as global to ipaddress, and
    an IPv4-mapped, 6to4, Teredo or zone-scoped address carries another
    address inside it."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version == 6 and (addr.scope_id or addr.is_site_local or addr.ipv4_mapped
                              or addr.sixtofour or addr.teredo):
        return False
    return addr.is_global and not (addr.is_multicast or addr.is_reserved or addr.is_loopback
                                   or addr.is_link_local or addr.is_unspecified)


class GeoResolver:
    def __init__(self, cache_path: Optional[os.PathLike] = None, clock=time.time) -> None:
        self.cache: dict[str, GeoInfo | None] = {}   # None == looked up, no data
        self.failures = 0                            # lookups that raised, not just missed
        self._q: queue.Queue[str] = queue.Queue()
        self._seen: set[str] = set()
        self._retry_at: dict[str, float] = {}        # ip -> monotonic time it may be retried
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._clock = clock
        self._cache_path = Path(cache_path) if cache_path is not None else None
        self._looked_up: dict[str, float] = {}       # ip -> when it was answered (epoch)
        self._dirty = False
        self._last_save = 0.0
        self._paused_until = 0.0                     # monotonic; after a 429
        self._day = ""
        self._today = 0
        self.paused = ""                             # why lookups are paused, if they are
        if self._cache_path is not None:
            self._load()

    # --- the cache on disk ----------------------------------------------------
    def _load(self) -> None:
        try:
            with open(self._cache_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            hosts = data.get("hosts") if isinstance(data, dict) else None
        except (OSError, ValueError):
            return
        if not isinstance(hosts, dict):
            return
        now = self._clock()
        for ip, entry in hosts.items():
            try:
                when = float(entry["t"])
                raw = entry.get("info")
                info = None if raw is None else GeoInfo(**{
                    k: raw[k] for k in GeoInfo.__dataclass_fields__ if k in raw})
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if not isinstance(ip, str) or now - when > _KEEP_S:
                continue                 # too old: asked again when next seen
            self.cache[ip] = info
            self._seen.add(ip)
            self._looked_up[ip] = when

    def save(self) -> bool:
        """Write the answers to disk (atomically). Never raises."""
        if self._cache_path is None:
            return False
        with self._lock:
            entries = sorted(self._looked_up.items(), key=lambda kv: kv[1])[-_MAX_KEPT:]
            hosts = {ip: {"t": t, "info": None if self.cache.get(ip) is None
                          else asdict(self.cache[ip])} for ip, t in entries}
            self._dirty = False
        tmp = self._cache_path.with_name(self._cache_path.name + ".tmp")
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "hosts": hosts}, fh)
            os.replace(tmp, self._cache_path)
            self._last_save = time.monotonic()
            return True
        except (OSError, TypeError, ValueError):
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._dirty:
            self.save()

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

    def _held(self) -> bool:
        """Whether lookups must wait: after a 429, or with today's allowance
        used up. Says why in ``paused``."""
        if time.monotonic() < self._paused_until:
            return True
        day = time.strftime("%Y-%m-%d", time.localtime(self._clock()))
        if day != self._day:
            self._day, self._today = day, 0
        if self._today >= _DAILY_CAP:
            self.paused = f"{_DAILY_CAP} lookups today; the rest wait until tomorrow"
            return True
        self.paused = ""
        return False

    def _worker(self) -> None:
        while not self._stop.is_set():
            if self._held():
                self._stop.wait(5.0)
                continue
            try:
                ip = self._q.get(timeout=0.5)
            except queue.Empty:
                if self._dirty and time.monotonic() - self._last_save > _SAVE_EVERY_S:
                    self.save()
                continue
            self._today += 1
            self._process(ip)
            if self._dirty and time.monotonic() - self._last_save > _SAVE_EVERY_S:
                self.save()
            self._stop.wait(_MIN_INTERVAL)

    def _process(self, ip: str) -> None:
        try:
            info = self._lookup(ip)
        except RateLimited:
            # Everything waits, and this address goes back in the queue.
            self._paused_until = time.monotonic() + _BACKOFF_429_S
            self.paused = "the lookup service asked us to slow down; trying again in an hour"
            with self._lock:
                self.failures += 1
            self._q.put(ip)
            return
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
            self._looked_up[ip] = self._clock()
            self._dirty = True

    def _lookup(self, ip: str) -> GeoInfo | None:
        try:
            # The canonical form ipaddress gives, never the string as it
            # came in: a loaded session's address could carry "/" or "%".
            req = urllib.request.Request(_URL.format(ip=urllib.parse.quote(
                                             str(ipaddress.ip_address(ip)), safe=":")),
                                         headers={"User-Agent": "PingerPlot"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                # Capped: this is one small JSON object from a third party, not
                # a download. urlopen's timeout applies to socket inactivity
                # rather than total transfer, so a slow-drip large body would
                # otherwise be read in full and then handed to json.loads.
                data = json.loads(resp.read(_MAX_BODY).decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise RateLimited(str(exc)) from exc
            raise LookupFailed(str(exc)) from exc
        except (OSError, ValueError) as exc:
            # OSError covers URLError, other HTTP errors and timeouts;
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
            if not isinstance(conn, dict):
                conn = {}
            try:
                asn = int(conn.get("asn")) if conn.get("asn") not in (None, "") else None
            except (TypeError, ValueError):
                asn = None
            return GeoInfo(
                lat=float(data["latitude"]),
                lon=float(data["longitude"]),
                city=data.get("city") or "",
                region=data.get("region") or "",
                country=data.get("country") or "",
                isp=conn.get("isp") or conn.get("org") or "",
                asn=asn,
                org=str(conn.get("org") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None
