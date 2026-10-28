#!/usr/bin/env python3
"""Separate "drink water" and "walk 2 min" reminders.

A small card rises from the bottom of the screen up the right side and
settles in the top-right corner, then waits until "Done" is clicked. Water
and walk each have their own schedule (on/off, first and last reminder, how
often, which days); when both are due at once, one card shows both. The gear
button on the card opens the settings, saved in CONFIG_FILE.

A systemd user timer runs this every 15 minutes; it only shows a card when a
reminder is due according to the settings.

Usage:
  reminder.py               show a card if a reminder is due now
  reminder.py --settings    open the settings window only
  reminder.py --test        show a water + walk card now, whatever the time
  reminder.py --test water  show a water-only card now (or: --test walk)
  reminder.py --bottom      settle in the bottom-right corner instead of the top
"""
import fcntl
import json
import math
import os
import sys
import time

# Wayland does not let apps place their own windows; XWayland does.
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

CARD_W, CARD_H = 500, 120
MARGIN = 14
SLIDE_SECONDS = 4.0
LOCK_FILE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "water-walk-reminder.lock")
CONFIG_FILE = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "water-walk-reminder.json"
)
KINDS = ("water", "walk")
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
DEFAULTS = {
    "water": {"on": True, "start_hour": 11, "end_hour": 20, "every_minutes": 30, "days": ALL_DAYS},
    "walk": {"on": True, "start_hour": 11, "end_hour": 20, "every_minutes": 60, "days": ALL_DAYS},
}
LABELS = {"water": "💧 Water", "walk": "🚶 Walk"}
TEXT = {  # (title, body) for each combination of due reminders
    ("water",): ("Water break 💧", "Drink a glass of water."),
    ("walk",): ("Walk break 🚶", "Stand up and walk\nfor 2 minutes."),
    ("water", "walk"): ("Time to move 💧🚶", "Drink a glass of water\nand walk for 2 minutes."),
}
INTERVALS = [15, 30, 45, 60, 90, 120]  # multiples of the timer's 15-minute tick
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def ease_out(t):
    return 1 - (1 - t) ** 3


def hour_label(h):
    return time.strftime("%I:%M %p", time.struct_time((2000, 1, 1, h, 0, 0, 0, 1, -1)))


def load_config():
    cfg = {kind: dict(rule) for kind, rule in DEFAULTS.items()}
    try:
        with open(CONFIG_FILE) as f:
            saved = json.load(f)
        for kind in KINDS:
            if isinstance(saved.get(kind), dict):
                cfg[kind].update({k: v for k, v in saved[kind].items() if k in DEFAULTS[kind]})
    except (OSError, ValueError, AttributeError):
        pass
    return cfg


def save_config(cfg):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def is_due(rule, now):
    """True on the 15-minute tick that starts one of this reminder's slots."""
    if not rule["on"] or now.tm_wday not in rule["days"]:
        return False
    minute = now.tm_hour * 60 + now.tm_min // 15 * 15
    since = minute - rule["start_hour"] * 60
    return 0 <= since and minute <= rule["end_hour"] * 60 and since % rule["every_minutes"] == 0


def due_kinds(cfg, now):
    return tuple(kind for kind in KINDS if is_due(cfg[kind], now))


def summary(rule):
    if not rule["on"]:
        return "Off."
    if not rule["days"]:
        return "No days selected: off."
    count = (rule["end_hour"] - rule["start_hour"]) * 60 // rule["every_minutes"] + 1
    days = "every day" if len(rule["days"]) == 7 else ", ".join(DAY_NAMES[d] for d in sorted(rule["days"]))
    return (f"{hour_label(rule['start_hour'])} to {hour_label(rule['end_hour'])}, "
            f"every {rule['every_minutes']} min: {count} a day, {days}.")


class RuleEditor:
    """The on switch, times and days for one reminder, laid out in a grid."""

    def __init__(self, grid, rule):
        self.on = Gtk.Switch(halign=Gtk.Align.START)
        self.on.set_active(rule["on"])

        self.start = Gtk.ComboBoxText()
        self.end = Gtk.ComboBoxText()
        for h in range(24):
            self.start.append(str(h), hour_label(h))
            self.end.append(str(h), hour_label(h))
        self.start.set_active_id(str(rule["start_hour"]))
        self.end.set_active_id(str(rule["end_hour"]))

        self.every = Gtk.ComboBoxText()
        for m in INTERVALS:
            self.every.append(str(m), f"{m} minutes" if m < 60 else f"{m / 60:g} hour{'s' if m > 60 else ''}")
        if not self.every.set_active_id(str(rule["every_minutes"])):
            self.every.set_active_id("60")

        days = Gtk.Box(spacing=6)
        self.day_checks = []
        for i, name in enumerate(DAY_NAMES):
            check = Gtk.CheckButton(label=name)
            check.set_active(i in rule["days"])
            days.pack_start(check, False, False, 0)
            self.day_checks.append(check)

        for row, (label, widget) in enumerate([
            ("On", self.on), ("First reminder", self.start), ("Last reminder", self.end),
            ("Every", self.every), ("Days", days),
        ]):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)

        self.summary = Gtk.Label(label=summary(rule), xalign=0)
        self.summary.set_line_wrap(True)
        self.summary.set_max_width_chars(48)
        grid.attach(self.summary, 0, 5, 2, 1)

    def value(self):
        return {
            "on": self.on.get_active(),
            "start_hour": int(self.start.get_active_id()),
            "end_hour": int(self.end.get_active_id()),
            "every_minutes": int(self.every.get_active_id()),
            "days": [i for i, c in enumerate(self.day_checks) if c.get_active()],
        }


class Settings(Gtk.Window):
    def __init__(self, on_close):
        super().__init__(title="Water + walk reminder settings")
        self.set_keep_above(True)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(False)
        self.connect("destroy", lambda *_: on_close())
        cfg = load_config()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.set_border_width(16)
        self.add(outer)

        self.editors = {}
        for kind in KINDS:
            frame = Gtk.Frame(label=LABELS[kind])
            grid = Gtk.Grid(row_spacing=8, column_spacing=12)
            grid.set_border_width(10)
            frame.add(grid)
            outer.pack_start(frame, False, False, 0)
            self.editors[kind] = RuleEditor(grid, cfg[kind])

        self.status = Gtk.Label(xalign=0)
        outer.pack_start(self.status, False, False, 0)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.destroy())
        save = Gtk.Button(label="Save")
        save.get_style_context().add_class("suggested-action")
        save.connect("clicked", self.on_save)
        buttons.pack_start(close, False, False, 0)
        buttons.pack_start(save, False, False, 0)
        outer.pack_start(buttons, False, False, 0)

    def on_save(self, *_):
        cfg = {kind: editor.value() for kind, editor in self.editors.items()}
        bad = [LABELS[k] for k in KINDS if cfg[k]["end_hour"] < cfg[k]["start_hour"]]
        if bad:
            self.status.set_markup(
                f"<span foreground='#b3261e'>{' and '.join(bad)}: last reminder must be after the first one.</span>"
            )
            return
        try:
            save_config(cfg)
        except OSError as e:
            self.status.set_markup(f"<span foreground='#b3261e'>Could not save: {GLib.markup_escape_text(str(e))}</span>")
            return
        for kind, editor in self.editors.items():
            editor.summary.set_text(summary(cfg[kind]))
        self.status.set_markup("<b>Saved.</b>")


class Reminder(Gtk.Window):
    def __init__(self, kinds, bottom):
        super().__init__(type=Gtk.WindowType.POPUP)  # no frame, stays on top
        self.set_name("card")
        self.kinds = kinds
        self.settings = None
        self.set_default_size(CARD_W, CARD_H)
        self.set_keep_above(True)
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

        mon = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
        area = mon.get_workarea()
        self.x = area.x + area.width - CARD_W - MARGIN
        self.start_y = area.y + area.height  # just below the bottom edge
        self.end_y = area.y + area.height - CARD_H - MARGIN if bottom else area.y + MARGIN
        self.t0 = time.monotonic()
        self.phase = 0.0

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_border_width(14)
        self.add(box)

        self.man = Gtk.DrawingArea()
        self.man.set_size_request(70, 90)
        self.man.connect("draw", self.draw_man)
        box.pack_start(self.man, False, False, 0)

        heading, message = TEXT[kinds]
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title = Gtk.Label(xalign=0)
        title.set_markup(f"<span size='large' weight='bold' foreground='#0b3d5c'>{heading}</span>")
        body = Gtk.Label(xalign=0)
        body.set_markup(f"<span foreground='#24506b'>{message}</span>")
        stamp = Gtk.Label(xalign=0)
        stamp.set_markup(f"<span size='small' foreground='#6b8796'>{time.strftime('%I:%M %p')}</span>")
        text.pack_start(title, False, False, 0)
        text.pack_start(body, False, False, 0)
        text.pack_start(stamp, False, False, 0)
        box.pack_start(text, True, True, 0)

        done = Gtk.Button(label="  Done ✓  ")
        done.set_valign(Gtk.Align.CENTER)
        done.connect("clicked", lambda *_: Gtk.main_quit())
        box.pack_end(done, False, False, 0)

        gear = Gtk.Button(label="⚙")
        gear.set_tooltip_text("Set reminder times")
        gear.set_valign(Gtk.Align.CENTER)
        gear.get_style_context().add_class("gear")
        gear.connect("clicked", self.open_settings)
        box.pack_end(gear, False, False, 0)

        css = Gtk.CssProvider()
        css.load_from_data(b"""
            #card { background-color: transparent; }
            #card > box { background-color: #e8f6ff; border-radius: 16px;
                          border: 2px solid #5fb4e6; }
            #card button { background-image: none; background-color: #1f8fd6; color: white;
                           font-weight: bold; border-radius: 10px; padding: 8px 6px; border: none; }
            #card button:hover { background-color: #136fab; }
            #card button.gear { background-color: transparent; color: #1f8fd6;
                                font-size: 18px; padding: 4px 8px; }
            #card button.gear:hover { background-color: #cfe9fa; }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.move(self.x, self.start_y)
        self.show_all()
        GLib.timeout_add(16, self.tick)

    def open_settings(self, *_):
        if self.settings is None:
            self.settings = Settings(on_close=lambda: setattr(self, "settings", None))
            self.settings.show_all()
        self.settings.present()

    def tick(self):
        t = min((time.monotonic() - self.t0) / SLIDE_SECONDS, 1.0)
        y = int(self.start_y + (self.end_y - self.start_y) * ease_out(t))
        self.move(self.x, y)
        # Brisk steps while sliding in, gentle marching in place after.
        self.phase += 0.22 if t < 1.0 else 0.12
        self.man.queue_draw()
        return True

    def draw_drop(self, cr, dx, dy, scale):
        cr.set_source_rgb(0.37, 0.71, 0.90)
        cr.move_to(dx, dy - 9 * scale)
        cr.curve_to(dx + 7 * scale, dy, dx + 7 * scale, dy + 8 * scale, dx, dy + 8 * scale)
        cr.curve_to(dx - 7 * scale, dy + 8 * scale, dx - 7 * scale, dy, dx, dy - 9 * scale)
        cr.fill()

    def draw_man(self, widget, cr):
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        if "walk" not in self.kinds:  # water only: a big, gently bobbing drop
            self.draw_drop(cr, w / 2, h / 2 + math.sin(self.phase) * 3, 3.2)
            return False

        s = math.sin(self.phase)
        bob = abs(math.cos(self.phase)) * 2
        cx, hip_y = w / 2, h * 0.58 - bob
        shoulder_y, leg = h * 0.30 - bob, h * 0.34

        cr.set_line_cap(1)  # round
        cr.set_line_width(5)
        cr.set_source_rgb(0.12, 0.45, 0.70)

        cr.arc(cx, h * 0.15 - bob, 8, 0, 2 * math.pi)  # head
        cr.fill()
        cr.move_to(cx, shoulder_y)  # body
        cr.line_to(cx, hip_y)
        cr.stroke()

        for sign in (1, -1):  # legs (knee + foot) and arms swing opposite
            a = sign * s * 0.55
            kx, ky = cx + math.sin(a) * leg * 0.5, hip_y + math.cos(a) * leg * 0.5
            b = a - abs(s) * 0.3 * sign if sign * s < 0 else a
            fx, fy = kx + math.sin(b) * leg * 0.5, ky + math.cos(b) * leg * 0.5
            cr.move_to(cx, hip_y)
            cr.line_to(kx, ky)
            cr.line_to(fx, fy)
            cr.line_to(fx + 5, fy)
            cr.stroke()

            arm = -a * 0.9
            cr.move_to(cx, shoulder_y + 3)
            cr.line_to(cx + math.sin(arm) * leg * 0.55, shoulder_y + 3 + math.cos(arm) * leg * 0.55)
            cr.stroke()

        if "water" in self.kinds:
            self.draw_drop(cr, w - 10, h * 0.12, 1.0)
        return False


def main():
    args = sys.argv[1:]
    if "--settings" in args:
        Settings(on_close=Gtk.main_quit).show_all()
        Gtk.main()
        return
    if "--test" in args:
        kinds = tuple(k for k in KINDS if k in args) or KINDS
    else:
        kinds = due_kinds(load_config(), time.localtime())
    if not kinds:
        return
    lock = open(LOCK_FILE, "w")
    try:  # one card at a time: skip if the previous one is still open
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    Reminder(kinds, bottom="--bottom" in args)
    Gtk.main()


if __name__ == "__main__":
    main()
