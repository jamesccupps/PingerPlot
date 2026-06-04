"""Tests for the procedurally-generated app icon (pure-stdlib PNG writer)."""
import base64
import struct

from pingerplot import appicon


def test_png_signature_and_dimensions():
    data = appicon.png_bytes(64)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"          # PNG magic number
    w, h = struct.unpack(">II", data[16:24])         # IHDR width/height
    assert (w, h) == (64, 64)


def test_render_length_matches_rgba():
    assert len(appicon._render(32)) == 32 * 32 * 4   # 4 bytes/px, top-to-bottom


def test_base64_decodes_to_png():
    raw = base64.b64decode(appicon.png_base64(48))
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_icon_has_transparent_corners_and_opaque_body():
    alphas = appicon._render(64)[3::4]               # every alpha byte
    assert min(alphas) == 0                          # rounded corner is transparent
    assert max(alphas) == 255                        # tile body is opaque
