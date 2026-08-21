"""Write a multi-size .ico from the procedurally-drawn app icon.

The app draws its own icon from math (``appicon.py``) rather than shipping a
binary asset, so there is no .ico in the repo for PyInstaller to embed. This
generates one at build time from the same code that draws the title-bar icon,
which keeps the two from drifting apart.

Why bother: an executable wearing the generic Python icon looks like something
assembled in a hurry, and this is an unsigned network tool that people are
already going to be asked to trust past a SmartScreen warning. Looking like
itself is worth twenty lines.

ICO structure (an ICONDIR, then one ICONDIRENTRY per image, then the images).
Vista and later accept PNG-compressed entries, which is what appicon already
produces, so no BMP conversion is needed.

    python packaging/make_icon.py build/pingerplot.ico
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pingerplot import appicon  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_ico(sizes=SIZES) -> bytes:
    images = [appicon.png_bytes(s) for s in sizes]

    # ICONDIR: reserved, type (1 = icon), image count
    out = [struct.pack("<HHH", 0, 1, len(images))]

    # Directory entries come first, so every offset must account for the whole
    # table being written before any image data.
    offset = 6 + 16 * len(images)
    for size, png in zip(sizes, images):
        # 0 means 256 in a single byte field; every other size fits.
        dim = 0 if size >= 256 else size
        out.append(struct.pack(
            "<BBBBHHII",
            dim, dim,      # width, height
            0,             # palette size (0 = no palette)
            0,             # reserved
            1,             # colour planes
            32,            # bits per pixel
            len(png),      # bytes of image data
            offset,        # where that data starts
        ))
        offset += len(png)

    out.extend(images)
    return b"".join(out)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dest = Path(argv[0]) if argv else Path("pingerplot.ico")
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = build_ico()
    dest.write_bytes(data)
    print(f"wrote {dest} ({len(data):,} bytes, {len(SIZES)} sizes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
