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

## Hotkeys (default)

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
Hold **Tab** to reveal the key-binding hints (hidden otherwise).

> **Double-press to confirm:** a lane only starts when you press its key
> **twice within 0.5s** (avoids accidental single taps). Tune the window with
> `double_press_seconds` in `config.json`.

## Appearance

- No title text — just the timers on a smooth diagonal black gradient that is
  darkest (most readable) at the bottom-left and softer toward the top-right.
- `opacity` (0–1) sets how see-through the background is. On Windows the
  background uses a transparent colour key, so even at `opacity: 0` the
  background is completely clear while the **text stays fully visible**.

## Colours

- **Green** — more than 3:00 left
- **Yellow** — 3:00 to 1:00
- **Orange** — 1:00 to 0:30
- **Red** — under 0:30 / Flash is up (`UP`)

## Configuration

Everything lives in `config.json` — no need to touch the code:

- `flash_seconds` — cooldown length (use `240` for Cosmic Insight)
- `always_on_top` / `opacity` — overlay behaviour
- `corner` — start position (`bottom-left`, `top-left`, `top-right`, `bottom-right`)
- `bindings` — hotkey → lane label
- `champions` / `auto_champions` / `track_team` — champion-name labels
- `reset_all_key`, `quit_key`

Drag the overlay anywhere on its surface; its position is saved on exit.

> Global hotkeys via the `keyboard` library work best on Windows and may
> require running as administrator.
