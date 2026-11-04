"""The settings a target runs with, and the two places they're edited: the
Settings page (defaults for targets added from now on, plus the app's own
options) and a target's Edit dialog. One form definition serves both, so a
setting can't exist in one and be missing from the other."""
from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Dict

from . import __version__, monitor, tcpudp
from .gui_widgets import ScrollFrame, section, setting_row

REPO_URL = "https://github.com/jamesccupps/PingerPlot"

# start() keyword, kind, default, and the key it has always had in
# settings.json (the names predate this form, and old files must still load).
FIELDS = (
    ("packet_type", "str", "ICMP", "packet_type"),
    ("port", "str", "443", "port"),
    ("interval", "str", "2.5", "interval"),
    ("timeout_ms", "str", "1000", "timeout_ms"),
    ("max_hops", "str", "30", "max_hops"),
    ("packet_size", "str", "32", "packet_size"),
    ("send_delay_ms", "str", "0", "send_delay_ms"),
    ("dscp", "str", "0", "dscp"),
    ("source_ip", "str", "", "source_ip"),
    ("resolve_names", "bool", True, "resolve_names"),
    ("final_hop_only", "bool", False, "final_hop_only"),
    ("alert_enabled", "bool", True, "alerts_enabled"),
    ("alert_loss_pct", "str", "20", "alert_loss"),
    ("alert_latency_ms", "str", "250", "alert_latency"),
    ("alert_window", "str", "20", "alert_window"),
    ("alert_mos", "str", "0", "alert_mos"),
    ("alert_sound", "bool", True, "alert_sound"),
    ("log_path", "str", "", "log_path"),
    ("webhook_url", "str", "", "webhook_url"),
)
NUMBERS = {  # what each number field must parse as, and its name in an error
    "interval": (float, "Probe every"), "timeout_ms": (int, "Reply timeout"),
    "max_hops": (int, "Maximum hops"), "packet_size": (int, "Packet size"),
    "send_delay_ms": (int, "Delay between probes"), "port": (int, "Port"),
    "dscp": (int, "DSCP"), "alert_loss_pct": (float, "Packet loss"),
    "alert_latency_ms": (float, "Latency"), "alert_window": (int, "Over the last"),
    "alert_mos": (float, "MOS"),
}


class OptionVars:
    """Tk variables for every start() option, and the conversions to and
    from a Monitor's options and the settings file."""

    def __init__(self, master) -> None:
        self.vars: Dict[str, tk.Variable] = {}
        for key, kind, default, _skey in FIELDS:
            cls = tk.BooleanVar if kind == "bool" else tk.StringVar
            self.vars[key] = cls(master=master, value=default)

    def __getitem__(self, key: str) -> tk.Variable:
        return self.vars[key]

    def to_options(self) -> dict:
        """start() keyword arguments. Raises ValueError naming the field
        whose text isn't a number."""
        out = {}
        for key, kind, _default, _skey in FIELDS:
            v = self.vars[key].get()
            if key in NUMBERS:
                conv, label = NUMBERS[key]
                try:
                    v = conv(float(str(v).strip())) if conv is int else conv(str(v).strip())
                except ValueError:
                    raise ValueError(f"{label} must be a number (it says {v!r}).") from None
            elif key == "packet_type":
                v = str(v).lower()
            elif kind == "str":
                v = str(v).strip()
            out[key] = v
        return out

    def load_options(self, opts: dict) -> None:
        """Show a Monitor's options (Monitor.start_options())."""
        for key, kind, _default, _skey in FIELDS:
            if key not in opts:
                continue
            v = opts[key]
            if key == "packet_type":
                v = str(v).upper()
            elif kind == "bool":
                v = bool(v)
            elif isinstance(v, float):
                v = f"{v:g}"
            self.vars[key].set(v)

    def load_settings(self, s: dict) -> None:
        for key, kind, _default, skey in FIELDS:
            val = s.get(skey)
            if val is None:
                continue
            if kind == "bool":
                if isinstance(val, bool):
                    self.vars[key].set(val)
            else:
                self.vars[key].set(str(val))

    def save_settings(self, s: dict) -> None:
        for key, kind, _default, skey in FIELDS:
            v = self.vars[key].get()
            s[skey] = bool(v) if kind == "bool" else v


# --- the form ------------------------------------------------------------------

def _spin(look, var, lo, hi, step=1, width=7, fmt=None):
    def make(parent):
        kw = dict(from_=lo, to=hi, increment=step, width=width, textvariable=var)
        if fmt:
            kw["format"] = fmt
        return ttk.Spinbox(parent, style="Card.TSpinbox", **kw)
    return make


def _switch(var):
    return lambda parent: ttk.Checkbutton(parent, variable=var, style="Card.Switch.TCheckbutton")


def _entry(var, width=28):
    return lambda parent: ttk.Entry(parent, textvariable=var, width=width, style="Card.TEntry")


def build_option_form(parent, look, ov: OptionVars, browse_log: Callable[[], None]) -> None:
    """The Probing, Alerts and Logging sections, bound to ``ov``."""
    def probe_type(row):
        box = ttk.Combobox(row, textvariable=ov["packet_type"], values=["ICMP", "TCP", "UDP"],
                           state="readonly", width=7, style="Card.TCombobox")

        def changed(_e=None):
            ptype = ov["packet_type"].get().lower()
            if ptype in tcpudp.DEFAULT_PORTS:
                ov["port"].set(str(tcpudp.DEFAULT_PORTS[ptype]))
        box.bind("<<ComboboxSelected>>", changed)
        return box

    card = section(parent, look, "Probing")
    setting_row(card, look, "Probe type",
                "ICMP needs no administrator rights. TCP and UDP follow the path a service's traffic "
                "takes; tracing every hop with them needs Administrator on Windows.",
                probe_type, first=True)
    setting_row(card, look, "Port",
                "TCP: the service's port. UDP: the first port; each hop uses the next one up.",
                _spin(look, ov["port"], 1, 65535))
    setting_row(card, look, "Probe every (seconds)", "", _spin(look, ov["interval"], 0.5, 60, 0.5))
    setting_row(card, look, "Reply timeout (ms)",
                f"TCP waits at least {monitor.TCP_REFUSAL_FLOOR_MS} ms: Windows takes about 2 s to "
                "report a closed port, and a shorter wait can't tell one from an unreachable host.",
                _spin(look, ov["timeout_ms"], 100, 10000, 100))
    setting_row(card, look, "Maximum hops", "", _spin(look, ov["max_hops"], 1, 64))
    setting_row(card, look, "Packet size (bytes)", "", _spin(look, ov["packet_size"], 0, 1472, 8))
    setting_row(card, look, "Delay between probes (ms)",
                "Spreads each round's probes out, for routers that rate-limit.",
                _spin(look, ov["send_delay_ms"], 0, 1000, 5))
    setting_row(card, look, "DSCP",
                "Marks probes as a traffic class: 46 is EF (voice), 34 AF41 (video), 0 best effort. "
                "Windows ignores it on TCP and UDP unless DisableUserTOSSetting is cleared.",
                _spin(look, ov["dscp"], 0, 63))
    setting_row(card, look, "Source address",
                "Blank lets the system choose the interface. An address this computer doesn't "
                "have is refused, never quietly replaced.",
                _entry(ov["source_ip"], 18))
    setting_row(card, look, "Look up hostnames", "Reverse DNS for every hop.", _switch(ov["resolve_names"]))
    setting_row(card, look, "Destination only",
                "Ping just the destination, without mapping the route.", _switch(ov["final_hop_only"]))

    card = section(parent, look, "Alerts")
    setting_row(card, look, "Alert on the destination",
                "Alerts judge the last hop only: loss at a router in the middle is usually that "
                "router deprioritising ICMP, not a problem on the path.",
                _switch(ov["alert_enabled"]), first=True)
    setting_row(card, look, "Packet loss at or above (%)", "0 turns it off.",
                _spin(look, ov["alert_loss_pct"], 0, 100, 5))
    setting_row(card, look, "Average latency at or above (ms)", "0 turns it off.",
                _spin(look, ov["alert_latency_ms"], 0, 5000, 25))
    setting_row(card, look, "MOS at or below",
                "The voice-quality score, 1 to 5: under 3.6 is poor for a call. 0 turns it off.",
                _spin(look, ov["alert_mos"], 0, 5, 0.1, fmt="%.1f"))
    setting_row(card, look, "Over the last (probes)",
                "How long a problem has to last before it alerts.",
                _spin(look, ov["alert_window"], 2, 300))
    setting_row(card, look, "Play a sound", "", _switch(ov["alert_sound"]))

    def log_control(row):
        box = ttk.Frame(row, style="Card.TFrame")
        ttk.Entry(box, textvariable=ov["log_path"], width=24, style="Card.TEntry").pack(side="left")
        ttk.Button(box, text="Browse…", style="Card.TButton", command=browse_log).pack(
            side="left", padx=(look.S(6), 0))
        return box

    card = section(parent, look, "Logging and webhooks")
    setting_row(card, look, "Log every probe to",
                "A CSV per target, flushed as it goes: probe.csv becomes probe_<target>.csv. Rolls "
                "over at 25 MB, keeping one backup.", log_control, first=True)
    setting_row(card, look, "Webhook",
                "An http(s) address that gets a JSON POST when an alert starts or ends. Delivery "
                "failures show in the target's Events.", _entry(ov["webhook_url"], 30))


def browse_log_into(var: tk.Variable, parent=None) -> None:
    path = filedialog.asksaveasfilename(
        parent=parent, title="Log probes to CSV", defaultextension=".csv",
        initialfile="probe.csv", confirmoverwrite=False,
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
    if path:
        var.set(path)


# --- the Settings page -----------------------------------------------------------

class SettingsPage(ScrollFrame):
    """Defaults for new targets, and the app's own options."""

    def __init__(self, parent, app) -> None:
        super().__init__(parent, app.look)
        look, S = app.look, app.look.S
        body = ttk.Frame(self.inner, padding=(S(24), 0, S(24), S(24)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="New targets use these settings. To change a target that's already "
                             "running, right-click it and choose Edit settings.",
                  style="Muted.TLabel", wraplength=S(680), justify="left").pack(
            side="top", anchor="w", pady=(S(8), 0))
        build_option_form(body, look, app.defaults,
                          lambda: browse_log_into(app.defaults["log_path"], app.root))

        card = section(body, look, "App")

        def theme_control(row):
            labels = {"system": "Match Windows", "dark": "Dark", "light": "Light"}
            shown = tk.StringVar(master=row, value=labels.get(app.theme_var.get(), "Match Windows"))
            box = ttk.Combobox(row, textvariable=shown, values=list(labels.values()),
                               state="readonly", width=15, style="Card.TCombobox")
            back = {v: k for k, v in labels.items()}
            box.bind("<<ComboboxSelected>>", lambda _e: app._apply_theme(back[shown.get()]))
            app.theme_var.trace_add("write", lambda *_a: shown.set(labels.get(app.theme_var.get(), "")))
            return box
        setting_row(card, look, "Theme", "", theme_control, first=True)
        setting_row(card, look, "Resume targets when the app starts",
                    "Each with the settings it was running with.", _switch(app.resume_var))
        app.build_app_settings(card)

        card = section(body, look, "About")

        def links(row):
            box = ttk.Frame(row, style="Card.TFrame")
            for text, url in (("Project page", REPO_URL),
                              ("What's new", f"{REPO_URL}/releases"),
                              ("Report a problem", app.report_url())):
                lab = ttk.Label(box, text=text, style="Card.Link.TLabel", cursor="hand2")
                lab.pack(side="left", padx=(S(14), 0))
                lab.bind("<Button-1>", lambda _e, u=url: webbrowser.open(u))
            return box
        setting_row(card, look, f"PingerPlot {__version__}",
                    "MIT licensed. No third-party dependencies.", links, first=True)


# --- one target's settings ---------------------------------------------------------

class EditTargetDialog(tk.Toplevel):
    """A running (or stopped) target's own settings. Applying restarts it with
    them; its history starts again, as a restart always has."""

    def __init__(self, app, name: str) -> None:
        super().__init__(app.root)
        self.app, self.name = app, name
        look, S = app.look, app.look.S
        self.title(f"Settings for {name}")
        self.transient(app.root)
        self.geometry(f"{S(720)}x{S(640)}")
        look.style_window(self)
        self.ov = OptionVars(self)
        mon = app._monitors.get(name)
        self.ov.load_options(mon.start_options() if mon is not None else app.defaults.to_options())
        if mon is not None:
            self.ov["log_path"].set(monitor.log_path_template(mon.log_path, name))

        foot = ttk.Frame(self, padding=(S(24), S(12)))
        foot.pack(side="bottom", fill="x")
        ttk.Button(foot, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(foot, text="Apply and restart", style="Accent.TButton",
                   command=self.apply).pack(side="right", padx=(0, S(8)))
        ttk.Label(foot, text="Applying restarts the target: its history starts again.",
                  style="Muted.TLabel").pack(side="left")

        scroll = ScrollFrame(self, look)
        scroll.pack(side="top", fill="both", expand=True)
        body = ttk.Frame(scroll.inner, padding=(S(24), 0, S(24), S(16)))
        body.pack(fill="both", expand=True)
        build_option_form(body, look, self.ov, lambda: browse_log_into(self.ov["log_path"], self))
        self.bind("<Escape>", lambda _e: self.destroy())

    def apply(self) -> None:
        try:
            opts = self.ov.to_options()
        except ValueError as exc:
            messagebox.showwarning("Check the settings", str(exc), parent=self)
            return
        self.destroy()
        self.app._start(self.name, opts)
