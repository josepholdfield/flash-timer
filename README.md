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

## Appearance

- A smooth diagonal black gradient sits behind the text — darkest (most
  readable) at the bottom-left, fading softer toward the top-right.
- Overall see-through level is set by `opacity` in `config.json`.

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
