"""Lightweight League of Legends Flash cooldown timer overlay.

A small, always-on-top, transparent Tkinter overlay that tracks the enemy
Flash summoner spell cooldown (default 300s) for each lane. Press a hotkey
when an enemy uses Flash to start that lane's countdown.

Hotkeys (configurable in config.json) work globally while you're in game.
"""

from __future__ import annotations

import json
import math
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
COLOR_EXPIRED_DIM = "#7a2a2a"  # dim red used for the blink when expired
COLOR_HINT = "#aab2c6"

# Champion-name and reset-icon interaction colours.
COLOR_NAME = "#c4cad6"        # champion name at rest
COLOR_NAME_HOVER = "#ffffff"  # "sharpened" (brightened) on hover
COLOR_START_FLASH = "#8dffb2" # brief confirm flash when a timer is started
COLOR_ICON = "#8b93a7"        # reset icon stroke
COLOR_ICON_HOVER = "#e9edf5"
COLOR_ICON_BG = "#161a22"     # subtle circular hit area
COLOR_ICON_BG_HOVER = "#2a3140"

# Fully-transparent colour key (Windows): kept very dark so any anti-aliased
# halo around the text stays a subtle dark outline rather than a coloured fringe.
CHROMA = "#010101"

# Diagonal gradient endpoints: harshest (darkest) at the bottom-left corner,
# softest (lighter) toward the top-right.
GRAD_BOTTOM_LEFT = (6, 7, 12)
GRAD_TOP_RIGHT = (34, 37, 50)

# 8x8 ordered-dither matrix, normalised to thresholds in (0, 1). Used to fake
# partial background transparency against the transparent colour key.
_BAYER8_RAW = [
    [0, 48, 12, 60, 3, 51, 15, 63],
    [32, 16, 44, 28, 35, 19, 47, 31],
    [8, 56, 4, 52, 11, 59, 7, 55],
    [40, 24, 36, 20, 43, 27, 39, 23],
    [2, 50, 14, 62, 1, 49, 13, 61],
    [34, 18, 46, 30, 33, 17, 45, 29],
    [10, 58, 6, 54, 9, 57, 5, 53],
    [42, 26, 38, 22, 41, 25, 37, 21],
]
_BAYER8 = [[(v + 0.5) / 64 for v in row] for row in _BAYER8_RAW]

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
    "double_press_seconds": 0.5,
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
    ROW_SPACING = 6
    HINT_GAP = 8
    GAP_NAME_TIME = 18  # space between the name column and the countdown
    GAP_TIME_ICON = 16  # space between the countdown and the reset icon
    ICON_R = 10         # reset icon (circular hit area) radius

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

        # Require two presses within this window to confirm a Flash was cast.
        self.double_press_window = float(config.get("double_press_seconds", 0.5))
        self._last_press: dict[str, float] = {}

        self._tab_held = False  # show key hints only while Tab is held
        self._hover_lane: str | None = None  # lane whose name is hovered
        self._win_w = 0
        self._win_h = 0
        self._grad_img: tk.PhotoImage | None = None
        self.opacity = float(config["opacity"])
        self._use_chroma = False

        self.root = tk.Tk()
        self.root.title("")
        self.root.configure(bg=CHROMA)
        self.root.attributes("-topmost", bool(config["always_on_top"]))
        self.root.overrideredirect(True)  # borderless floating overlay
        try:
            # Windows: render the CHROMA colour fully see-through. This clears
            # the background independently of the (always opaque) text, so the
            # timers stay readable even at opacity 0.
            self.root.attributes("-transparentcolor", CHROMA)
            self._use_chroma = True
        except tk.TclError:
            # Other platforms: fall back to uniform window transparency.
            try:
                self.root.attributes("-alpha", max(0.2, self.opacity))
            except tk.TclError:
                pass

        self._build_ui()
        self._place_window()
        self._register_hotkeys()
        self._watch_tab()

        # Quit on Escape from the window too.
        self.root.bind("<Escape>", lambda _e: self.quit())

    # ----- UI construction -------------------------------------------------

    def _build_ui(self) -> None:
        """Create the canvas, fonts and text items, then size everything."""
        self.row_font = tkfont.Font(family="Consolas", size=18, weight="bold")
        self.hint_font = tkfont.Font(family="Consolas", size=9)

        self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0, bg=CHROMA)
        self.canvas.pack(fill="both", expand=True)

        # Background gradient sits behind everything.
        self.bg_item = self.canvas.create_image(0, 0, anchor="nw")

        # Each lane row: a clickable champion name (start), a countdown value,
        # and a reset icon (drawn per-row later in _relayout).
        self.name_items: dict[str, int] = {}
        self.time_items: dict[str, int] = {}
        for lane in self.timers:
            name_id = self.canvas.create_text(
                0, 0, anchor="w", text=self.labels[lane],
                font=self.row_font, fill=COLOR_NAME,
            )
            time_id = self.canvas.create_text(
                0, 0, anchor="w", text="-", font=self.row_font, fill=COLOR_IDLE
            )
            self.name_items[lane] = name_id
            self.time_items[lane] = time_id
            # Click the name to start the timer; hovering "sharpens" it.
            self.canvas.tag_bind(name_id, "<Button-1>", lambda _e, l=lane: self._on_name_click(l))
            self.canvas.tag_bind(name_id, "<Enter>", lambda _e, l=lane: self._on_name_enter(l))
            self.canvas.tag_bind(name_id, "<Leave>", lambda _e, l=lane: self._on_name_leave(l))

        hint_text = "  ".join(
            f"{k.replace('num ', 'Num')}:{v}" for k, v in self.bindings.items()
        )
        self.hint_item = self.canvas.create_text(
            0, 0, anchor="nw", text=hint_text, font=self.hint_font,
            fill=COLOR_HINT, state="hidden",
        )

        self._make_draggable()
        self._relayout()

    def _draw_reset_icon(self, cx: float, cy: float, lane: str) -> None:
        """Draw a small, easy-to-click restart icon (circular arrow) for a lane."""
        tag, bg_tag, fg_tag = self._reset_tags(lane)
        r = self.ICON_R

        # Subtle filled circle acts as the (generous) click target.
        self.canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=COLOR_ICON_BG, outline="", tags=(tag, bg_tag),
        )
        # Circular arrow ring with a gap where the arrowhead goes.
        rr = r - 4
        self.canvas.create_arc(
            cx - rr, cy - rr, cx + rr, cy + rr,
            start=60, extent=270, style="arc",
            outline=COLOR_ICON, width=2, tags=(tag, fg_tag),
        )
        # Arrowhead at the open end of the ring (makes it read as "restart").
        end = math.radians(60 + 270)
        tx = cx + rr * math.cos(end)
        ty = cy - rr * math.sin(end)
        dirx, diry = -math.sin(end), -math.cos(end)  # CCW tangent (screen coords)
        perpx, perpy = -diry, dirx
        ah = 4.0
        self.canvas.create_polygon(
            tx + dirx * ah, ty + diry * ah,
            tx + perpx * ah, ty + perpy * ah,
            tx - perpx * ah, ty - perpy * ah,
            fill=COLOR_ICON, outline="", tags=(tag, fg_tag),
        )

        self.canvas.tag_bind(tag, "<Button-1>", lambda _e, l=lane: self._on_reset_click(l))
        self.canvas.tag_bind(tag, "<Enter>", lambda _e, l=lane: self._on_icon_enter(l))
        self.canvas.tag_bind(tag, "<Leave>", lambda _e, l=lane: self._on_icon_leave(l))

    def _render_row(self, lane: str, blink_on: bool = True) -> None:
        """Update one lane's countdown text and colour."""
        timer = self.timers[lane]
        value = self._format(timer.remaining, timer.running)
        if timer.running and timer.remaining <= 0:
            color = COLOR_RED if blink_on else COLOR_EXPIRED_DIM  # blink when up
        else:
            color = self._color_for(timer.remaining, timer.running)
        self.canvas.itemconfig(self.time_items[lane], text=value, fill=color)

    # ----- Mouse interaction ----------------------------------------------

    @staticmethod
    def _reset_tags(lane: str) -> tuple[str, str, str]:
        """Canvas tag names for a lane's reset icon (space-safe)."""
        safe = lane.replace(" ", "_")
        return f"reset-{safe}", f"reset-bg-{safe}", f"reset-fg-{safe}"

    def _on_name_click(self, lane: str) -> None:
        """Start (or restart) a lane's timer and flash the name as feedback."""
        self.timers[lane].start()
        self._render_row(lane)
        self.canvas.itemconfig(self.name_items[lane], fill=COLOR_START_FLASH)
        self.root.after(200, lambda l=lane: self._end_name_flash(l))

    def _end_name_flash(self, lane: str) -> None:
        fill = COLOR_NAME_HOVER if self._hover_lane == lane else COLOR_NAME
        self.canvas.itemconfig(self.name_items[lane], fill=fill)

    def _on_name_enter(self, lane: str) -> None:
        self._hover_lane = lane
        self.canvas.itemconfig(self.name_items[lane], fill=COLOR_NAME_HOVER)
        self.canvas.configure(cursor="hand2")

    def _on_name_leave(self, lane: str) -> None:
        if self._hover_lane == lane:
            self._hover_lane = None
        self.canvas.itemconfig(self.name_items[lane], fill=COLOR_NAME)
        self.canvas.configure(cursor="")

    def _on_reset_click(self, lane: str) -> None:
        """Reset a lane fully back to the unknown (\"-\") state."""
        self.timers[lane].clear()
        self._render_row(lane)

    def _on_icon_enter(self, lane: str) -> None:
        _tag, bg_tag, fg_tag = self._reset_tags(lane)
        self.canvas.itemconfig(bg_tag, fill=COLOR_ICON_BG_HOVER)
        self.canvas.itemconfig(fg_tag, fill=COLOR_ICON_HOVER, outline=COLOR_ICON_HOVER)
        self.canvas.configure(cursor="hand2")

    def _on_icon_leave(self, lane: str) -> None:
        _tag, bg_tag, fg_tag = self._reset_tags(lane)
        self.canvas.itemconfig(bg_tag, fill=COLOR_ICON_BG)
        self.canvas.itemconfig(fg_tag, fill=COLOR_ICON, outline=COLOR_ICON)
        self.canvas.configure(cursor="")

    def _relayout(self) -> None:
        """Recompute size/positions, redraw reset icons and the gradient."""
        # Champion names may have changed (auto-detect) since the last layout.
        for lane in self.timers:
            self.canvas.itemconfig(self.name_items[lane], text=self.labels[lane])

        row_h = self.row_font.metrics("linespace")
        hint_h = self.hint_font.metrics("linespace")

        # Column widths: name (widest label), countdown, and the icon.
        name_col_w = max(self.row_font.measure(self.labels[l]) for l in self.timers)
        time_col_w = self.row_font.measure("0:00")
        icon_d = 2 * self.ICON_R
        content_w = (
            self.PAD_X + name_col_w + self.GAP_NAME_TIME + time_col_w
            + self.GAP_TIME_ICON + icon_d + self.PAD_X
        )
        hint_w = (
            self.hint_font.measure(self.canvas.itemcget(self.hint_item, "text"))
            + 2 * self.PAD_X
        )
        width = max(content_w, hint_w)

        time_x = self.PAD_X + name_col_w + self.GAP_NAME_TIME
        icon_cx = width - self.PAD_X - self.ICON_R

        y = self.PAD_TOP
        for lane in self.timers:
            cy = y + row_h / 2
            self.canvas.coords(self.name_items[lane], self.PAD_X, cy)
            self.canvas.coords(self.time_items[lane], time_x, cy)
            self.canvas.delete(self._reset_tags(lane)[0])  # redraw at new position
            self._draw_reset_icon(icon_cx, cy, lane)
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
        """Render the diagonal black gradient (darkest at the bottom-left).

        When a transparent colour key is available, `opacity` controls how much
        of the gradient is drawn via an ordered (Bayer) dither: at 0 every pixel
        is the clear key (fully see-through), at 1 it is solid. The text, drawn
        on top, always stays fully opaque.
        """
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

        op = self.opacity if self._use_chroma else 1.0
        img = tk.PhotoImage(width=w, height=h)
        for y in range(h):
            base = h - 1 - y  # 0 at bottom row -> darkest on the left
            thresholds = _BAYER8[y & 7]
            row = [
                lut[x + base] if op > thresholds[x & 7] else CHROMA  # CHROMA = hole
                for x in range(w)
            ]
            img.put("{" + " ".join(row) + "}", to=(0, y))

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
        """Bind global hotkeys to start/reset timers (invalid keys are skipped)."""
        for key, lane in self.bindings.items():
            self._safe_add_hotkey(key, self._start_lane, lane)
        self._safe_add_hotkey(self.config.get("reset_all_key"), self._reset_all)
        self._safe_add_hotkey(self.config.get("quit_key"), self.quit)

    def _safe_add_hotkey(self, key, callback, *args) -> None:
        """Register a hotkey, ignoring blank/invalid keys instead of crashing."""
        if not key or not str(key).strip():
            return
        try:
            keyboard.add_hotkey(key, callback, args=args)
        except Exception as exc:  # keyboard raises ValueError etc. on bad keys
            print(f"[flash-timer] Ignoring invalid hotkey {key!r}: {exc}")

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
        """Start a lane only on a confirming double-press within the window.

        The first press is remembered; a second press of the same key within
        `double_press_window` seconds starts (or restarts) that lane's timer.
        """
        now = time.monotonic()
        if now - self._last_press.get(lane, 0.0) <= self.double_press_window:
            self.timers[lane].start()
            self._last_press[lane] = 0.0  # reset so the next cast needs two presses
        else:
            self._last_press[lane] = now

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
        """Refresh every countdown once per second (keeps CPU near idle)."""
        self._refresh_auto_labels()
        blink_on = int(time.monotonic()) % 2 == 0  # blink phase for expired timers
        for lane in self.timers:
            self._render_row(lane, blink_on)

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
