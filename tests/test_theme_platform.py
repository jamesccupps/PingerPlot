"""The look and the Windows integration under it (1.4).

gfx draws every rounded control at run time, theme turns the drawings into a
ttk style set, and winplatform reads Windows' theme and owns the Run key and
the single-instance mutex. The tests check the drawing primitives against
known geometry, that the palette serves every colour the existing drawing code
asks for, and that each Windows call is a harmless no-op elsewhere.
"""
import struct
import sys
import uuid

import pytest

from pingerplot import gfx, theme, winplatform


# --- gfx ---------------------------------------------------------------------

def test_colour_helpers():
    assert gfx.hex_rgb("#ff8000") == (255, 128, 0)
    assert gfx.rgb_hex((255, 128, 0)) == "#ff8000"
    assert gfx.mix("#000000", "#ffffff", 0.5) == "#808080"
    assert gfx.contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert gfx.contrast("#777777", "#777777") == pytest.approx(1.0)


def test_rounded_rect_distance():
    d = gfx.sd_rrect(0, 0, 20, 10, 3)
    assert d(10, 5) < 0            # inside
    assert d(25, 5) > 0            # outside
    assert d(0.2, 0.2) > 0         # a rounded-off corner
    assert abs(d(20, 5)) < 1e-9    # on the edge


def test_arc_is_stroked_only_between_its_angles():
    import math
    d = gfx.sd_arc(0, 0, 10, 2, 0, math.pi / 2)    # the upper-right quarter
    assert d(10, 0) <= 0 and d(0, -10) <= 0        # both ends (y down the screen)
    assert d(7.07, -7.07) <= 0                     # the middle
    assert d(-10, 0) > 0 and d(0, 10) > 0          # the other quarters
    assert d(3, -3) > 0                            # inside the arc is not the stroke


def test_raster_coverage_and_png():
    r = gfx.Raster(16, 16).draw(gfx.sd_rrect(0, 0, 16, 16, 6), "#ff0000")
    rgba = r.rgba_bytes()
    centre = (8 * 16 + 8) * 4
    assert rgba[centre:centre + 4] == bytes([255, 0, 0, 255])
    assert rgba[3] < 255                           # the corner pixel is antialiased away
    png = r.png()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (16, 16)


def test_ico_lists_every_image():
    pngs = [(s, gfx.Raster(s, s).png()) for s in (16, 32, 256)]
    ico = gfx.ico_bytes(pngs)
    assert struct.unpack("<HHH", ico[:6]) == (0, 1, 3)
    assert ico[6 + 32] == 0                        # 256 is written as 0


def test_expand9_keeps_corners_and_grows_the_middle():
    r = gfx.Raster(10, 10).draw(gfx.sd_rrect(0, 0, 10, 10, 3), "#336699")
    rgba, w, h = gfx.expand9(r.rgba_bytes(), 10, 10, 4, 4, 40, 30)
    assert (w, h) == (40, 30)
    assert rgba[:4] == r.rgba_bytes()[:4]         # top-left corner unchanged


# --- palette -------------------------------------------------------------------

@pytest.mark.parametrize("dark", [True, False])
def test_palette_text_on_accent_is_readable(dark, monkeypatch):
    monkeypatch.setattr(winplatform, "system_accent", lambda d: None)
    p = theme.build_palette(dark)
    assert gfx.contrast(p["accent"], p["on_accent"]) >= 3.0


@pytest.mark.parametrize("dark", [True, False])
def test_a_chosen_accent_is_shaded_until_it_reads(dark):
    p = theme.build_palette(dark, "#ffff00" if not dark else "#101010")
    if dark:
        assert gfx.contrast(p["accent"], p["bg"]) >= 4.0
    else:
        assert gfx.contrast(p["accent"], "#ffffff") >= 4.5


def test_the_palette_serves_every_colour_the_drawing_code_reads():
    """The graphs, rows and labels draw from gui.COLORS, which is filled from
    theme.legacy_colours: every key gui.py reads must exist there, or a
    canvas raises KeyError the first time it draws."""
    import re
    from pathlib import Path
    src = (Path(theme.__file__).with_name("gui.py")).read_text(encoding="utf-8")
    used = set(re.findall(r'(?:COLORS|\bp)\["([a-z_]+)"\]', src))
    assert used, "found no COLORS reads: the pattern is out of date"
    legacy = theme.legacy_colours(theme.build_palette(True))
    assert not used - set(legacy), used - set(legacy)


@pytest.mark.parametrize("setting,system,expected", [
    ("dark", False, True), ("light", True, False),
    ("system", True, True), ("system", False, False),
    ("system", None, True),        # nothing to follow (Linux, macOS): dark, as before 1.4
])
def test_resolve_dark(setting, system, expected, monkeypatch):
    monkeypatch.setattr(winplatform, "system_prefers_dark", lambda: system)
    assert theme.resolve_dark(setting) is expected


def test_theme_builds_and_switches_in_place(tk_root):
    t = theme.Theme(tk_root, {"theme": "dark"})
    img = t.image("btn")
    assert "Pp.btn" in t.style.element_names()
    assert t.style.layout("Accent.TButton")
    t.apply({"theme": "light"})
    assert t.image("btn") is img, "a theme change must redraw images in place"
    assert t.dark is False and t.c["bg"] == theme.LIGHT["bg"]


# --- winplatform -----------------------------------------------------------------

@pytest.mark.skipif(winplatform.IS_WINDOWS, reason="the non-Windows path")
def test_every_windows_call_is_a_no_op_elsewhere(monkeypatch):
    # Linux asks the desktop's portal instead (tests/test_linux_theme.py);
    # without gdbus there's nothing to ask.
    monkeypatch.setattr(winplatform.shutil, "which", lambda name: None)
    assert winplatform.system_prefers_dark() is None
    assert winplatform.system_accent(True) is None
    assert winplatform.autostart_value() is None
    assert winplatform.set_autostart(True) is False
    winplatform.enable_dpi_awareness()
    winplatform.set_app_id()
    winplatform.set_menu_theme(True)
    s = winplatform.SingleInstance()
    assert not s.already_running and not s.signal_existing()
    s.release()


def test_the_autostart_command_starts_minimized():
    cmd = winplatform.autostart_command()
    assert cmd.endswith("--minimized")
    assert "main.py" in cmd or getattr(sys, "frozen", False)


@pytest.mark.skipif(not winplatform.IS_WINDOWS, reason="a Windows named mutex")
def test_a_second_instance_sees_the_first():
    name = f"PingerPlotTest.{uuid.uuid4()}"
    first = winplatform.SingleInstance(name)
    try:
        assert not first.already_running
        second = winplatform.SingleInstance(name)
        try:
            assert second.already_running
        finally:
            second.release()
    finally:
        first.release()
    third = winplatform.SingleInstance(name)
    try:
        assert not third.already_running, "the mutex outlived its holders"
    finally:
        third.release()


@pytest.mark.skipif(not winplatform.IS_WINDOWS, reason="the Windows registry")
def test_reading_the_system_theme_does_not_raise():
    assert winplatform.system_prefers_dark() in (True, False, None)
    accent = winplatform.system_accent(True)
    assert accent is None or (accent.startswith("#") and len(accent) == 7)
