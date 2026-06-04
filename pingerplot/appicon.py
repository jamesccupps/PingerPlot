"""Procedurally-rendered application icon (pure standard library).

Replaces Tk's default feather in the title bar / taskbar with a themed mark:
"signal ripples" — concentric arcs radiating from a node, the universal cue for
ping / reachability — in the app's accent blue on a dark rounded tile.

It is drawn from math and encoded to a PNG in memory (a tiny stdlib PNG writer),
so there is no third-party dependency (no Pillow) and no binary asset committed
to the repo. The result is returned as base64 PNG text for
``tkinter.PhotoImage(data=...)``. Edges are anti-aliased by supersampling and
correct (premultiplied) alpha downsampling, so the rounded corners and arcs stay
smooth at any title-bar size.
"""
from __future__ import annotations

import base64
import math
import struct
import zlib

# palette (matches the GUI's dark theme accent)
_TILE = (26, 34, 46)     # dark navy tile
_BORDER = (60, 74, 92)   # subtle lighter rim
_ARC = (74, 163, 223)    # accent blue (COLORS["line"])
_DOT = (152, 212, 255)   # bright node


def _in_round_rect(x: float, y: float, lo: float, hi: float, r: float) -> bool:
    if x < lo or x > hi or y < lo or y > hi:
        return False
    cx = lo + r if x < lo + r else (hi - r if x > hi - r else None)
    cy = lo + r if y < lo + r else (hi - r if y > hi - r else None)
    if cx is not None and cy is not None:
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    return True


def _render(n: int = 64, ss: int = 3) -> bytes:
    """Return ``n*n`` RGBA bytes (top-to-bottom) for the icon, supersampled by
    ``ss`` and downsampled with premultiplied alpha for clean anti-aliasing."""
    b = n * ss
    x0, y0 = 0.30 * b, 0.74 * b           # ripple origin (lower-left)
    radii = ((0.21 * b, 255), (0.35 * b, 225), (0.49 * b, 185))  # (radius, alpha)
    half_t = 0.038 * b                    # arc half-thickness
    dot_r = 0.085 * b
    corner = 0.16 * b
    bw = 0.022 * b                        # rim width
    lo, hi = 0.05 * b, b - 0.05 * b
    qx, qy = -0.06 * b, 0.06 * b          # keep arcs to the upper-right quadrant

    pr = [0.0] * (n * n); pg = [0.0] * (n * n)
    pb = [0.0] * (n * n); pa = [0.0] * (n * n)

    for by in range(b):
        row = (by // ss) * n
        py = by + 0.5
        for bx in range(b):
            px = bx + 0.5
            if not _in_round_rect(px, py, lo, hi, corner):
                continue
            if not _in_round_rect(px, py, lo + bw, hi - bw, corner - bw):
                r, g, bl, a = _BORDER[0], _BORDER[1], _BORDER[2], 255
            else:
                r, g, bl, a = _TILE[0], _TILE[1], _TILE[2], 255
                dx, dy = px - x0, py - y0
                d = math.hypot(dx, dy)
                if dx >= qx and dy <= qy:
                    for rr, aa in radii:
                        if abs(d - rr) <= half_t:
                            r, g, bl, a = _ARC[0], _ARC[1], _ARC[2], aa
                            break
                if d <= dot_r:
                    r, g, bl, a = _DOT[0], _DOT[1], _DOT[2], 255
            idx = row + (bx // ss)
            f = a / 255.0
            pr[idx] += r * f; pg[idx] += g * f; pb[idx] += bl * f; pa[idx] += a

    n2 = ss * ss
    out = bytearray(n * n * 4)
    for i in range(n * n):
        a_sum = pa[i]
        if a_sum <= 0:
            continue  # transparent (already zeroed)
        j = i * 4
        out[j] = min(255, int(pr[i] * 255 / a_sum + 0.5))
        out[j + 1] = min(255, int(pg[i] * 255 / a_sum + 0.5))
        out[j + 2] = min(255, int(pb[i] * 255 / a_sum + 0.5))
        out[j + 3] = min(255, int(a_sum / n2 + 0.5))
    return bytes(out)


def _png(rgba: bytes, w: int, h: int) -> bytes:
    """Encode raw RGBA (8-bit, top-to-bottom) as a PNG. Stdlib only."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    stride = w * 4
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter type 0 (None) per scanline
        raw.extend(rgba[y * stride:(y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit, colour type 6 (RGBA)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def png_bytes(size: int = 64) -> bytes:
    """The icon as PNG bytes."""
    return _png(_render(size), size, size)


def png_base64(size: int = 64) -> str:
    """The icon as base64 PNG text, for ``tkinter.PhotoImage(data=...)``."""
    return base64.b64encode(png_bytes(size)).decode("ascii")


if __name__ == "__main__":  # render a preview file: python -m pingerplot.appicon out.png
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "appicon_preview.png"
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 256
    with open(path, "wb") as fh:
        fh.write(png_bytes(size))
    print(f"wrote {path} ({size}x{size})")
