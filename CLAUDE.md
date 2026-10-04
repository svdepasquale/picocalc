# CLAUDE.md — PicoCalc Toolkit

## What this project is

A modular MicroPython toolkit for the **PicoCalc** hardware (Raspberry Pi Pico 2W). It provides Wi-Fi management, AI chat (OpenRouter), RSS news, clock/NTP, notes/todo, weather, scientific calculator, MP3 player, and synthesizer — all optimized for a 320x320 display with a tiny keyboard.

## Target hardware

- **Board:** Raspberry Pi Pico 2W (RP2350, wireless). The kit ships with a Pico H (RP2040); this toolkit targets the 2W.
- **Enclosure:** PicoCalc v2.0 mainboard (keyboard MCU, SD slot, 2 speakers + 3.5 mm jack, 8 MB PSRAM that the stock `RPI_PICO2_W` MicroPython build does not enable)
- **Display:** 4" IPS 320x320 (ILI9488, SPI1); usable area ~32 characters wide, ~8 lines per page
- **Audio:** Built-in speakers on PWM GP26 (L) + GP27 (R), the pins the official `boot.py` uses (other ClockworkPi sources put L on GP28). GP22 is the SD card-detect line, not audio. An external I2S DAC (SCK=GP16, WS=GP17, SD=GP28) collides with the SD card, which sits on SPI0 GP16-19.
- **RAM:** RP2350 has 520 KB SRAM; the MicroPython heap is what `gc.mem_free()` reports, minus the official driver's ~50 KB framebuffer (320x320 at 4 bpp). Memory is still a hard constraint

## Official firmware facts

ClockworkPi ships MicroPython built on [PicoCalc-micropython-driver](https://github.com/zenodante/PicoCalc-micropython-driver):

- `boot.py` (on the filesystem) starts display, keyboard, SD (`/sd`) and speakers, then `os.dupterm()`s the screen terminal. Never delete it.
- The terminal is 53x40 characters (6x8 font). It draws each character as the CP437 glyph of its code: no Unicode, so `à` shows as `α`.
- Arrow keys reach stdin as VT100 sequences (`\x1b[A`...), which `input()`'s line editor consumes (history/cursor).
- Battery: I2C reg 0x0B of the keyboard MCU (address 0x1F) returns 2 bytes; byte 1 = percent, bit 7 = charging. The official C example waits 16 ms between the register write and the read; the driver's `picocalc.keyboard.battery()` does not.

## MicroPython constraints

This is **not** standard CPython. Key differences:

- Use `ujson` (falls back to `json` via try/except pattern)
- No `pip` — dependencies are installed via `mip` on the device (`import mip; mip.install('urequests')`)
- External dependencies: `urequests`, `ntptime` (installed via mip, not bundled)
- No threading, no async/await in most modules
- `gc.collect()` is called explicitly to manage memory pressure
- `input()` has no hidden mode (passwords are visible)
- `ticks_ms` overflows after ~12-25 days
- `socket` has no `setdefaulttimeout()`: pass the timeout with each request (`pico_utils.http_request`)
- `requests` reads `.text`/`.json()` bodies whole: stream big ones from `response.raw` (see `rss_news._read_body`)
- rp2 floats are single precision; ints are arbitrary precision
- The clock reads 2021-01-01 after power-on until NTP sets it (`pico_utils.clock_synced()`)

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
| `synthesizer.py` | Tone/note synthesizer (PWM or I2S) | No | Yes (speaker/DAC) |
| `sys_status.py` | RAM, flash, uptime, IP, CPU | No | No |

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

To verify changes:

1. Host checks for the pure logic (parsers, formatters, helpers), with both interpreters: `python3 tests/test_logic.py` and `micropython tests/test_logic.py` (MicroPython unix port: `brew install micropython`). Builtin modules can't be monkeypatched on MicroPython: stub through module attributes instead.
2. Verify output fits the screen width by visual inspection
3. On-device testing is the primary validation method

## Build and deploy

No build step. Copy all `.py` files to the Pico root via USB (Thonny, `mpremote`, or MicroPico VS Code extension). The device runs files directly from flash.
