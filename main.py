"""Lightweight League of Legends Flash cooldown timer overlay.

A small, always-on-top, transparent Tkinter overlay that tracks the enemy
Flash summoner spell cooldown (default 300s) for each lane. Press a hotkey
when an enemy uses Flash to start that lane's countdown.

Hotkeys (configurable in config.json) work globally while you're in game.
"""

from __future__ import annotations

import json
import os
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

try:
    import keyboard  # global hotkeys (Windows-friendly)
except ImportError:  # pragma: no cover - friendly hint if dependency missing
    print("Missing dependency 'keyboard'. Run: pip install -r requirements.txt")
    sys.exit(1)

from league import ChampionWatcher


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Readable, high-contrast countdown palette (kept bright so it pops on the
# dark gradient behind it).
COLOR_IDLE = "#aeb4c4"    # no timer running
COLOR_GREEN = "#5ce68a"   # > 3:00
COLOR_YELLOW = "#ffd24a"  # 1:00 - 3:00
COLOR_ORANGE = "#ff9d3c"  # 0:30 - 1:00
COLOR_RED = "#ff5d5d"     # < 0:30 / up
COLOR_TITLE = "#cdd6ff"
COLOR_HINT = "#aab2c6"

# Diagonal gradient endpoints: harshest (darkest) at the bottom-left corner,
# softest (lighter) toward the top-right.
GRAD_BOTTOM_LEFT = (6, 7, 12)
GRAD_TOP_RIGHT = (34, 37, 50)

DEFAULT_CONFIG = {
    "flash_seconds": 300,
    "always_on_top": True,
    "opacity": 0.85,
    "corner": "bottom-left",
    "bindings": {
        "num 7": "Top",
        "num 8": "Jungle",
        "num 9": "Mid",
        "num 4": "Bot",
        "num 5": "Support",
    },
    "champions": {
        "Top": "",
        "Jungle": "",
        "Mid": "",
        "Bot": "",
        "Support": "",
    },
    "auto_champions": True,
    "track_team": "enemy",
    "reset_all_key": "num 0",
    "quit_key": "esc",
}


def load_config() -> dict:
    """Load config.json, falling back to defaults and writing missing keys."""
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
                config.update(json.load(handle))
        except (json.JSONDecodeError, OSError):
            pass  # keep defaults if the file is broken
    else:
        save_config(config)
    return config


def save_config(config: dict) -> None:
    """Persist config to disk so edits survive between launches."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
    except OSError:
        pass


class FlashTimer:
    """A single independent Flash countdown for one lane."""

    def __init__(self, duration: int) -> None:
        self.duration = duration
        self._expires_at: float | None = None

    def start(self) -> None:
        """(Re)start the timer back to full duration."""
        self._expires_at = time.monotonic() + self.duration

    def clear(self) -> None:
        """Stop the timer (no countdown shown)."""
        self._expires_at = None

    @property
    def running(self) -> bool:
        return self._expires_at is not None

    @property
    def remaining(self) -> float:
        """Seconds left, clamped to >= 0. Zero when up or not running."""
        if self._expires_at is None:
            return 0.0
        return max(0.0, self._expires_at - time.monotonic())


class FlashOverlay:
    """Tkinter overlay that renders all lane Flash timers on a gradient canvas."""

    # Layout metrics (pixels).
    PAD_X = 16
    PAD_TOP = 12
    PAD_BOTTOM = 12
    TITLE_GAP = 8
    ROW_SPACING = 6
    HINT_GAP = 8

    def __init__(self, config: dict) -> None:
        self.config = config
        self.duration = int(config["flash_seconds"])
        self.bindings = config["bindings"]

        # Display label per role: champion name if set, otherwise the role.
        champions = config.get("champions", {})
        self.labels: dict[str, str] = {
            lane: (champions.get(lane) or lane) for lane in self.bindings.values()
        }

        # Optional: auto-fill champion names from League's Live Client API.
        self.watcher: ChampionWatcher | None = None
        if config.get("auto_champions", True):
            self.watcher = ChampionWatcher(track=config.get("track_team", "enemy"))
            self.watcher.start()

        # One independent timer per lane.
        self.timers: dict[str, FlashTimer] = {
            lane: FlashTimer(self.duration) for lane in self.bindings.values()
        }

        self._tab_held = False  # show key hints only while Tab is held
        self._win_w = 0
        self._win_h = 0
        self._grad_img: tk.PhotoImage | None = None

        self.root = tk.Tk()
        self.root.title("Flash Timers")
        self.root.configure(bg="#06070c")
        self.root.attributes("-topmost", bool(config["always_on_top"]))
        try:
            # Uniform window transparency so the game shows through the overlay.
            self.root.attributes("-alpha", float(config["opacity"]))
        except tk.TclError:
            pass
        self.root.overrideredirect(True)  # borderless floating overlay

        self._build_ui()
        self._place_window()
        self._register_hotkeys()
        self._watch_tab()

        # Quit on Escape from the window too.
        self.root.bind("<Escape>", lambda _e: self.quit())

    # ----- UI construction -------------------------------------------------

    def _build_ui(self) -> None:
        """Create the canvas, fonts and text items, then size everything."""
        self.title_font = tkfont.Font(family="Consolas", size=12, weight="bold")
        self.row_font = tkfont.Font(family="Consolas", size=18, weight="bold")
        self.hint_font = tkfont.Font(family="Consolas", size=9)

        self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        # Background gradient sits behind everything.
        self.bg_item = self.canvas.create_image(0, 0, anchor="nw")

        self.title_item = self.canvas.create_text(
            0, 0, anchor="nw", text="⚡ Flash Timers",
            font=self.title_font, fill=COLOR_TITLE,
        )
        self.row_items: dict[str, int] = {
            lane: self.canvas.create_text(
                0, 0, anchor="nw", text="", font=self.row_font, fill=COLOR_IDLE
            )
            for lane in self.timers
        }
        hint_text = "  ".join(
            f"{k.replace('num ', 'Num')}:{v}" for k, v in self.bindings.items()
        )
        self.hint_item = self.canvas.create_text(
            0, 0, anchor="nw", text=hint_text, font=self.hint_font,
            fill=COLOR_HINT, state="hidden",
        )

        self._make_draggable()
        self._relayout()

    def _row_text(self, lane: str, value: str) -> str:
        """Pad the label so the countdown column lines up across rows."""
        width = max(8, *(len(name) for name in self.labels.values()))
        return f"{self.labels[lane]:<{width}}  {value}"

    def _relayout(self) -> None:
        """Recompute size/positions and rebuild the gradient (champion changes)."""
        # Set current row texts so width measurement reflects real content.
        for lane in self.timers:
            self.canvas.itemconfig(self.row_items[lane], text=self._row_text(lane, "0:00"))

        # Width: the widest of title, any row, and the hint.
        widths = [self.title_font.measure("⚡ Flash Timers"), self.hint_font.measure(
            self.canvas.itemcget(self.hint_item, "text")
        )]
        widths += [
            self.row_font.measure(self.canvas.itemcget(self.row_items[lane], "text"))
            for lane in self.timers
        ]
        width = max(widths) + 2 * self.PAD_X

        # Vertical stacking.
        title_h = self.title_font.metrics("linespace")
        row_h = self.row_font.metrics("linespace")
        hint_h = self.hint_font.metrics("linespace")

        y = self.PAD_TOP
        self.canvas.coords(self.title_item, self.PAD_X, y)
        y += title_h + self.TITLE_GAP
        for lane in self.timers:
            self.canvas.coords(self.row_items[lane], self.PAD_X, y)
            y += row_h + self.ROW_SPACING
        # Reserve hint space even when hidden so toggling never resizes/jumps.
        y += self.HINT_GAP - self.ROW_SPACING
        self.canvas.coords(self.hint_item, self.PAD_X, y)
        y += hint_h
        height = y + self.PAD_BOTTOM

        self._win_w, self._win_h = width, height
        self.canvas.config(width=width, height=height)
        x = self.root.winfo_x()
        wy = self.root.winfo_y()
        self.root.geometry(f"{width}x{height}+{x}+{wy}")
        self._draw_gradient(width, height)

    def _draw_gradient(self, w: int, h: int) -> None:
        """Render a smooth diagonal black gradient (dark bottom-left)."""
        bl, tr = GRAD_BOTTOM_LEFT, GRAD_TOP_RIGHT
        total = (w - 1) + (h - 1) or 1
        # Pre-compute one colour per diagonal step (cheap: w+h, not w*h).
        lut = []
        for s in range(total + 1):
            t = s / total
            r = int(bl[0] + (tr[0] - bl[0]) * t)
            g = int(bl[1] + (tr[1] - bl[1]) * t)
            b = int(bl[2] + (tr[2] - bl[2]) * t)
            lut.append(f"#{r:02x}{g:02x}{b:02x}")

        img = tk.PhotoImage(width=w, height=h)
        for y in range(h):
            base = h - 1 - y  # 0 at bottom row -> darkest on the left
            img.put("{" + " ".join(lut[x + base] for x in range(w)) + "}", to=(0, y))

        self._grad_img = img  # keep a reference so it isn't garbage-collected
        self.canvas.itemconfig(self.bg_item, image=img)
        self.canvas.tag_lower(self.bg_item)  # keep behind the text

    def _make_draggable(self) -> None:
        """Drag the borderless overlay from anywhere on the canvas."""
        self._drag = {"x": 0, "y": 0}

        def start(event: tk.Event) -> None:
            self._drag["x"] = event.x
            self._drag["y"] = event.y

        def move(event: tk.Event) -> None:
            x = self.root.winfo_x() + event.x - self._drag["x"]
            y = self.root.winfo_y() + event.y - self._drag["y"]
            self.root.geometry(f"+{x}+{y}")

        self.canvas.bind("<Button-1>", start)
        self.canvas.bind("<B1-Motion>", move)

    def _place_window(self) -> None:
        """Restore saved position, or anchor to the configured screen corner."""
        self.root.update_idletasks()
        pos = self.config.get("window_position")
        if pos:
            self.root.geometry(f"+{pos[0]}+{pos[1]}")
            return

        margin = 12
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        corner = self.config.get("corner", "bottom-left")
        x = margin if "left" in corner else screen_w - self._win_w - margin
        # Leave room above the Windows taskbar for bottom corners.
        y = margin if "top" in corner else screen_h - self._win_h - 48
        self.root.geometry(f"+{x}+{y}")

    # ----- Input -----------------------------------------------------------

    def _register_hotkeys(self) -> None:
        """Bind global hotkeys to start/reset timers."""
        for key, lane in self.bindings.items():
            keyboard.add_hotkey(key, self._start_lane, args=(lane,))
        keyboard.add_hotkey(self.config["reset_all_key"], self._reset_all)
        keyboard.add_hotkey(self.config["quit_key"], self.quit)

    def _watch_tab(self) -> None:
        """Track Tab held state globally and poll it to toggle the hint."""
        keyboard.on_press_key("tab", lambda _e: setattr(self, "_tab_held", True))
        keyboard.on_release_key("tab", lambda _e: setattr(self, "_tab_held", False))
        self._poll_hint()

    def _poll_hint(self) -> None:
        """Show the key-binding hint only while Tab is held (low CPU poll)."""
        state = "normal" if self._tab_held else "hidden"
        if self.canvas.itemcget(self.hint_item, "state") != state:
            self.canvas.itemconfig(self.hint_item, state=state)
        self.root.after(120, self._poll_hint)

    def _start_lane(self, lane: str) -> None:
        self.timers[lane].start()

    def _reset_all(self) -> None:
        for timer in self.timers.values():
            timer.clear()

    # ----- Rendering -------------------------------------------------------

    @staticmethod
    def _color_for(remaining: float, running: bool) -> str:
        """Color-code the countdown by urgency."""
        if not running:
            return COLOR_IDLE
        if remaining <= 0 or remaining < 30:
            return COLOR_RED
        if remaining < 60:
            return COLOR_ORANGE
        if remaining < 180:
            return COLOR_YELLOW
        return COLOR_GREEN

    @staticmethod
    def _format(remaining: float, running: bool) -> str:
        if not running:
            return "-"
        if remaining <= 0:
            return "UP"
        minutes, seconds = divmod(int(remaining + 0.999), 60)
        return f"{minutes}:{seconds:02d}"

    def _tick(self) -> None:
        """Refresh every label once per second (keeps CPU near idle)."""
        self._refresh_auto_labels()
        flash = False
        for lane, timer in self.timers.items():
            value = self._format(timer.remaining, timer.running)
            color = self._color_for(timer.remaining, timer.running)
            self.canvas.itemconfig(
                self.row_items[lane], text=self._row_text(lane, value), fill=color
            )
            if timer.running and timer.remaining <= 0:
                flash = True

        # Subtle flash of the title when any Flash is back up.
        on = int(time.monotonic()) % 2 == 0
        title_color = COLOR_RED if (flash and on) else COLOR_TITLE
        self.canvas.itemconfig(self.title_item, fill=title_color)

        self.root.after(1000, self._tick)

    def _refresh_auto_labels(self) -> None:
        """Pull champion names from the watcher and update row labels."""
        if self.watcher is None:
            return
        roles = self.watcher.roles
        if not roles:
            return
        changed = False
        for lane in self.labels:
            champion = roles.get(lane)
            if champion and self.labels[lane] != champion:
                self.labels[lane] = champion
                changed = True
        if changed:
            self._relayout()  # names changed width, so re-size and re-align

    def quit(self) -> None:
        """Save window position and close."""
        try:
            self.config["window_position"] = [self.root.winfo_x(), self.root.winfo_y()]
            save_config(self.config)
        except tk.TclError:
            pass
        if self.watcher is not None:
            self.watcher.stop()
        keyboard.unhook_all()
        self.root.destroy()

    def run(self) -> None:
        self._tick()
        self.root.mainloop()


def main() -> None:
    config = load_config()
    FlashOverlay(config).run()


if __name__ == "__main__":
    main()
