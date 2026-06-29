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

try:
    import keyboard  # global hotkeys (Windows-friendly)
except ImportError:  # pragma: no cover - friendly hint if dependency missing
    print("Missing dependency 'keyboard'. Run: pip install -r requirements.txt")
    sys.exit(1)


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "flash_seconds": 300,
    "always_on_top": True,
    "opacity": 0.85,
    "bindings": {
        "num 7": "Top",
        "num 8": "Jungle",
        "num 9": "Mid",
        "num 4": "Bot",
        "num 5": "Support",
    },
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
    """Tkinter overlay that renders all lane Flash timers."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.duration = int(config["flash_seconds"])
        self.bindings = config["bindings"]

        # One independent timer per lane label.
        self.timers: dict[str, FlashTimer] = {
            lane: FlashTimer(self.duration) for lane in self.bindings.values()
        }

        self.root = tk.Tk()
        self.root.title("Flash Timers")
        self.root.configure(bg="#101015")
        self.root.attributes("-topmost", bool(config["always_on_top"]))
        try:
            self.root.attributes("-alpha", float(config["opacity"]))
        except tk.TclError:
            pass
        self.root.overrideredirect(True)  # borderless floating overlay

        self._build_rows()
        self._make_draggable()
        self._register_hotkeys()

        # Quit on Escape from the window too.
        self.root.bind("<Escape>", lambda _e: self.quit())

    def _build_rows(self) -> None:
        """Create one label row per lane plus a draggable title bar."""
        self.title_bar = tk.Label(
            self.root,
            text="⚡ Flash Timers",
            font=("Consolas", 12, "bold"),
            fg="#9fb4ff",
            bg="#101015",
            anchor="w",
            padx=10,
        )
        self.title_bar.pack(fill="x", pady=(6, 2))

        self.row_labels: dict[str, tk.Label] = {}
        for lane in self.timers:
            label = tk.Label(
                self.root,
                text=f"{lane:<8} -",
                font=("Consolas", 16, "bold"),
                fg="#666a75",
                bg="#101015",
                anchor="w",
                padx=12,
            )
            label.pack(fill="x")
            self.row_labels[lane] = label

        self.hint = tk.Label(
            self.root,
            text="  ".join(f"{k.replace('num ', 'Num')}:{v}" for k, v in self.bindings.items()),
            font=("Consolas", 8),
            fg="#4a4d57",
            bg="#101015",
            padx=10,
        )
        self.hint.pack(fill="x", pady=(2, 6))

    def _make_draggable(self) -> None:
        """Allow dragging the borderless window by its title bar."""
        self._drag = {"x": 0, "y": 0}

        def start(event: tk.Event) -> None:
            self._drag["x"] = event.x
            self._drag["y"] = event.y

        def move(event: tk.Event) -> None:
            x = self.root.winfo_x() + event.x - self._drag["x"]
            y = self.root.winfo_y() + event.y - self._drag["y"]
            self.root.geometry(f"+{x}+{y}")

        self.title_bar.bind("<Button-1>", start)
        self.title_bar.bind("<B1-Motion>", move)

        # Restore saved position if present.
        pos = self.config.get("window_position")
        if pos:
            self.root.geometry(f"+{pos[0]}+{pos[1]}")

    def _register_hotkeys(self) -> None:
        """Bind global hotkeys to start/reset timers."""
        for key, lane in self.bindings.items():
            keyboard.add_hotkey(key, self._start_lane, args=(lane,))
        keyboard.add_hotkey(self.config["reset_all_key"], self._reset_all)
        keyboard.add_hotkey(self.config["quit_key"], self.quit)

    def _start_lane(self, lane: str) -> None:
        self.timers[lane].start()

    def _reset_all(self) -> None:
        for timer in self.timers.values():
            timer.clear()

    @staticmethod
    def _color_for(remaining: float, running: bool) -> str:
        """Color-code the countdown by urgency."""
        if not running:
            return "#666a75"  # idle grey
        if remaining <= 0:
            return "#ff3b3b"  # ready / flashing red
        if remaining < 30:
            return "#ff3b3b"  # red
        if remaining < 60:
            return "#ff9f1c"  # orange
        if remaining < 180:
            return "#ffd23f"  # yellow
        return "#3ad07a"  # green

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
        flash = False
        for lane, timer in self.timers.items():
            text = self._format(timer.remaining, timer.running)
            color = self._color_for(timer.remaining, timer.running)
            self.row_labels[lane].config(text=f"{lane:<8} {text}", fg=color)
            if timer.running and timer.remaining <= 0:
                flash = True

        # Subtle flash of the title bar when any Flash is back up.
        if flash:
            on = int(time.monotonic()) % 2 == 0
            self.title_bar.config(fg="#ff3b3b" if on else "#9fb4ff")
        else:
            self.title_bar.config(fg="#9fb4ff")

        self.root.after(1000, self._tick)

    def quit(self) -> None:
        """Save window position and close."""
        try:
            self.config["window_position"] = [self.root.winfo_x(), self.root.winfo_y()]
            save_config(self.config)
        except tk.TclError:
            pass
        keyboard.clear_all_hotkeys()
        self.root.destroy()

    def run(self) -> None:
        self._tick()
        self.root.mainloop()


def main() -> None:
    config = load_config()
    FlashOverlay(config).run()


if __name__ == "__main__":
    main()
