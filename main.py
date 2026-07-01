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
import threading
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

# Settings panel palette (kept in keeping with the overlay's dark styling).
COLOR_PANEL_BG = "#12141b"
COLOR_PANEL_CARD = "#181b24"
COLOR_PANEL_FG = "#c4cad6"
COLOR_PANEL_SUB = "#8b93a7"
COLOR_FIELD_BG = "#1c2029"
COLOR_FIELD_ACTIVE = "#2a3140"
COLOR_ACCENT = "#5c9dff"
COLOR_UNBOUND = COLOR_RED  # unbound keys are flagged in red

# The fixed set of lanes/roles the overlay always shows (independent of which
# keys, if any, are bound to them).
DEFAULT_ROLES = ["Top", "Jungle", "Mid", "Bot", "Support"]
# Sensible keybinds applied by the settings panel's "reset keys" button.
DEFAULT_KEYBINDS = {
    "Top": "num 7",
    "Jungle": "num 8",
    "Mid": "num 9",
    "Bot": "num 4",
    "Support": "num 5",
}

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
    "roles": list(DEFAULT_ROLES),
    # role -> key. Empty string means the role has no key bound.
    "bindings": {role: "" for role in DEFAULT_ROLES},
    "champions": {
        "Top": "",
        "Jungle": "",
        "Mid": "",
        "Bot": "",
        "Support": "",
    },
    "auto_champions": True,
    "track_team": "enemy",
    "double_press_keys": False,   # require a double key-press to start a timer
    "double_press_seconds": 0.5,
    "double_click_mouse": False,  # require a double mouse-click for start/reset
    "reset_all_key": "",
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
    GEAR_R = 8          # settings gear (shown on the Tab hint line) radius
    CLOSE_R = 8         # close (x) icon (shown on the Tab hint line) radius

    def __init__(self, config: dict) -> None:
        self.config = config
        self.duration = int(config["flash_seconds"])

        # The lanes are fixed; key bindings are optional and layered on top so
        # the overlay still works (and can be rebound) with no keys bound.
        self.roles: list[str] = list(config.get("roles") or DEFAULT_ROLES)
        self.bindings: dict[str, str] = self._normalise_bindings(
            config.get("bindings", {})
        )
        config["bindings"] = self.bindings  # keep the config in sync

        # Display label per role: champion name if set, otherwise the role.
        champions = config.get("champions", {})
        self.labels: dict[str, str] = {
            role: (champions.get(role) or role) for role in self.roles
        }

        # Optional: auto-fill champion names from League's Live Client API.
        self.watcher: ChampionWatcher | None = None
        if config.get("auto_champions", True):
            self.watcher = ChampionWatcher(track=config.get("track_team", "enemy"))
            self.watcher.start()

        # One independent timer per lane.
        self.timers: dict[str, FlashTimer] = {
            role: FlashTimer(self.duration) for role in self.roles
        }

        # Anti-misfire options (both off by default).
        self.double_press_keys = bool(config.get("double_press_keys", False))
        self.double_click_mouse = bool(config.get("double_click_mouse", False))
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
        self._opacity_job: str | None = None  # debounce handle for live opacity
        self.settings_win: "SettingsWindow | None" = None

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

    def _normalise_bindings(self, raw: dict) -> dict[str, str]:
        """Return a role -> key map, tolerating the legacy key -> role schema."""
        bindings = {role: "" for role in self.roles}
        for a, b in (raw or {}).items():
            if a in bindings:            # new schema: role -> key
                bindings[a] = str(b or "")
            elif b in bindings:          # legacy schema: key -> role
                bindings[b] = str(a or "")
        return bindings

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
            self.canvas.tag_bind(name_id, "<Button-1>", lambda _e, l=lane: self._on_name_click(l, False))
            self.canvas.tag_bind(name_id, "<Double-Button-1>", lambda _e, l=lane: self._on_name_click(l, True))
            self.canvas.tag_bind(name_id, "<Enter>", lambda _e, l=lane: self._on_name_enter(l))
            self.canvas.tag_bind(name_id, "<Leave>", lambda _e, l=lane: self._on_name_leave(l))

        self.hint_item = self.canvas.create_text(
            0, 0, anchor="nw", text=self._hint_text(), font=self.hint_font,
            fill=COLOR_HINT, state="hidden",
        )
        # Settings gear lives on the hint line and is only shown while Tab is
        # held (drawn/positioned in _relayout).
        self._gear_bound = False
        self._close_bound = False

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

        self.canvas.tag_bind(tag, "<Button-1>", lambda _e, l=lane: self._on_reset_click(l, False))
        self.canvas.tag_bind(tag, "<Double-Button-1>", lambda _e, l=lane: self._on_reset_click(l, True))
        self.canvas.tag_bind(tag, "<Enter>", lambda _e, l=lane: self._on_icon_enter(l))
        self.canvas.tag_bind(tag, "<Leave>", lambda _e, l=lane: self._on_icon_leave(l))

    # ----- Settings gear ---------------------------------------------------

    def _hint_text(self) -> str:
        """Build the Tab-only hint line: `Role:Key` (unbound shown as `--`)."""
        parts = []
        for role in self.roles:
            key = self.bindings.get(role, "")
            disp = key.replace("num ", "Num") if key else "--"
            parts.append(f"{role}:{disp}")
        return "  ".join(parts)

    def _draw_gear(self, cx: float, cy: float) -> None:
        """Draw the settings cog (hidden until Tab is held) on the hint line."""
        self.canvas.delete("gear")
        r = self.GEAR_R
        vis = "normal" if self._tab_held else "hidden"

        # Circular hit area (also acts as the cog's backing plate).
        self.canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=COLOR_ICON_BG, outline="", state=vis,
            tags=("gear", "gear-bg"),
        )
        # Cog silhouette: alternate outer/inner radius to form the teeth.
        teeth = 8
        r_out, r_in = r - 1.0, r - 3.5
        pts: list[float] = []
        for i in range(teeth * 2):
            ang = math.pi * i / teeth
            rad = r_out if i % 2 == 0 else r_in
            pts += [cx + rad * math.cos(ang), cy + rad * math.sin(ang)]
        self.canvas.create_polygon(
            *pts, fill=COLOR_ICON, outline="", state=vis,
            tags=("gear", "gear-fg"),
        )
        # Centre hole (follows the backing plate colour) to read as a cog.
        hr = r * 0.34
        self.canvas.create_oval(
            cx - hr, cy - hr, cx + hr, cy + hr,
            fill=COLOR_ICON_BG, outline="", state=vis,
            tags=("gear", "gear-bg"),
        )

        if not self._gear_bound:
            self.canvas.tag_bind("gear", "<Button-1>", lambda _e: self._open_settings())
            self.canvas.tag_bind("gear", "<Enter>", lambda _e: self._on_gear_enter())
            self.canvas.tag_bind("gear", "<Leave>", lambda _e: self._on_gear_leave())
            self._gear_bound = True

    def _on_gear_enter(self) -> None:
        self.canvas.itemconfig("gear-bg", fill=COLOR_ICON_BG_HOVER)
        self.canvas.itemconfig("gear-fg", fill=COLOR_ICON_HOVER)
        self.canvas.configure(cursor="hand2")

    def _on_gear_leave(self) -> None:
        self.canvas.itemconfig("gear-bg", fill=COLOR_ICON_BG)
        self.canvas.itemconfig("gear-fg", fill=COLOR_ICON)
        self.canvas.configure(cursor="")

    def _open_settings(self) -> None:
        """Open (or focus) the settings window."""
        if self.settings_win is not None and self.settings_win.is_alive():
            self.settings_win.focus()
            return
        self.settings_win = SettingsWindow(self)

    def _draw_close(self, cx: float, cy: float) -> None:
        """Draw the close (x) icon (hidden until Tab is held) on the hint line."""
        self.canvas.delete("close")
        r = self.CLOSE_R
        vis = "normal" if self._tab_held else "hidden"

        # Circular hit area, matching the reset/gear icons.
        self.canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=COLOR_ICON_BG, outline="", state=vis,
            tags=("close", "close-bg"),
        )
        d = r - 4
        self.canvas.create_line(
            cx - d, cy - d, cx + d, cy + d, fill=COLOR_ICON, width=2,
            capstyle="round", state=vis, tags=("close", "close-fg"),
        )
        self.canvas.create_line(
            cx - d, cy + d, cx + d, cy - d, fill=COLOR_ICON, width=2,
            capstyle="round", state=vis, tags=("close", "close-fg"),
        )

        if not self._close_bound:
            self.canvas.tag_bind("close", "<Button-1>", lambda _e: self.quit())
            self.canvas.tag_bind("close", "<Enter>", lambda _e: self._on_close_enter())
            self.canvas.tag_bind("close", "<Leave>", lambda _e: self._on_close_leave())
            self._close_bound = True

    def _on_close_enter(self) -> None:
        self.canvas.itemconfig("close-bg", fill=COLOR_ICON_BG_HOVER)
        self.canvas.itemconfig("close-fg", fill=COLOR_RED)
        self.canvas.configure(cursor="hand2")

    def _on_close_leave(self) -> None:
        self.canvas.itemconfig("close-bg", fill=COLOR_ICON_BG)
        self.canvas.itemconfig("close-fg", fill=COLOR_ICON)
        self.canvas.configure(cursor="")

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

    def _on_name_click(self, lane: str, double: bool = False) -> None:
        """Start (or restart) a lane's timer and flash the name as feedback."""
        if self.double_click_mouse and not double:
            return  # anti-misclick: only a double-click counts
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

    def _on_reset_click(self, lane: str, double: bool = False) -> None:
        """Reset a lane fully back to the unknown (\"-\") state."""
        if self.double_click_mouse and not double:
            return  # anti-misclick: only a double-click counts
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
        # The hint line also carries the close (x) and settings (gear) icons.
        self.canvas.itemconfig(self.hint_item, text=self._hint_text())
        gear_space = (
            self.GAP_TIME_ICON + 2 * self.GEAR_R + 8 + 2 * self.CLOSE_R
        )
        hint_w = (
            self.hint_font.measure(self.canvas.itemcget(self.hint_item, "text"))
            + 2 * self.PAD_X + gear_space
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
        icon_cy = y + hint_h / 2
        gear_cx = width - self.PAD_X - self.GEAR_R
        self._draw_gear(gear_cx, icon_cy)
        close_cx = gear_cx - self.GEAR_R - 8 - self.CLOSE_R
        self._draw_close(close_cx, icon_cy)
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
        for role, key in self.bindings.items():
            self._safe_add_hotkey(key, self._start_lane, role)
        self._safe_add_hotkey(self.config.get("reset_all_key"), self._reset_all)
        self._safe_add_hotkey(self.config.get("quit_key"), self.quit)

    def _reregister_hotkeys(self) -> None:
        """Re-apply all global hooks after a rebind (keeps Tab tracking alive)."""
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        self._register_hotkeys()
        self._install_tab_hooks()

    def _safe_add_hotkey(self, key, callback, *args) -> None:
        """Register a hotkey, ignoring blank/invalid keys instead of crashing."""
        if not key or not str(key).strip():
            return
        try:
            keyboard.add_hotkey(key, callback, args=args)
        except Exception as exc:  # keyboard raises ValueError etc. on bad keys
            print(f"[flash-timer] Ignoring invalid hotkey {key!r}: {exc}")

    def _install_tab_hooks(self) -> None:
        """(Re)install the global Tab press/release hooks."""
        keyboard.on_press_key("tab", lambda _e: setattr(self, "_tab_held", True))
        keyboard.on_release_key("tab", lambda _e: setattr(self, "_tab_held", False))

    def _watch_tab(self) -> None:
        """Track Tab held state globally and poll it to toggle the hint."""
        self._install_tab_hooks()
        self._poll_hint()

    def _poll_hint(self) -> None:
        """Show the key-binding hint and icons only while Tab is held."""
        state = "normal" if self._tab_held else "hidden"
        if self.canvas.itemcget(self.hint_item, "state") != state:
            self.canvas.itemconfig(self.hint_item, state=state)
            self.canvas.itemconfig("gear", state=state)
            self.canvas.itemconfig("close", state=state)
        self.root.after(120, self._poll_hint)

    def _start_lane(self, lane: str) -> None:
        """Start a lane's timer, optionally requiring a confirming double-press.

        When double-press is enabled the first press is remembered; a second
        press of the same key within `double_press_window` seconds starts it.
        """
        if not self.double_press_keys:
            self.timers[lane].start()
            return
        now = time.monotonic()
        if now - self._last_press.get(lane, 0.0) <= self.double_press_window:
            self.timers[lane].start()
            self._last_press[lane] = 0.0  # reset so the next cast needs two presses
        else:
            self._last_press[lane] = now

    # ----- Settings-panel callbacks ---------------------------------------

    def set_flash_seconds(self, seconds: int) -> None:
        self.duration = int(seconds)
        for timer in self.timers.values():
            timer.duration = self.duration
        self.config["flash_seconds"] = self.duration
        save_config(self.config)

    def set_opacity(self, value: float, *, persist: bool = False) -> None:
        """Live-update overlay opacity while a slider is dragged."""
        self.opacity = max(0.0, min(1.0, float(value)))
        self.config["opacity"] = self.opacity
        if self._use_chroma:
            # Debounce the (relatively costly) gradient redraw during a drag.
            if self._opacity_job is not None:
                self.root.after_cancel(self._opacity_job)
            self._opacity_job = self.root.after(
                40, lambda: self._draw_gradient(self._win_w, self._win_h)
            )
        else:
            try:
                self.root.attributes("-alpha", max(0.2, self.opacity))
            except tk.TclError:
                pass
        if persist:
            save_config(self.config)

    def set_double_press_keys(self, enabled: bool) -> None:
        self.double_press_keys = bool(enabled)
        self._last_press.clear()
        self.config["double_press_keys"] = self.double_press_keys
        save_config(self.config)

    def set_double_click_mouse(self, enabled: bool) -> None:
        self.double_click_mouse = bool(enabled)
        self.config["double_click_mouse"] = self.double_click_mouse
        save_config(self.config)

    def set_always_on_top(self, enabled: bool) -> None:
        self.config["always_on_top"] = bool(enabled)
        try:
            self.root.attributes("-topmost", bool(enabled))
        except tk.TclError:
            pass
        save_config(self.config)

    def set_auto_champions(self, enabled: bool) -> None:
        self.config["auto_champions"] = bool(enabled)
        if enabled and self.watcher is None:
            self.watcher = ChampionWatcher(track=self.config.get("track_team", "enemy"))
            self.watcher.start()
        elif not enabled and self.watcher is not None:
            self.watcher.stop()
            self.watcher = None
            # Fall back to plain role labels.
            for role in self.roles:
                self.labels[role] = role
            self._relayout()
        save_config(self.config)

    def set_binding(self, ident: str, key: str) -> None:
        """Rebind a role (or the special `reset_all`/`quit` keys)."""
        key = (key or "").strip()
        if ident in self.bindings:
            self.bindings[ident] = key
            self.config["bindings"] = self.bindings
        elif ident in ("reset_all_key", "quit_key"):
            self.config[ident] = key
        self._reregister_hotkeys()
        self.canvas.itemconfig(self.hint_item, text=self._hint_text())
        save_config(self.config)

    def reset_bindings_to_default(self) -> None:
        """Restore the built-in numpad keybinds (used by the settings button)."""
        for role in self.roles:
            self.bindings[role] = DEFAULT_KEYBINDS.get(role, "")
        self.config["bindings"] = self.bindings
        self.config["reset_all_key"] = "num 0"
        self.config["quit_key"] = "esc"
        self._reregister_hotkeys()
        self.canvas.itemconfig(self.hint_item, text=self._hint_text())
        save_config(self.config)

    def binding_for(self, ident: str) -> str:
        if ident in self.bindings:
            return self.bindings.get(ident, "")
        return str(self.config.get(ident, "") or "")

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


class SettingsWindow:
    """Dark, Consolas-styled settings panel opened from the Tab-only gear."""

    # Rebindable actions shown in the keybinds section: (identifier, label).
    _KEY_ROWS_EXTRA = [("reset_all_key", "Reset All"), ("quit_key", "Quit")]

    def __init__(self, overlay: "FlashOverlay") -> None:
        self.overlay = overlay
        self._capturing = False
        self._key_buttons: dict[str, tk.Button] = {}

        self.win = tk.Toplevel(overlay.root)
        self.win.title("Flash Timer - Settings")
        self.win.configure(bg=COLOR_PANEL_BG)
        self.win.resizable(False, False)
        try:
            self.win.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        self._build()

        # Open next to the overlay.
        ox = overlay.root.winfo_x()
        oy = overlay.root.winfo_y()
        self.win.geometry(f"+{ox + 24}+{max(24, oy - 60)}")

    # ----- lifecycle -------------------------------------------------------

    def is_alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def focus(self) -> None:
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except tk.TclError:
            pass

    def close(self) -> None:
        save_config(self.overlay.config)
        self.overlay.settings_win = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    # ----- styled widget helpers ------------------------------------------

    def _heading(self, parent: tk.Widget, text: str) -> None:
        tk.Label(
            parent, text=text.upper(), bg=COLOR_PANEL_BG, fg=COLOR_PANEL_SUB,
            font=("Consolas", 9, "bold"), anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 4))

    def _card(self, parent: tk.Widget) -> tk.Frame:
        frame = tk.Frame(parent, bg=COLOR_PANEL_CARD)
        frame.pack(fill="x", padx=14, pady=2)
        return frame

    def _row_label(self, parent: tk.Widget, text: str) -> tk.Label:
        return tk.Label(
            parent, text=text, bg=COLOR_PANEL_CARD, fg=COLOR_PANEL_FG,
            font=("Consolas", 10), anchor="w",
        )

    def _make_check(self, parent: tk.Widget, text: str, initial: bool, command) -> None:
        var = tk.BooleanVar(value=bool(initial))

        def toggled() -> None:
            command(var.get())

        chk = tk.Checkbutton(
            parent, text="  " + text, variable=var, command=toggled,
            bg=COLOR_PANEL_CARD, fg=COLOR_PANEL_FG, selectcolor=COLOR_FIELD_BG,
            activebackground=COLOR_PANEL_CARD, activeforeground=COLOR_ACCENT,
            font=("Consolas", 10), anchor="w", bd=0, highlightthickness=0,
            takefocus=0, cursor="hand2",
        )
        chk.pack(fill="x", padx=12, pady=5)

    def _flat_button(self, parent: tk.Widget, text: str, command, **kw) -> tk.Button:
        opts = dict(
            bg=COLOR_FIELD_BG, fg=COLOR_PANEL_FG, activebackground=COLOR_FIELD_ACTIVE,
            activeforeground=COLOR_ACCENT, font=("Consolas", 10), bd=0,
            highlightthickness=0, relief="flat", cursor="hand2",
            padx=10, pady=4, command=command,
        )
        opts.update(kw)
        return tk.Button(parent, text=text, **opts)

    # ----- construction ----------------------------------------------------

    def _build(self) -> None:
        ov = self.overlay
        header = tk.Label(
            self.win, text="\u2699  Settings", bg=COLOR_PANEL_BG, fg=COLOR_PANEL_FG,
            font=("Consolas", 13, "bold"), anchor="w",
        )
        header.pack(fill="x", padx=16, pady=(14, 2))

        # --- General ---
        self._heading(self.win, "General")
        card = self._card(self.win)

        flash_row = tk.Frame(card, bg=COLOR_PANEL_CARD)
        flash_row.pack(fill="x", padx=12, pady=6)
        self._row_label(flash_row, "Flash cooldown (s)").pack(side="left")
        self.flash_var = tk.IntVar(value=int(ov.duration))
        spin = tk.Spinbox(
            flash_row, from_=15, to=1200, increment=5, width=6,
            textvariable=self.flash_var, command=self._on_flash,
            bg=COLOR_FIELD_BG, fg=COLOR_PANEL_FG, buttonbackground=COLOR_FIELD_BG,
            insertbackground=COLOR_PANEL_FG, font=("Consolas", 10), bd=0,
            highlightthickness=1, highlightbackground=COLOR_FIELD_ACTIVE,
            justify="right",
        )
        spin.pack(side="right")
        spin.bind("<Return>", lambda _e: self._on_flash())
        spin.bind("<FocusOut>", lambda _e: self._on_flash())

        op_row = tk.Frame(card, bg=COLOR_PANEL_CARD)
        op_row.pack(fill="x", padx=12, pady=(0, 8))
        self._row_label(op_row, "Opacity").pack(side="left")
        self.opacity_pct = tk.Label(
            op_row, text=f"{int(ov.opacity * 100)}%", bg=COLOR_PANEL_CARD,
            fg=COLOR_ACCENT, font=("Consolas", 10), width=5, anchor="e",
        )
        self.opacity_pct.pack(side="right")
        opacity_scale = tk.Scale(
            card, from_=0.0, to=1.0, resolution=0.01, orient="horizontal",
            showvalue=False, command=self._on_opacity, bg=COLOR_PANEL_CARD,
            fg=COLOR_PANEL_FG, troughcolor=COLOR_FIELD_BG, highlightthickness=0,
            bd=0, sliderrelief="flat", activebackground=COLOR_ACCENT, length=240,
        )
        opacity_scale.set(ov.opacity)
        opacity_scale.pack(fill="x", padx=12, pady=(0, 10))

        # --- Behaviour ---
        self._heading(self.win, "Behaviour")
        card = self._card(self.win)
        self._make_check(
            card, "Double-press keybinds (anti-misfire)",
            ov.double_press_keys, ov.set_double_press_keys,
        )
        self._make_check(
            card, "Double-click name / reset (anti-misclick)",
            ov.double_click_mouse, ov.set_double_click_mouse,
        )
        self._make_check(
            card, "Always on top", ov.config.get("always_on_top", True),
            ov.set_always_on_top,
        )
        self._make_check(
            card, "Auto-detect champion names", ov.config.get("auto_champions", True),
            ov.set_auto_champions,
        )

        # --- Keybinds ---
        self._heading(self.win, "Keybinds")
        card = self._card(self.win)
        rows = [(role, role) for role in ov.roles] + self._KEY_ROWS_EXTRA
        for ident, label in rows:
            row = tk.Frame(card, bg=COLOR_PANEL_CARD)
            row.pack(fill="x", padx=12, pady=3)
            self._row_label(row, label).pack(side="left")
            clear_btn = self._flat_button(
                row, "clear", lambda i=ident: self._set_key(i, ""), padx=6,
            )
            clear_btn.pack(side="right", padx=(6, 0))
            key_btn = self._flat_button(
                row, "", lambda i=ident: self._capture(i), width=10,
            )
            key_btn.pack(side="right")
            self._key_buttons[ident] = key_btn
        self._refresh_keys()

        tk.Label(
            card, text="Click a key to rebind, then press the new key (Esc cancels).",
            bg=COLOR_PANEL_CARD, fg=COLOR_PANEL_SUB, font=("Consolas", 8),
            anchor="w", justify="left",
        ).pack(fill="x", padx=12, pady=(2, 6))

        reset_bar = tk.Frame(self.win, bg=COLOR_PANEL_BG)
        reset_bar.pack(fill="x", padx=14, pady=(4, 2))
        self._flat_button(
            reset_bar, "Reset keys to default", self._reset_keys,
        ).pack(side="left")

        # --- Footer ---
        footer = tk.Frame(self.win, bg=COLOR_PANEL_BG)
        footer.pack(fill="x", padx=14, pady=(10, 14))
        self._flat_button(
            footer, "Close", self.close,
            bg=COLOR_ACCENT, fg="#0b0e14", activebackground="#7ab0ff",
            padx=18,
        ).pack(side="right")

    # ----- callbacks -------------------------------------------------------

    def _on_flash(self) -> None:
        try:
            value = int(self.flash_var.get())
        except (tk.TclError, ValueError):
            return
        value = max(1, value)
        self.overlay.set_flash_seconds(value)

    def _on_opacity(self, value: str) -> None:
        try:
            val = float(value)
        except ValueError:
            return
        self.overlay.set_opacity(val)
        try:
            self.opacity_pct.config(text=f"{int(val * 100)}%")
        except tk.TclError:
            pass

    def _reset_keys(self) -> None:
        self.overlay.reset_bindings_to_default()
        self._refresh_keys()

    def _set_key(self, ident: str, key: str) -> None:
        self.overlay.set_binding(ident, key)
        self._refresh_keys()

    def _refresh_keys(self) -> None:
        """Update every key button's label/colour (unbound flagged in red)."""
        for ident, button in self._key_buttons.items():
            key = self.overlay.binding_for(ident)
            if key:
                button.config(text=key, fg=COLOR_PANEL_FG)
            else:
                button.config(text="unbound", fg=COLOR_UNBOUND)

    def _capture(self, ident: str) -> None:
        """Capture the next key press (in a worker thread) and bind it."""
        if self._capturing:
            return
        self._capturing = True
        button = self._key_buttons[ident]
        button.config(text="press a key...", fg=COLOR_ACCENT)

        def worker() -> None:
            name = None
            try:
                event = keyboard.read_event(suppress=False)
                while event.event_type != "down":
                    event = keyboard.read_event(suppress=False)
                name = event.name
            except Exception:
                name = None
            self.win.after(0, lambda: self._finish_capture(ident, name))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_capture(self, ident: str, name) -> None:
        self._capturing = False
        if not self.is_alive():
            return
        if name and name != "esc":
            if name in ("backspace", "delete"):
                name = ""  # explicit unbind
            self.overlay.set_binding(ident, name)
        self._refresh_keys()


def main() -> None:
    config = load_config()
    FlashOverlay(config).run()


if __name__ == "__main__":
    main()
