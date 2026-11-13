"""PingerPlot against a pretend network, for trying the app and for the pictures.

    python scripts/demo.py                  # the app, monitoring four made-up paths
    python scripts/demo.py --shots DIR      # take the README/site pictures, then quit (Windows)

The real engine runs: traces, rounds, statistics, alerts, route-change events.
Only the network is pretend: ``icmp.ping`` answers from simulated paths, names
resolve to made-up hosts, and the Map tab gets made-up locations. Every address
is RFC 5737 documentation space or a private LAN address, so no picture can
show a real network. Settings go to a temporary folder; nothing of yours is
touched or sent anywhere.

--shots captures windows with PrintWindow, which works while they are covered.
"""
from __future__ import annotations

import atexit
import ctypes
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Settings into a throwaway folder before anything reads them.
_DATA = tempfile.mkdtemp(prefix="pingerplot-demo-")
atexit.register(shutil.rmtree, _DATA, True)
os.environ["APPDATA"] = _DATA
os.environ["XDG_CONFIG_HOME"] = _DATA

from pingerplot import geoip, icmp  # noqa: E402
from pingerplot.icmp import IP_REQ_TIMED_OUT, IP_SUCCESS, IP_TTL_EXPIRED_TRANSIT, PingResult  # noqa: E402

T0 = time.monotonic()


def _since() -> float:
    return time.monotonic() - T0


class Hop:
    def __init__(self, addr, name, ms, jitter=0.6, loss=0.0, where=None):
        self.addr, self.name, self.ms, self.jitter, self.loss = addr, name, ms, jitter, loss
        self.where = where          # (lat, lon, city, country) for the Map tab


class SimPath:
    """One simulated path. ``events(t, ttl)`` may change a hop's behaviour
    over time: that's how the demo shows a problem appearing, and a reroute."""

    def __init__(self, name, hops, events=None):
        self.name, self.hops, self.events = name, hops, events

    def hop(self, ttl):
        h = self.hops[min(ttl, len(self.hops)) - 1]
        return self.events(_since(), ttl, h) if self.events else h


HOME = Hop("192.168.1.1", "gateway.home.lan", 0.6, 0.2)
ISP = [Hop("198.51.100.1", "edge1.isp.example.net", 7.5, 1.2, where=(43.66, -70.26, "Portland", "US")),
       Hop("198.51.100.17", "agg2.isp.example.net", 9.8, 1.0, where=(42.36, -71.06, "Boston", "US"))]


def _voip_events(t, ttl, h):
    # From 20 s in, a congested link at hop 4 adds delay, jitter and loss that
    # carry through to the destination: the alert the demo is there to show.
    if ttl >= 4 and t > 20:
        ramp = min(1.0, (t - 20) / 15)
        return Hop(h.addr, h.name, h.ms + 55 * ramp, h.jitter + 18 * ramp,
                   h.loss + 0.22 * ramp, h.where)
    return h


def _dns_events(t, ttl, h):
    # A reroute at hop 5 after 30 s: a different router, a few ms further.
    if ttl == 5 and t > 30:
        return Hop("203.0.113.33", "core3.transit.example.net", h.ms + 4, h.jitter, h.loss,
                   (40.71, -74.01, "New York", "US"))
    return h


PATHS = {
    "203.0.113.53": SimPath("dns.example.net", [
        HOME, *ISP,
        Hop("198.51.100.65", "ix1.example.net", 14.2, 1.1, loss=0.3,     # rate-limits ICMP
            where=(40.71, -74.01, "New York", "US")),
        Hop("203.0.113.17", "core1.transit.example.net", 16.0, 1.0, where=(40.71, -74.01, "New York", "US")),
        Hop("203.0.113.53", "dns.example.net", 16.9, 0.8, where=(39.04, -77.49, "Ashburn", "US")),
    ], _dns_events),
    "203.0.113.80": SimPath("voip.example.com", [
        HOME, *ISP,
        Hop("198.51.100.97", "peer4.example.net", 21.0, 1.5, where=(41.88, -87.63, "Chicago", "US")),
        Hop("203.0.113.70", "edge.voip.example.com", 33.0, 1.4, where=(39.74, -104.99, "Denver", "US")),
        Hop("203.0.113.80", "voip.example.com", 34.2, 1.2, where=(39.74, -104.99, "Denver", "US")),
    ], _voip_events),
    "198.51.100.200": SimPath("vpn.example.org", [
        HOME, *ISP,
        Hop("198.51.100.129", "core5.isp.example.net", 11.0, 0.9, where=(42.36, -71.06, "Boston", "US")),
        Hop("198.51.100.200", "vpn.example.org", 12.4, 0.7, where=(42.36, -71.06, "Boston", "US")),
    ]),
    "192.168.1.1": SimPath("192.168.1.1", [HOME]),
}
NAMES = {p.name: ip for ip, p in PATHS.items()}
REVERSE = {h.addr: h.name for p in PATHS.values() for h in p.hops}
WHERE = {h.addr: h.where for p in PATHS.values() for h in p.hops if h.where}


def pretend_ping(dest_ip, ttl=128, timeout_ms=1000, payload=b"", tos=0, source_ip=None):
    path = PATHS.get(dest_ip)
    if path is None:
        time.sleep(timeout_ms / 1000)
        return PingResult(IP_REQ_TIMED_OUT, None, None, False)
    h = path.hop(ttl)
    reached = ttl >= len(path.hops)
    if random.random() < h.loss:
        time.sleep(timeout_ms / 1000)
        return PingResult(IP_REQ_TIMED_OUT, None, None, False)
    rtt = max(0.2, random.gauss(h.ms, h.jitter))
    time.sleep(rtt / 1000)
    return PingResult(IP_SUCCESS if reached else IP_TTL_EXPIRED_TRANSIT, float(round(rtt)),
                      h.addr, reached)


def install():
    icmp.ping = pretend_ping
    icmp.is_available = lambda: True
    real_resolve = socket.gethostbyname
    socket.gethostbyname = lambda n: NAMES.get(n) or (n if n in PATHS else real_resolve(n))

    def reverse(addr):
        if addr in REVERSE:
            return REVERSE[addr], [], [addr]
        raise socket.herror(1, "no PTR record")
    socket.gethostbyaddr = reverse

    # Documentation addresses aren't public, so the Map would skip them all.
    geoip._is_public = lambda ip: ip in WHERE

    def lookup(self, ip):
        lat, lon, city, country = WHERE[ip]
        name = REVERSE.get(ip, "")
        # Networks numbered from the AS range set aside for documentation
        # (RFC 5398), so no picture names a real one.
        if name.endswith("example.com"):
            asn, org = 64511, "Example Voice"
        elif name.startswith("peer"):
            asn, org = 64500, "Example Transit"
        else:
            asn, org = 64496, "Example ISP"
        return geoip.GeoInfo(lat=lat, lon=lon, city=city, region="", country=country, isp=org,
                             asn=asn, org=org)
    geoip.GeoResolver._lookup = lookup
    geoip._MIN_INTERVAL = 0.05


DEMO_OPTIONS = dict(interval=1.0, timeout_ms=400, alert_window=10, alert_loss_pct=10,
                    alert_latency_ms=60, alert_sound=False)


def start_demo(app):
    for name in ("dns.example.net", "voip.example.com", "vpn.example.org", "192.168.1.1"):
        opts = app._default_options()
        opts.update(DEMO_OPTIONS)
        if name == "192.168.1.1":
            opts["final_hop_only"] = True
        app._start(name, opts)
    app._activate("voip.example.com")


# --- pictures (Windows) --------------------------------------------------------

def capture(win, path):
    """The window (with its title bar) to PNG, by PrintWindow. Everything is
    invalidated first and the picture taken a moment later: on a locked or
    covered desktop Windows sends no WM_PAINT for what changed, and Tk draws
    the Expose events it turns them into from the event loop.

    Elsewhere (Linux under X11, e.g. CI's Xvfb) ImageMagick's ``import``
    grabs the window's frame from the X server."""
    if sys.platform != "win32":
        win.after(700, lambda: _grab_x11(win, path))
        return
    hwnd = int(win.wm_frame(), 16)
    ctypes.windll.user32.RedrawWindow(hwnd, None, None, 0x1 | 0x4 | 0x80 | 0x400)
    win.after(700, lambda: _grab(win, path))


def _grab_x11(win, path):
    win.update_idletasks()
    subprocess.run(["import", "-window", win.wm_frame(), str(path)], check=True, timeout=30)
    print("wrote", path)


def _grab(win, path):
    from ctypes import wintypes
    from pingerplot import gfx
    win.update_idletasks()
    hwnd = int(win.wm_frame(), 16)
    u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32
    rect, crect = wintypes.RECT(), wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(rect))
    u32.GetClientRect(hwnd, ctypes.byref(crect))
    pt = wintypes.POINT(0, 0)
    u32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = u32.GetWindowDC(hwnd)
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mdc, bmp)
    u32.PrintWindow(hwnd, mdc, 2)                  # PW_RENDERFULLCONTENT

    class BMI(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("rest", wintypes.DWORD * 5)]
    bmi = BMI(biSize=ctypes.sizeof(BMI), biWidth=w, biHeight=-h, biPlanes=1, biBitCount=32)
    buf = ctypes.create_string_buffer(w * h * 4)
    g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mdc)
    u32.ReleaseDC(hwnd, hdc)
    # Crop the invisible resize border, keep the title bar; BGRA to opaque RGBA.
    side, top = pt.x - rect.left, 1
    cw, ch = w - 2 * side, (pt.y - rect.top) + crect.bottom - top
    src = buf.raw
    rows = []
    for y in range(top, top + ch):
        row = bytearray(src[(y * w + side) * 4:(y * w + side + cw) * 4])
        row[0::4], row[2::4] = row[2::4], row[0::4]
        row[3::4] = b"\xff" * cw
        rows.append(bytes(row))
    Path(path).write_bytes(gfx.png_bytes(cw, ch, b"".join(rows)))
    print("wrote", path)


SHOTS = [
    # (file, theme, view): a target view (hops/timeline/map/events) or settings
    ("hops-dark.png", "dark", "hops"),
    ("hops-light.png", "light", "hops"),
    ("timeline-dark.png", "dark", "timeline"),
    ("map-dark.png", "dark", "map"),
    ("events-light.png", "light", "events"),
    ("settings-dark.png", "dark", "settings"),
]


def take(out, name, theme_name, tab, warmup):
    """One picture, in a process of its own, so each is built in its theme
    before the window first appears."""
    import tkinter as tk
    from pingerplot import gui, settings, winplatform
    settings.save({"theme": theme_name, "resume_on_launch": False})
    install()
    winplatform.enable_dpi_awareness()
    root = tk.Tk()
    app = gui.App(root)
    root.geometry(f"{app.s(1180)}x{app.s(760)}+40+40")
    start_demo(app)

    def shoot():
        if tab == "settings":
            app.nav.select("settings", notify=True)
        else:
            app.detail_nav.select(tab, notify=True)
        app._refresh_once()
        root.after(1500, lambda: capture(root, out / name))
        root.after(3500, app._on_close)
    root.after(int(warmup * 1000), shoot)
    root.mainloop()


def main():
    if "--take" in sys.argv:
        i = sys.argv.index("--take")
        out, name, theme_name, tab, warmup = sys.argv[i + 1:i + 6]
        take(Path(out), name, theme_name, tab, float(warmup))
        return
    if "--shots" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--shots") + 1])
        out.mkdir(parents=True, exist_ok=True)
        warmup = "45"
        for name, theme_name, tab in SHOTS:
            subprocess.run([sys.executable, __file__, "--take", str(out), name, theme_name, tab, warmup],
                           check=True, timeout=180)
        return
    import tkinter as tk
    from pingerplot import gui, winplatform
    install()
    winplatform.enable_dpi_awareness()
    root = tk.Tk()
    app = gui.App(root)
    root.after(200, lambda: start_demo(app))
    root.mainloop()


if __name__ == "__main__":
    main()
