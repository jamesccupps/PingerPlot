"""Small antialiased raster drawing, PNG and ICO encoding. Standard library only.

The theme draws its rounded buttons, fields, check boxes and switches at run
time, in the current accent colour and at the screen's scale, rather than
shipping fixed images; the tray icons are drawn the same way. Shapes are
signed distance functions in pixel units, so an edge's coverage is just
clamp(0.5 - distance).

Adapted from UniFi Stock Watcher, a sibling project with the same conventions.
"""
from __future__ import annotations

import base64
import math
import struct
import zlib


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(c))) for c in rgb[:3]))


def mix(a, b, t):
    """Blend colour a towards b by t (0..1); hex in, hex out."""
    ra, rb = hex_rgb(a), hex_rgb(b)
    return rgb_hex([ra[i] + (rb[i] - ra[i]) * t for i in range(3)])


def luminance(h):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in hex_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# --- Signed distance functions ------------------------------------------------

def sd_rrect(x0, y0, x1, y1, r):
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hx, hy = (x1 - x0) / 2 - r, (y1 - y0) / 2 - r

    def d(px, py):
        qx, qy = abs(px - cx) - hx, abs(py - cy) - hy
        return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r
    return d


def sd_circle(cx, cy, r):
    return lambda px, py: math.hypot(px - cx, py - cy) - r


def sd_polyline(points, width):
    """A stroked polyline with round caps and joins."""
    segs = list(zip(points, points[1:]))
    hw = width / 2

    def d(px, py):
        best = 1e9
        for (ax, ay), (bx, by) in segs:
            vx, vy, wx, wy = bx - ax, by - ay, px - ax, py - ay
            ll = vx * vx + vy * vy
            t = 0.0 if ll == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / ll))
            best = min(best, math.hypot(wx - vx * t, wy - vy * t))
        return best - hw
    return d


def sd_arc(cx, cy, r, width, a0, a1):
    """A stroked circular arc from angle a0 to a1 (radians, counter-clockwise
    from +x with y pointing down the screen), with round caps."""
    hw = width / 2
    ends = [(cx + r * math.cos(a), cy - r * math.sin(a)) for a in (a0, a1)]

    def d(px, py):
        ang = math.atan2(cy - py, px - cx)
        if a0 <= ang <= a1:
            return abs(math.hypot(px - cx, py - cy) - r) - hw
        return min(math.hypot(px - ex, py - ey) for ex, ey in ends) - hw
    return d


def sd_union(*fs):
    return lambda px, py: min(f(px, py) for f in fs)


def sd_subtract(a, b):
    return lambda px, py: max(a(px, py), -b(px, py))


# --- Raster ---------------------------------------------------------------------

class Raster:
    """Premultiplied RGBA float buffer."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.px = [0.0] * (w * h * 4)

    def draw(self, sdf, colour, alpha=1.0, bbox=None):
        """Composite shape ``sdf`` over the buffer in ``colour`` (hex).
        bbox=(x0, y0, x1, y1) limits the pixels visited."""
        x0, y0, x1, y1 = bbox or (0, 0, self.w, self.h)
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(self.w, int(math.ceil(x1))), min(self.h, int(math.ceil(y1)))
        rgb = hex_rgb(colour)
        px, w = self.px, self.w
        for y in range(y0, y1):
            cy = y + 0.5
            row = y * w
            for x in range(x0, x1):
                cov = 0.5 - sdf(x + 0.5, cy)
                if cov <= 0:
                    continue
                a = (1.0 if cov >= 1 else cov) * alpha
                i = (row + x) * 4
                inv = 1 - a
                px[i] = rgb[0] / 255 * a + px[i] * inv
                px[i + 1] = rgb[1] / 255 * a + px[i + 1] * inv
                px[i + 2] = rgb[2] / 255 * a + px[i + 2] * inv
                px[i + 3] = a + px[i + 3] * inv
        return self

    def rgba_bytes(self):
        out = bytearray(self.w * self.h * 4)
        p = self.px
        for i in range(0, len(p), 4):
            a = p[i + 3]
            if a <= 0:
                continue
            out[i] = min(255, round(p[i] / a * 255))
            out[i + 1] = min(255, round(p[i + 1] / a * 255))
            out[i + 2] = min(255, round(p[i + 2] / a * 255))
            out[i + 3] = min(255, round(a * 255))
        return bytes(out)

    def png(self):
        return png_bytes(self.w, self.h, self.rgba_bytes())

    def png_b64(self):
        return base64.b64encode(self.png()).decode("ascii")


def expand9(rgba, w, h, bx, by, nw, nh):
    """Grow a 9-slice image's middle to nw x nh by repeating its centre column
    and row (which, for the shapes drawn here, are uniform).

    ttk image elements *tile* the middle of an image rather than stretching
    it. With a few-pixel middle, a 1000-pixel panel was ~1000 alpha-blended
    blits per redraw. Drawing small and growing here keeps the drawing cheap
    and the blits few.
    """
    stride = w * 4
    rows = [rgba[y * stride:(y + 1) * stride] for y in range(h)]
    grow_x = w - 2 * bx > 0 and nw > w

    def widen(row):
        if not grow_x:
            return row
        return row[:bx * 4] + row[bx * 4:(bx + 1) * 4] * (nw - 2 * bx) + row[(w - bx) * 4:]
    out_w = nw if grow_x else w
    if h - 2 * by > 0 and nh > h:
        mid = widen(rows[by])
        out = ([widen(r) for r in rows[:by]] + [mid] * (nh - 2 * by)
               + [widen(r) for r in rows[h - by:]])
    else:
        out = [widen(r) for r in rows]
    return b"".join(out), out_w, len(out)


def png_bytes(w, h, rgba):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    stride = w * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


def ico_bytes(pngs):
    """An .ico holding PNG-compressed images, given [(size, png_bytes)]."""
    header = struct.pack("<HHH", 0, 1, len(pngs))
    entries, blobs = b"", b""
    offset = 6 + 16 * len(pngs)
    for size, data in pngs:
        s = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", s, s, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs
