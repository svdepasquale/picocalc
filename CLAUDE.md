# CLAUDE.md — PicoCalc Toolkit

## What this project is

A modular MicroPython toolkit for the **PicoCalc** hardware (Raspberry Pi Pico 2W). It provides Wi-Fi management, AI chat (OpenRouter), RSS news, clock/NTP, notes/todo, weather, scientific calculator, MP3 player, and synthesizer — all optimized for a 320x320 display with a tiny keyboard.

## Target hardware

- **Board:** Raspberry Pi Pico 2W (RP2350, wireless)
- **Enclosure:** PicoCalc (built-in screen + keyboard + speaker)
- **Display:** 320x320, usable area ~32 characters wide, ~8 lines per page
- **Audio:** Built-in speaker/buzzer on GPIO 22 (PWM), or external I2S DAC (SCK=GP16, WS=GP17, SD=GP28)
- **RAM:** ~260 KB available — memory is a hard constraint

## MicroPython constraints

This is **not** standard CPython. Key differences:

- Use `ujson` (falls back to `json` via try/except pattern)
- No `pip` — dependencies are installed via `mip` on the device (`import mip; mip.install('urequests')`)
- External dependencies: `urequests`, `ntptime` (installed via mip, not bundled)
- No threading, no async/await in most modules
- `gc.collect()` is called explicitly to manage memory pressure
- `input()` has no hidden mode (passwords are visible)
- `ticks_ms` overflows after ~12-25 days

## File structure

All `.py` files **must** stay in the root directory — MicroPython on the Pico imports from root only. Do not create subdirectories for modules.

| File | Purpose | Network? | Hardware? |
|------|---------|----------|-----------|
| `pico_utils.py` | Shared display/navigation/IO utilities | No | No |
| `wifi_manager.py` | Wi-Fi connect + interactive setup | Yes | No |
| `openrouter_ai.py` | OpenRouter API client + chat | Yes | No |
| `rss_news.py` | RSS reader with preview-first UX | Yes | No |
| `clock_ntp.py` | NTP sync, local time, timer, countdown | Yes | No |
| `notes.py` | Persistent notes/todo (JSON on flash) | No | No |
| `weather.py` | Open-Meteo weather + forecast | Yes | No |
| `scientific_calc.py` | Trig, log, conversions, history | No | No |
| `mp3_player.py` | WAV audio player via I2S | No | Yes (I2S DAC) |
| `synthesizer.py` | Tone/note synthesizer (I2S or PWM) | No | Yes (speaker/DAC) |

## Coding conventions

- **Naming:** `snake_case` for all public functions. Internal/private functions prefixed with `_underscore`.
- **Short aliases:** Every module exposes short aliases for REPL use (e.g., `view()` → `v()`, `latest()` → `l()`). Always define both.
- **Module version:** Each file has a `MODULE_VERSION` constant (or variant like `WIFI_MANAGER_VERSION`). Format: `"YYYY-MM-DD.N"` (e.g., `"2026-03-28.2"`).
- **Standard methods:** Every module must implement `ver()`, `help()`, and `h()` (short alias for help).
- **Display constants:** `DISPLAY_WIDTH = 32`, `PAGE_LINES = 8`. All output must fit within 32 chars to avoid wrapping on the PicoCalc screen.
- **Section headers in code:** Use `# ──` divider comments to separate logical sections.
- **Import pattern for utils:** `from pico_utils import func as _func` (underscore prefix to keep module namespace clean).
- **ujson fallback:** Always use `try: import ujson as json / except: import json`.

## What NOT to do

- Do not use libraries from PyPI — only MicroPython builtins and `mip`-installable packages
- Do not use `async`/`await` unless the module is specifically designed for it
- Do not create output strings longer than ~1400 chars without paging (`_paged_print`)
- Do not allocate large buffers (>4 KB) without calling `gc.collect()` first
- Do not add `__init__.py` or package directories — flat root structure only
- Do not use f-strings (not all MicroPython builds support them reliably)
- Do not hardcode Wi-Fi credentials, API keys, or locations in source code — use JSON config files on flash

## Config files (created at runtime on device)

These files are stored on the Pico's flash filesystem, not in the repo:

- `/wifi_credentials.json` — saved Wi-Fi networks
- `/openrouter_config.json` — AI key, model, system prompt
- `/rss_feeds.json` — RSS feed list + display settings
- `/clock_config.json` — UTC offset
- `/notes_data.json` — saved notes/todo items
- `/weather_config.json` — location (lat, lon, name)

## Testing

There is no test suite yet. To verify changes:

1. Check syntax with standard Python: `python3 -c "import py_compile; py_compile.compile('module.py')"`
2. Verify output fits 32-char width by visual inspection
3. On-device testing via serial REPL is the primary validation method

## Build and deploy

No build step. Copy all `.py` files to the Pico root via USB (Thonny, `mpremote`, or MicroPico VS Code extension). The device runs files directly from flash.
