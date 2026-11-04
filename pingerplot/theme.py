"""The look: Windows 11-style light and dark themes for ttk, in any accent.

ttk's own themes look dated on Windows 11, and image themes bake one accent
colour into their PNGs. Here every rounded element is drawn at start-up by
gfx.py - in the current palette, at the screen's scale - and a theme change
redraws the same PhotoImages in place, which every element picks up without
being recreated.

ttk fills an image element's transparent pixels with the style's background,
so each style that sits on a card has a "Card." twin whose background is the
card's colour; the rounded corners then blend into whatever is behind them.

Adapted from UniFi Stock Watcher, a sibling project with the same conventions,
plus PingerPlot's own colour roles (graphs, hop rows, events, MOS): see
``legacy_colours``.
"""
from __future__ import annotations

import base64
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from . import gfx, winplatform
from .gfx import Raster, contrast, mix, sd_circle, sd_polyline, sd_rrect

LIGHT = {
    "bg": "#f3f3f3", "surface": "#ffffff", "surface2": "#f7f7f7",
    "border": "#e3e3e3", "divider": "#e5e5e5",
    "text": "#1b1b1b", "text2": "#5c5c5c", "text3": "#9b9b9b",
    "control": "#fdfdfd", "control_hover": "#f6f6f6", "control_pressed": "#efefef",
    "control_border": "#d6d6d6", "control_border_bottom": "#c2c2c2",
    "strong_border": "#8a8a8a",
    "field": "#ffffff", "field_border": "#d6d6d6", "field_bottom": "#8a8a8a",
    "subtle_hover": "#ebebeb", "subtle_pressed": "#e1e1e1",
    "green": "#0f7b0f", "green_bg": "#dff6dd",
    "red": "#c42b1c", "red_bg": "#fde7e9",
    "amber": "#8a5300", "amber_bg": "#fff4ce",
    "neutral_bg": "#ececec",
    "scroll": "#a0a0a0", "scroll_hover": "#808080",
    "loss": "#d13438",
    "default_accent": "#0067c0",
}

DARK = {
    "bg": "#202020", "surface": "#2b2b2b", "surface2": "#323232",
    "border": "#363636", "divider": "#3a3a3a",
    "text": "#ffffff", "text2": "#bdbdbd", "text3": "#7c7c7c",
    "control": "#2f2f2f", "control_hover": "#353535", "control_pressed": "#2a2a2a",
    "control_border": "#3d3d3d", "control_border_bottom": "#353535",
    "strong_border": "#9c9c9c",
    "field": "#2d2d2d", "field_border": "#3d3d3d", "field_bottom": "#9c9c9c",
    "subtle_hover": "#383838", "subtle_pressed": "#303030",
    "green": "#6ccb5f", "green_bg": "#203a1f",
    "red": "#ff99a4", "red_bg": "#442726",
    "amber": "#f2c94c", "amber_bg": "#3d3218",
    "neutral_bg": "#3a3a3a",
    "scroll": "#8a8a8a", "scroll_hover": "#a8a8a8",
    "loss": "#ff5a67",
    "default_accent": "#4cc2ff",
}

THEMES = ("system", "dark", "light")

# Segoe Fluent Icons (Windows 11) and Segoe MDL2 Assets (Windows 10) share
# these code points; elsewhere the plain symbol stands in.
ICONS = {
    "more":   ("", "⋯"),
    "add":    ("", "+"),
    "pause":  ("", "‖"),
    "play":   ("", "▶"),
    "stop":   ("", "■"),
    "delete": ("", "✕"),
    "edit":   ("", "✎"),
    "close":  ("\ue711", "✕"),
}


def resolve_dark(theme_setting: str) -> bool:
    """dark/light as chosen; "system" follows Windows, and is dark where
    there is no system setting to follow (PingerPlot's look before 1.4)."""
    if theme_setting == "dark":
        return True
    if theme_setting == "light":
        return False
    pref = winplatform.system_prefers_dark()
    return True if pref is None else pref


def _fit_accent(colour, dark, bg):
    """Shade a chosen colour until it reads against the theme: dark enough
    for white text in light mode, light enough against the dark background."""
    c = colour
    for _ in range(20):
        if dark and contrast(c, bg) >= 4.0:
            break
        if not dark and contrast(c, "#ffffff") >= 4.5:
            break
        c = mix(c, "#ffffff" if dark else "#000000", 0.12)
    return c


def build_palette(dark: bool, accent_setting: str = "") -> dict:
    p = dict(DARK if dark else LIGHT)
    if accent_setting:
        accent = _fit_accent(accent_setting, dark, p["bg"])
    else:
        accent = winplatform.system_accent(dark) or p["default_accent"]
    p["accent"] = accent
    p["on_accent"] = "#000000" if contrast(accent, "#000000") > contrast(accent, "#ffffff") else "#ffffff"
    toward = "#000000" if dark else "#ffffff"
    p["accent_hover"] = mix(accent, toward, 0.10)
    p["accent_pressed"] = mix(accent, toward, 0.22)
    p["accent_disabled"] = mix(accent, p["bg"], 0.55)
    p["accent_subtle"] = mix(accent, p["surface"], 0.72 if dark else 0.86)
    p["accent_text"] = accent
    p["dark"] = dark
    return p


def legacy_colours(p: dict) -> dict:
    """The palette under the keys PingerPlot's drawing code reads (graphs,
    hop and event rows, the MOS label, the alert banner)."""
    return {
        "ok": p["surface"], "warn": p["amber_bg"], "bad": p["red_bg"], "dest": p["accent_subtle"],
        "graph_bg": p["surface"], "graph_border": p["border"], "grid": p["divider"],
        "line": p["accent"], "fill": mix(p["accent"], p["surface"], 0.78), "loss": p["loss"],
        "axis_text": p["text3"], "title": p["text"],
        "strip_sep": p["divider"], "strip_border": p["border"],
        "banner_bg": p["red_bg"], "banner_fg": p["red"],
        "fg_bad": p["red"], "fg_warn": p["amber"], "fg_dest": p["accent_text"], "fg_ok": p["text"],
        "ev_alert": p["red_bg"], "ev_clear": p["green_bg"], "ev_route": p["accent_subtle"],
        "ev_warn": p["amber_bg"],
        "mos_good": p["green"], "mos_mid": p["amber"], "mos_bad": p["red"],
        "win_bg": p["bg"], "panel": p["surface"], "field": p["field"], "fg": p["text"],
        "sel_bg": p["accent_subtle"], "sel_fg": p["text"], "active": p["subtle_hover"],
        "border": p["border"], "hint": p["text3"], "status_bg": p["bg"],
    }


class Theme:
    """Owns the palette, fonts, scale, generated images and ttk styles."""

    def __init__(self, root, settings: dict) -> None:
        self.root = root
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self._images = {}
        self._elements_made = False
        families = set(tkfont.families(root))
        self.family = next((f for f in ("Segoe UI Variable Text", "Segoe UI") if f in families),
                           tkfont.nametofont("TkDefaultFont").actual("family"))
        self.semibold = next((f for f in ("Segoe UI Semibold",) if f in families), None)
        self.mono = next((f for f in ("Cascadia Mono", "Consolas") if f in families),
                         tkfont.nametofont("TkFixedFont").actual("family"))
        self.icon_family = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets")
                                 if f in families), None)
        self.apply(settings)

    # Helpers ------------------------------------------------------------------

    def S(self, px: float) -> int:
        """Logical pixels (at 100 %) to screen pixels."""
        return int(round(px * self.scale))

    def font(self, delta: int = 0, weight: str = "normal"):
        size = max(8, self.size + delta)
        if weight == "semibold":
            return (self.semibold, size) if self.semibold else (self.family, size, "bold")
        return (self.family, size, weight)

    def mono_font(self, delta: int = 0):
        return (self.mono, max(8, self.size + delta))

    def icon_font(self, delta: int = 0):
        return (self.icon_family, max(8, self.size + delta)) if self.icon_family else self.font(delta)

    def icon(self, name: str) -> str:
        glyph, fallback = ICONS[name]
        return glyph if self.icon_family else fallback

    # Theme --------------------------------------------------------------------

    def apply(self, settings: dict) -> None:
        self.size = int(settings.get("font_size", 10) or 10)
        self.setting = settings.get("theme", "system")
        self.dark = resolve_dark(self.setting)
        self.c = build_palette(self.dark, settings.get("accent", "") or "")
        self._render_images()
        self._configure_styles()
        winplatform.set_menu_theme(self.dark)

    def style_window(self, win) -> None:
        """Title bar and default colours for a Toplevel (or the root)."""
        win.configure(bg=self.c["bg"])
        winplatform.style_title_bar(win, self.dark, caption=self.c["bg"], text=self.c["text"])

    # Images -------------------------------------------------------------------

    def _put(self, name, raster, grow=None):
        """Store ``raster`` as the PhotoImage ``name``, redrawing it in place if
        it exists. grow=(border_x, border_y, middle) widens a 9-slice image's
        middle to ``middle`` pixels (see gfx.expand9)."""
        rgba, w, h = raster.rgba_bytes(), raster.w, raster.h
        if grow:
            bx, by, m = grow
            rgba, w, h = gfx.expand9(rgba, w, h, bx, by, 2 * bx + m, 2 * by + m)
        data = base64.b64encode(gfx.png_bytes(w, h, rgba)).decode("ascii")
        img = self._images.get(name)
        if img is None:
            self._images[name] = tk.PhotoImage(master=self.root, data=data, format="png")
        else:
            img.configure(data=data, format="png")

    def image(self, name):
        return self._images[name]

    def _rrect_img(self, w, h, r, fill, border=None, bottom=None, bw=None, alpha=1.0):
        """A rounded rectangle, optionally with a 1px border and a different
        bottom edge (Windows 11 controls are a shade darker along the bottom)."""
        R = Raster(w, h)
        bw = bw or max(1.0, round(self.scale))
        if border:
            R.draw(sd_rrect(0, 0, w, h, r), border, alpha)
            if bottom:
                R.draw(sd_rrect(0, 0, w, h, r), bottom, alpha, bbox=(0, h - bw - 1, w, h))
                R.draw(sd_rrect(0, 0, w, h - bw, r), border, alpha, bbox=(0, h - bw - r - 2, w, h))
            R.draw(sd_rrect(bw, bw, w - bw, h - bw, max(0.5, r - bw)), fill, alpha)
        elif fill:
            R.draw(sd_rrect(0, 0, w, h, r), fill, alpha)
        return R

    def _render_images(self):
        c, S = self.c, self.S
        r = S(4)
        self._slice = {"ctl": r + 1, "card": S(8) + 1, "pill": S(10)}
        n = 2 * self._slice["ctl"] + 2
        g_ctl = (self._slice["ctl"], self._slice["ctl"], S(96))
        g_card = (self._slice["card"], self._slice["card"], S(240))
        g_pill = (self._slice["pill"], self._slice["pill"], S(64))

        def ctl(fill, b=c["control_border"], bb=c["control_border_bottom"]):
            return self._rrect_img(n, n, r, fill, b, bb)
        self._put("btn", ctl(c["control"]), g_ctl)
        self._put("btn_hover", ctl(c["control_hover"]), g_ctl)
        self._put("btn_pressed", ctl(c["control_pressed"], c["control_border"], c["control_border"]), g_ctl)
        self._put("btn_disabled", ctl(c["control"], c["divider"], c["divider"]), g_ctl)
        for name, fill in (("acc", c["accent"]), ("acc_hover", c["accent_hover"]),
                           ("acc_pressed", c["accent_pressed"]), ("acc_disabled", c["accent_disabled"])):
            self._put(name, self._rrect_img(n, n, r, fill), g_ctl)
        self._put("subtle", Raster(n, n), g_ctl)
        self._put("subtle_hover", self._rrect_img(n, n, r, c["subtle_hover"]), g_ctl)
        self._put("subtle_pressed", self._rrect_img(n, n, r, c["subtle_pressed"]), g_ctl)

        # Fields: the bottom edge is the strong line, accent and 2px on focus.
        def field(bottom, thick=1.0, fill=c["field"]):
            R = self._rrect_img(n, n, r, fill, c["field_border"])
            t = max(1.0, round(self.scale * thick))
            R.draw(sd_rrect(0, 0, n, n, r), bottom, bbox=(0, n - t, n, n))
            return R
        self._put("field", field(c["field_bottom"]), g_ctl)
        self._put("field_hover", field(c["field_bottom"], fill=c["control_hover"]), g_ctl)
        self._put("field_focus", field(c["accent"], 2.0), g_ctl)
        self._put("field_disabled", self._rrect_img(n, n, r, c["control"], c["divider"]), g_ctl)

        # Cards.
        cr = S(8)
        cn = 2 * self._slice["card"] + 2
        self._put("card", self._rrect_img(cn, cn, cr, c["surface"], c["border"]), g_card)
        self._put("card_sel", self._rrect_img(cn, cn, cr, c["surface"], c["accent"],
                                              bw=max(1.0, round(self.scale * 1.5))), g_card)
        self._put("card_alert", self._rrect_img(cn, cn, cr, c["surface"], c["red"],
                                                bw=max(1.0, round(self.scale * 1.5))), g_card)
        self._put("banner", self._rrect_img(cn, cn, cr, c["red_bg"], mix(c["red"], c["surface"], 0.5)),
                  g_card)

        # Status pills.
        pr = S(10)
        pn = 2 * self._slice["pill"] + 2
        for name, fill in (("green", c["green_bg"]), ("red", c["red_bg"]),
                           ("amber", c["amber_bg"]), ("neutral", c["neutral_bg"])):
            self._put(f"pill_{name}", self._rrect_img(pn, pn, pr, fill), g_pill)

        # Check boxes.
        cb, cbr = S(18), S(4)
        tick = [(cb * 0.27, cb * 0.52), (cb * 0.44, cb * 0.68), (cb * 0.74, cb * 0.34)]
        stroke = max(1.4, self.scale * 1.35)

        def checkbox(on, hover=False, disabled=False):
            if disabled:
                R = self._rrect_img(cb, cb, cbr, c["neutral_bg"], c["divider"])
                if on:
                    R.draw(sd_polyline(tick, stroke), c["text3"])
                return R
            if on:
                R = self._rrect_img(cb, cb, cbr, c["accent_hover"] if hover else c["accent"])
                R.draw(sd_polyline(tick, stroke), c["on_accent"])
                return R
            return self._rrect_img(cb, cb, cbr, c["subtle_hover"] if hover else c["field"],
                                   c["strong_border"])
        self._put("check_off", checkbox(False))
        self._put("check_off_hover", checkbox(False, True))
        self._put("check_on", checkbox(True))
        self._put("check_on_hover", checkbox(True, True))
        self._put("check_disabled", checkbox(False, disabled=True))
        self._put("check_on_disabled", checkbox(True, disabled=True))

        # Switches.
        sw, sh = S(40), S(20)

        def switch(on, hover=False):
            R = Raster(sw, sh)
            rad = sh / 2
            knob = rad - S(4) + (S(1) if hover else 0)
            if on:
                R.draw(sd_rrect(0, 0, sw, sh, rad), c["accent_hover"] if hover else c["accent"])
                R.draw(sd_circle(sw - rad, rad, knob), c["on_accent"])
            else:
                bw = max(1.0, round(self.scale))
                R.draw(sd_rrect(0, 0, sw, sh, rad), c["strong_border"])
                R.draw(sd_rrect(bw, bw, sw - bw, sh - bw, rad - bw),
                       c["subtle_hover"] if hover else c["field"])
                R.draw(sd_circle(rad, rad, knob), c["text2"])
            return R
        self._put("switch_off", switch(False))
        self._put("switch_off_hover", switch(False, True))
        self._put("switch_on", switch(True))
        self._put("switch_on_hover", switch(True, True))

        # Combobox and spinbox chevrons.
        aw = S(24)

        def chevron(col):
            R = Raster(aw, aw)
            m = aw / 2
            R.draw(sd_polyline([(m - S(4), m - S(2)), (m, m + S(2)), (m + S(4), m - S(2))],
                               max(1.2, self.scale)), col)
            return R
        self._put("chevron", chevron(c["text2"]))
        self._put("chevron_disabled", chevron(c["text3"]))

        # Scrollbar thumb: a thin pill.
        tw, th = S(12), S(28)

        def thumb(col, wide):
            return Raster(tw, th).draw(
                sd_rrect(tw / 2 - wide / 2, S(2), tw / 2 + wide / 2, th - S(2), wide / 2), col)
        self._put("thumb", thumb(c["scroll"], S(4)), (tw // 2, S(8), S(160)))
        self._put("thumb_hover", thumb(c["scroll_hover"], S(6)), (tw // 2, S(8), S(160)))
        self._put("hthumb", Raster(th, tw).draw(
            sd_rrect(S(2), tw / 2 - S(2), th - S(2), tw / 2 + S(2), S(2)), c["scroll"]))

    # Styles -------------------------------------------------------------------

    def _make_elements(self):
        st, I, S = self.style, self.image, self.S
        # Sliced images are drawn large (see _put's grow), so each says how
        # small it may be: an image element otherwise asks for its image's
        # whole size, and every button would be 150 px tall.
        b = self._slice["ctl"]
        small = dict(border=b, sticky="nsew", width=2 * b, height=2 * b)
        st.element_create("Pp.btn", "image", I("btn"),
                          ("disabled", I("btn_disabled")), ("pressed", I("btn_pressed")),
                          ("active", I("btn_hover")), **small)
        st.element_create("Pp.acc", "image", I("acc"),
                          ("disabled", I("acc_disabled")), ("pressed", I("acc_pressed")),
                          ("active", I("acc_hover")), **small)
        st.element_create("Pp.subtle", "image", I("subtle"),
                          ("pressed", I("subtle_pressed")), ("active", I("subtle_hover")), **small)
        st.element_create("Pp.field", "image", I("field"),
                          ("disabled", I("field_disabled")), ("focus", I("field_focus")),
                          ("hover", I("field_hover")), **small)
        cb = self._slice["card"]
        card = dict(border=cb, sticky="nsew", width=2 * cb, height=2 * cb)
        st.element_create("Pp.card", "image", I("card"), **card)
        st.element_create("Pp.cardsel", "image", I("card_sel"), **card)
        st.element_create("Pp.cardalert", "image", I("card_alert"), **card)
        st.element_create("Pp.banner", "image", I("banner"), **card)
        pb = self._slice["pill"]
        for name in ("green", "red", "amber", "neutral"):
            st.element_create(f"Pp.pill.{name}", "image", I(f"pill_{name}"),
                              border=pb, sticky="nsew", width=2 * pb, height=2 * pb)
        st.element_create("Pp.check", "image", I("check_off"),
                          ("disabled selected", I("check_on_disabled")),
                          ("disabled", I("check_disabled")),
                          ("selected active", I("check_on_hover")),
                          ("selected", I("check_on")),
                          ("active", I("check_off_hover")), sticky="")
        st.element_create("Pp.switch", "image", I("switch_off"),
                          ("selected active", I("switch_on_hover")),
                          ("selected", I("switch_on")),
                          ("active", I("switch_off_hover")), sticky="")
        st.element_create("Pp.chevron", "image", I("chevron"),
                          ("disabled", I("chevron_disabled")), sticky="")
        st.element_create("Pp.thumb", "image", I("thumb"), ("active", I("thumb_hover")),
                          ("pressed", I("thumb_hover")), border=(S(6), S(8)), sticky="nsew",
                          width=S(12), height=2 * S(8))
        st.element_create("Pp.hthumb", "image", I("hthumb"), border=(S(8), S(6)), sticky="nsew")

        def button(el):
            return [(el, {"sticky": "nsew", "children": [
                ("Button.padding", {"sticky": "nsew", "children": [
                    ("Button.label", {"sticky": "nsew"})]})]})]
        st.layout("TButton", button("Pp.btn"))
        st.layout("Accent.TButton", button("Pp.acc"))
        st.layout("Subtle.TButton", button("Pp.subtle"))
        st.layout("TEntry", [("Pp.field", {"sticky": "nsew", "children": [
            ("Entry.padding", {"sticky": "nsew", "children": [
                ("Entry.textarea", {"sticky": "nsew"})]})]})])
        st.layout("TCombobox", [("Pp.field", {"sticky": "nsew", "children": [
            ("Pp.chevron", {"side": "right", "sticky": "ns"}),
            ("Combobox.padding", {"expand": "1", "sticky": "nsew", "children": [
                ("Combobox.textarea", {"sticky": "nsew"})]})]})])
        st.layout("TSpinbox", [("Pp.field", {"sticky": "nsew", "children": [
            ("null", {"side": "right", "sticky": "ns", "children": [
                ("Spinbox.uparrow", {"side": "top", "sticky": "e"}),
                ("Spinbox.downarrow", {"side": "bottom", "sticky": "e"})]}),
            ("Spinbox.padding", {"sticky": "nsew", "children": [
                ("Spinbox.textarea", {"sticky": "nsew"})]})]})])
        st.layout("Tile.TFrame", [("Pp.card", {"sticky": "nsew"})])
        st.layout("Selected.Tile.TFrame", [("Pp.cardsel", {"sticky": "nsew"})])
        st.layout("Alert.Tile.TFrame", [("Pp.cardalert", {"sticky": "nsew"})])
        st.layout("Banner.TFrame", [("Pp.banner", {"sticky": "nsew"})])
        for name in ("green", "red", "amber", "neutral"):
            st.layout(f"{name.title()}.Pill.TLabel", [(f"Pp.pill.{name}", {"sticky": "nsew", "children": [
                ("Label.padding", {"sticky": "nsew", "children": [
                    ("Label.label", {"sticky": "nsew"})]})]})])

        def check(el):
            return [("Checkbutton.padding", {"sticky": "nsew", "children": [
                (el, {"side": "left", "sticky": ""}),
                ("Checkbutton.label", {"side": "left", "sticky": "nsew"})]})]
        st.layout("TCheckbutton", check("Pp.check"))
        st.layout("Switch.TCheckbutton", check("Pp.switch"))
        st.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Pp.thumb", {"expand": "1", "sticky": "nswe"})]})])
        st.layout("Horizontal.TScrollbar", [("Horizontal.Scrollbar.trough", {"sticky": "ew", "children": [
            ("Pp.hthumb", {"expand": "1", "sticky": "nswe"})]})])
        st.layout("Treeview", [("Treeview.treearea", {"sticky": "nsew"})])
        self._elements_made = True

    def _configure_styles(self):
        c, S = self.c, self.S
        if not self._elements_made:
            self.style = ttk.Style(self.root)
            self.style.theme_use("clam")
            self._make_elements()
        st = self.style
        body, small, caption = self.font(), self.font(-1), self.font(-2)

        st.configure(".", background=c["bg"], foreground=c["text"], font=body,
                     bordercolor=c["divider"], darkcolor=c["bg"], lightcolor=c["bg"],
                     troughcolor=c["bg"], focuscolor=c["accent"],
                     selectbackground=c["accent"], selectforeground=c["on_accent"],
                     insertcolor=c["text"], fieldbackground=c["field"],
                     insertwidth=max(1, S(1)))
        st.map(".", foreground=[("disabled", c["text3"])])

        for prefix, surface in (("", c["bg"]), ("Card.", c["surface"])):
            st.configure(f"{prefix}TFrame", background=surface)
            st.configure(f"{prefix}TLabel", background=surface, foreground=c["text"])
            st.configure(f"{prefix}Muted.TLabel", background=surface, foreground=c["text2"], font=small)
            st.configure(f"{prefix}Faint.TLabel", background=surface, foreground=c["text3"], font=caption)
            st.configure(f"{prefix}Strong.TLabel", background=surface, foreground=c["text"],
                         font=self.font(0, "semibold"))
            st.configure(f"{prefix}Heading.TLabel", background=surface, foreground=c["text"],
                         font=self.font(1, "semibold"))
            st.configure(f"{prefix}Caps.TLabel", background=surface, foreground=c["text2"],
                         font=self.font(-2, "semibold"))
            st.configure(f"{prefix}Link.TLabel", background=surface, foreground=c["accent_text"],
                         font=small)
            st.configure(f"{prefix}Amber.TLabel", background=surface, foreground=c["amber"], font=small)
            st.configure(f"{prefix}Green.TLabel", background=surface, foreground=c["green"], font=small)
            st.configure(f"{prefix}Red.TLabel", background=surface, foreground=c["red"], font=small)
            st.configure(f"{prefix}Dot.TLabel", background=surface, font=self.font(2))
            pad = (S(12), S(5))
            st.configure(f"{prefix}TButton", background=surface, foreground=c["text"],
                         padding=pad, anchor="center", font=body)
            st.configure(f"{prefix}Accent.TButton", background=surface, foreground=c["on_accent"],
                         padding=pad, anchor="center", font=body)
            st.map(f"{prefix}Accent.TButton",
                   foreground=[("disabled", mix(c["on_accent"], c["accent"], 0.4))])
            # clam gives every TButton a minimum width of 11 characters; an
            # icon button is one glyph wide.
            st.configure(f"{prefix}Subtle.TButton", background=surface, foreground=c["text"],
                         padding=(S(8), S(5)), anchor="center", font=body, width=0)
            st.configure(f"{prefix}Icon.Subtle.TButton", background=surface, foreground=c["text2"],
                         padding=(S(6), S(5)), font=self.icon_font(0))
            st.configure(f"{prefix}TCheckbutton", background=surface, foreground=c["text"],
                         padding=(0, S(2)), font=body)
            st.map(f"{prefix}TCheckbutton", background=[("active", surface)])
            st.configure(f"{prefix}Switch.TCheckbutton", background=surface, foreground=c["text"],
                         padding=(0, S(2)), font=body)
            st.map(f"{prefix}Switch.TCheckbutton", background=[("active", surface)])
            st.configure(f"{prefix}TEntry", background=surface, foreground=c["text"],
                         padding=(S(10), S(6)), font=body)
            st.configure(f"{prefix}TSpinbox", background=surface, foreground=c["text"],
                         padding=(S(10), S(5), S(4), S(5)), arrowsize=S(10), font=body)
            st.configure(f"{prefix}TCombobox", background=surface, foreground=c["text"],
                         padding=(S(10), S(5), 0, S(5)), arrowsize=S(12), font=body)
            st.map(f"{prefix}TCombobox",
                   fieldbackground=[("readonly", c["field"])],
                   foreground=[("disabled", c["text3"]), ("readonly", c["text"])],
                   selectbackground=[("readonly", c["field"])],
                   selectforeground=[("readonly", c["text"])])
            st.configure(f"{prefix}Vertical.TScrollbar", troughcolor=surface, background=surface,
                         bordercolor=surface, arrowsize=S(12))
            st.configure(f"{prefix}Horizontal.TScrollbar", troughcolor=surface, background=surface,
                         bordercolor=surface)

        for name, fg in (("Green", c["green"]), ("Red", c["red"]),
                         ("Amber", c["amber"]), ("Neutral", c["text2"])):
            st.configure(f"{name}.Pill.TLabel", background=c["surface"], foreground=fg,
                         font=self.font(-2, "semibold"), padding=(S(9), S(2)), anchor="center")

        for tile in ("Tile.TFrame", "Selected.Tile.TFrame", "Alert.Tile.TFrame", "Banner.TFrame"):
            st.configure(tile, background=c["bg"])
        st.configure("Banner.TLabel", background=c["red_bg"], foreground=c["red"],
                     font=self.font(0, "semibold"))

        st.configure("Title.TLabel", background=c["bg"], foreground=c["text"],
                     font=self.font(3, "semibold"))
        st.configure("Nav.TLabel", background=c["bg"], foreground=c["text2"],
                     font=body, padding=(S(2), S(6)))
        st.configure("NavActive.TLabel", background=c["bg"], foreground=c["text"],
                     font=self.font(0, "semibold"), padding=(S(2), S(6)))
        st.configure("NavBar.TFrame", background=c["accent"])
        st.configure("Divider.TFrame", background=c["divider"])

        st.configure("Treeview", background=c["surface"], fieldbackground=c["surface"],
                     foreground=c["text"], rowheight=S(28), borderwidth=0, font=body)
        st.map("Treeview",
               background=[("selected", c["accent_subtle"])],
               foreground=[("selected", c["text"])])
        st.configure("Treeview.Heading", background=c["surface"], foreground=c["text2"],
                     relief="flat", borderwidth=0, font=self.font(-1, "semibold"),
                     padding=(S(8), S(6)))
        st.map("Treeview.Heading", background=[("active", c["surface2"])],
               relief=[("pressed", "flat")])

        # Classic ttk.Notebook, restyled as underlined tabs on the window's
        # background (the house style's navigation).
        st.configure("TNotebook", background=c["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        st.configure("TNotebook.Tab", background=c["bg"], foreground=c["text2"], borderwidth=0,
                     padding=(S(12), S(6)), font=body, focuscolor=c["bg"])
        st.map("TNotebook.Tab",
               background=[("selected", c["bg"]), ("active", c["bg"])],
               foreground=[("selected", c["text"]), ("active", c["text"])],
               font=[("selected", self.font(0, "semibold"))],
               lightcolor=[("selected", c["accent"])], bordercolor=[("selected", c["accent"])])

        st.configure("Horizontal.TProgressbar", troughcolor=c["neutral_bg"], background=c["accent"],
                     bordercolor=c["neutral_bg"], lightcolor=c["accent"], darkcolor=c["accent"],
                     thickness=S(3))
        st.configure("TPanedwindow", background=c["bg"])
        st.configure("Sash", sashthickness=S(6), gripcount=0, background=c["bg"])
        st.configure("TSeparator", background=c["divider"])
        st.configure("TLabelframe", background=c["bg"], bordercolor=c["divider"])
        st.configure("TLabelframe.Label", background=c["bg"], foreground=c["text2"],
                     font=self.font(-1, "semibold"))

        # The combobox's drop-down list is a classic Tk listbox.
        r = self.root
        r.option_add("*TCombobox*Listbox.background", c["surface"])
        r.option_add("*TCombobox*Listbox.foreground", c["text"])
        r.option_add("*TCombobox*Listbox.selectBackground", c["accent_subtle"])
        r.option_add("*TCombobox*Listbox.selectForeground", c["text"])
        r.option_add("*TCombobox*Listbox.font", body)
        r.option_add("*TCombobox*Listbox.borderWidth", 0)
        r.option_add("*TCombobox*Listbox.relief", "flat")
        r.option_add("*Menu.font", body)

    def menu_colours(self) -> dict:
        c = self.c
        return dict(bg=c["surface"], fg=c["text"], activebackground=c["accent_subtle"],
                    activeforeground=c["text"], disabledforeground=c["text3"],
                    relief="flat", bd=0, font=self.font())
