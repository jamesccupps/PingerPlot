"""Write the site's icon and the 1280x640 social preview (what link previews
show, and the repository's Settings > Social preview, uploaded by hand).

    python scripts/demo.py --shots site/images     (first: the screenshot it uses)
    python scripts/make_social_preview.py

Needs Pillow, for the text and the shadow; the app itself does not. The icon
is drawn by the app's own code (pingerplot/appicon.py), as in the exe.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pingerplot import appicon  # noqa: E402

IMAGES = ROOT / "site" / "images"
W, H = 1280, 640


def font(size, bold=False):
    for name in (("seguisb.ttf" if bold else "segoeui.ttf"), "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def icon(size):
    return Image.open(io.BytesIO(appicon.png_bytes(size))).convert("RGBA")


def main():
    IMAGES.mkdir(parents=True, exist_ok=True)
    icon(256).save(IMAGES / "icon.png", optimize=True)

    img = Image.new("RGB", (W, H))
    top, bottom = (16, 42, 38), (13, 18, 20)
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))

    shot = Image.open(IMAGES / "hops-dark.png").convert("RGB")
    shot.thumbnail((680, 580))
    x, y = W - shot.width - 40, (H - shot.height) // 2 + 20
    shadow = Image.new("RGBA", (shot.width + 60, shot.height + 60), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([30, 30, shot.width + 30, shot.height + 30], 12,
                                             fill=(0, 0, 0, 160))
    blurred = shadow.filter(ImageFilter.GaussianBlur(14))
    img.paste(blurred, (x - 30, y - 18), blurred)
    mask = Image.new("L", shot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, shot.width - 1, shot.height - 1], 10, fill=255)
    img.paste(shot, (x, y), mask)

    mark = icon(104)
    img.paste(mark, (64, 96), mark)
    d = ImageDraw.Draw(img)
    d.text((64, 226), "PingerPlot", font=font(66, True), fill="white")
    for i, line in enumerate(["See where on the path", "a connection goes wrong."]):
        d.text((66, 322 + i * 42), line, font=font(31), fill=(176, 214, 200))
    for i, line in enumerate(["Continuous traceroute: per-hop loss,", "latency, jitter and MOS over time."]):
        d.text((66, 430 + i * 30), line, font=font(22), fill=(140, 170, 160))
    d.text((66, 540), "Free and open source · Windows 10 and 11", font=font(22), fill=(140, 170, 160))
    out = IMAGES / "social-preview.png"
    img.save(out, optimize=True)
    print("wrote", IMAGES / "icon.png")
    print("wrote", out)


if __name__ == "__main__":
    main()
