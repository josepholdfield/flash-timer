# flash-timer

A lightweight, always-on-top **Flash cooldown overlay** for League of Legends.

Press a hotkey the moment an enemy uses Flash and a 300s (5:00) countdown starts
for that lane. Each lane runs independently and is colour-coded by urgency.

## Run

```bat
run.bat
```

`run.bat` installs the one dependency (`keyboard`) and starts the overlay.
Requires Python 3.12+.

## Hotkeys

The overlay ships with **no keys bound** by default. Open the settings panel
(see below) to bind keys, or click **Reset keys to default** to apply the
classic numpad layout:

| Key    | Lane    |
| ------ | ------- |
| Num 7  | Top     |
| Num 8  | Jungle  |
| Num 9  | Mid     |
| Num 4  | Bot     |
| Num 5  | Support |
| Num 0  | Reset all |
| Esc    | Quit    |

Press a lane key again to restart that timer back to full duration.
Hold **Tab** to reveal the settings gear and close (x) icons, plus the
key-binding hints. The hints only appear for lanes that actually have a key
bound (nothing is shown when no keys are bound).

You can also **click a champion name** (or the area around it) to start its
timer, and click the **reset** icon after the countdown to clear it.

**Hold Tab to drag** the overlay from anywhere on its surface, and click the
**x** icon (only shown while Tab is held) to close it.

> **Anti-misfire (optional):** enable *double-press keybinds* and/or
> *double-click name / reset* in the settings panel so a timer only starts on a
> confirming second press/click. The keyboard window is tuned by
> `double_press_seconds` in `config.json`.

## Settings panel

Hold **Tab** and click the small **gear** on the hint line to open the settings
window. It is a clean, borderless panel (no OS title bar) that matches the
overlay's dark styling — drag it by its header, and close it with its own
**Close** button. It lets you:

- **Set the Flash cooldown** (seconds).
- **Drag an opacity slider** that updates the overlay live.
- **Drag a window-size slider** that scales the text and window proportionally.
- Toggle **double-press keybinds** and **double-click name / reset**.
- Toggle **always on top** and **auto-detect champion names**.
- **Rebind any key** — click a key, then press the new key (Esc cancels,
  Backspace/Delete unbinds). Unbound keys are flagged in **red**.
- **Reset keys to default** to restore the numpad layout above.

Changes apply immediately and are saved to `config.json`.

## Appearance

- No title text — just the timers on a smooth diagonal black gradient that is
  darkest (most readable) at the bottom-left and softer toward the top-right.
- `opacity` (0–1) sets how see-through the background is (**defaults to 0**).
  On Windows the background uses a transparent colour key, so even at
  `opacity: 0` the background is completely clear while the **text stays fully
  visible**.
- `ui_scale` scales the text and window size proportionally (adjust it live via
  the settings panel's window-size slider).

## Colours

- **Green** — more than 3:00 left
- **Yellow** — 3:00 to 1:00
- **Orange** — 1:00 to 0:30
- **Red** — under 0:30 / Flash is up (`UP`)

## Configuration

Everything lives in `config.json` — no need to touch the code:

- `flash_seconds` — cooldown length (use `240` for Cosmic Insight)
- `always_on_top` / `opacity` — overlay behaviour (`opacity` defaults to `0`)
- `ui_scale` — overlay text/window size multiplier
- `corner` — start position (`bottom-left`, `top-left`, `top-right`, `bottom-right`)
- `roles` — the lanes shown (defaults to Top/Jungle/Mid/Bot/Support)
- `bindings` — lane → key (empty string means unbound)
- `double_press_keys` / `double_click_mouse` / `double_press_seconds` — anti-misfire
- `champions` / `auto_champions` / `track_team` — champion-name labels
- `reset_all_key`, `quit_key`

Drag the overlay by **holding Tab** and dragging anywhere on its surface; its
position is saved on exit.

> Global hotkeys via the `keyboard` library work best on Windows and may
> require running as administrator.
