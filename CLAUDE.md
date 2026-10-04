# CLAUDE.md — PicoCalc Toolkit

## What this project is

A modular MicroPython toolkit for the **PicoCalc** hardware (Raspberry Pi Pico 2W), meant to be used standalone with the device's keyboard and screen: a launcher (`menu.py`, opened at power-on by the `default_style.py` hook) plus Wi-Fi management, AI chat (OpenRouter or a local server), RSS/Miniflux news, clock/NTP, notes/todo, weather, scientific calculator, WAV player, synthesizer, file manager, apps from the SD card, Snake and system status.

## Target hardware

- **Board:** Raspberry Pi Pico 2W (RP2350, wireless). The kit ships with a Pico H (RP2040); this toolkit targets the 2W.
- **Enclosure:** PicoCalc v2.0 mainboard (keyboard MCU, SD slot, 2 speakers + 3.5 mm jack, 8 MB PSRAM that the stock `RPI_PICO2_W` MicroPython build does not enable)
- **Display:** 4" IPS 320x320 (ILI9488, SPI1). `pico_utils` reads the terminal size: 52 usable columns and 34-line pages on the official firmware, a 32x8 fallback elsewhere (host tests)
- **Audio:** Built-in speakers on PWM GP26 (L) + GP27 (R), the pins the official `boot.py` uses (other ClockworkPi sources put L on GP28). GP22 is the SD card-detect line, not audio. An external I2S DAC (SCK=GP16, WS=GP17, SD=GP28) collides with the SD card, which sits on SPI0 GP16-19.
- **RAM:** RP2350 has 520 KB SRAM; the MicroPython heap is what `gc.mem_free()` reports, minus the official driver's ~50 KB framebuffer (320x320 at 4 bpp). Memory is still a hard constraint

## Official firmware facts

ClockworkPi ships MicroPython built on [PicoCalc-micropython-driver](https://github.com/zenodante/PicoCalc-micropython-driver). The latest `micropython_pico2w.uf2` (2025-10-30) is MicroPython 1.27.0-preview:

- `boot.py` starts display, keyboard, SD (`/sd`) and speakers, then `os.dupterm()`s the screen terminal. The 2025-10-30 build freezes `boot.py` and `main.py` in the firmware; older driver builds keep them on flash (never delete `boot.py` there).
- MicroPython runs a frozen `main.py` before one on flash (`shared/runtime/pyexec.c`, `pyexec_file_if_exists`), so the toolkit's `main.py` can only run on firmwares without a frozen one (inferred from that code, not tried); `import go` opens the launcher everywhere. The frozen `main.py` is empty.
- Auto-start: the frozen `boot.py` imports `pye` (and through it `default_style`) before it builds the keyboard, and a flash module shadows a frozen one in normal imports. `default_style.py` re-exports the frozen colours and, when imported within `COLD_BOOT_MS` of power-on (measured: ~145 ms) with `go.py` present, wraps `PicoKeyboard.__init__` once to queue `import go\r` in `hardwarekeyBuf`. Recovery: `mpremote resume rm :default_style.py`.
- After a soft reset the stock terminal is back (no `pico_utils` wrapper), and `mpremote`'s raw-paste handshake detaches it mid-command (`unexpected read during raw paste: b'd'`, the start of dupterm's error): use `mpremote resume ...`, never the default soft reset.
- The firmware's Ctrl+U screenshot (`vt.screencapture`) reads `display.buffer` (never set) and `memoryview.cast` (absent): it raised from `readinto()`, crashing whatever read keys or detaching the screen. `pico_utils._fix_screenshot()` supplies both; screenshots land in `/sd/screen_<ticks>.bmp`.
- `os.statvfs('/sd')` scans the whole FAT on its first call: over a minute on a 32 GB card. Never call it from the UI.
- Audio (`music.py`): PWM base `0x400A8000`, slice stride `0x14` (CSR/DIV/CTR/CC/TOP at 0/4/8/C/10), DREQ_PWM_WRAPn = 32+n (pico-sdk rp2350 headers). Slice 11 has no pins on the RP2350A and counts when enabled: its wrap DREQ (43) paces the DMA at the sample rate (measured 5512 words in 256 ms at 22,049 Hz). DMA channels need `irq_quiet=False` in `pack_ctrl` to raise IRQs (default quiet). Never stop them with `DMA.active(0)`: it aborts an enabled channel, which can spin forever on the RP2350 (erratum E5; the device froze, USB dead). Clear EN (`dma.ctrl = 0`) on both chained channels, then `close()`.
- SD card reads: 89 KB/s at the driver's 1.3 MHz, 214 KB/s at 8 MHz, ~234 KB/s from 12 MHz (Python driver bound); same SHA-256 up to 20 MHz on the user's card. `music.py` uses 8 MHz while playing and restores 1.32 MHz.
- No MP3 decoder in this MicroPython (PicoMite and ClockworkPi's MP3Player decode in C, as separate firmwares); a native-module port of a C decoder would be its own project. Overclocking would not help without one, and changing `machine.freq()` after boot shifts the SPI timings the display and Wi-Fi drivers set at startup.
- Scroll overflow (`vtterminal.c` `scroll()`): it does `YP++`, redraws the whole screen, then clamps `YP`; the 250 ms cursor-blink IRQ (`dispCursor`) can draw/erase the cursor at row 41 in between, 8 pixel rows past the framebuffer. The SD driver's `dummybuf` (0xFF filler sent during reads) sits 192 bytes after it: zeros there desynced the card (EIO, then "no SD card" until power-off). `pico_utils._set_margins()` sets DECSTBM `1;39` at attach and on every `clear_screen()` (measured: 9 bytes zeroed per 150 scrolled lines before, 0 in 300 after). Never move the cursor to row 40: a line feed from there overflows the same way. Residual: `import pico_utils` typed at a full stock REPL keeps the cursor on row 40 (DECSC/DECRC around DECSTBM), so the next Enter is one unprotected line feed; `import go` clears the screen first and is safe. Before any toolkit import (after a soft reset) every scroll is unprotected.
- Drawing costs ~0.23 ms per character in `vtterminal.printChar`, escape bytes included (~4000 chars/s): build a screen once, repaint only the rows that change, avoid colour spans in hot paths. `to_cp437` is a few `replace()` calls, not a per-character loop.
- With the Pico on USB power only (PicoCalc switched off), the keyboard MCU answers I2C reads with `EIO`; the driver raises it from `vt.readinto()` and dupterm detaches the screen. `_ScreenTerm.readinto()` swallows it.
- The same `vt.write()` decode also fails on the raw-paste handshake bytes (`R\x01\x80\x00`) that `mpremote` and Thonny send: connecting a host tool detaches the stock terminal (screen and keyboard dead until reset). With `pico_utils` imported the wrapper takes them as `?`; `import pico_utils` (or `import go`) also re-attaches a terminal that was already detached.
- The terminal is 53x40 characters (6x8 font). It draws each character as the CP437 glyph of its code: no Unicode, so `à` shows as `α`.
- Its `vt.write()` returns characters, not bytes: on non-ASCII output MicroPython re-sends the UTF-8 tail, `decode()` raises, and dupterm detaches screen and keyboard until reset. `pico_utils` installs `_ScreenTerm` over it at import (reports bytes, never raises, maps to CP437). Do not print around it.
- Arrow keys reach stdin as VT100 sequences (`\x1b[A`...), which `input()`'s line editor consumes (history/cursor). The terminal's `readinto()` never blocks and it has no `ioctl`, so `select` can't see device keys: `pico_utils.read_key()` polls it directly.
- Ctrl+C from the device keyboard only acts while something reads stdin: long loops must call `poll_key()` and stop on `q`/Esc.
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
| `music.py` | WAV player: PWM slice 5 + DMA paced by pin-less slice 11 | No | Yes (speakers/jack) |
| `synthesizer.py` | Tone/note synthesizer (PWM or I2S) | No | Yes (speaker/DAC) |
| `sys_status.py` | RAM, flash, uptime, IP, CPU, battery | No | Yes (keyboard MCU) |
| `menu.py` | Launcher, one key per app | No | No |
| `go.py` | `import go` opens the launcher | No | No |
| `main.py` | Opens the launcher at boot (firmwares without a frozen `main.py`) | No | No |
| `default_style.py` | Boot hook: opens the launcher at power-on on the official firmware | No | No |
| `files.py` | File manager: view, edit (firmware's pye), run, delete | No | No |
| `apps.py` | Lists and runs `.py` apps from `/sd/apps` (or `/apps`) | No | No |
| `snake.py` | Snake game | No | Yes (speaker blip) |

`sd/apps/hello.py` is SD card content (a sample app), not a module: it goes to `/sd/apps/` on the card.

## Coding conventions

- **Naming:** `snake_case` for all public functions. Internal/private functions prefixed with `_underscore`.
- **Short aliases:** Every module exposes short aliases for REPL use (e.g., `view()` → `v()`, `latest()` → `l()`). Always define both.
- **Module version:** Each file has a `MODULE_VERSION` constant (or variant like `WIFI_MANAGER_VERSION`). Format: `"YYYY-MM-DD.N"` (e.g., `"2026-03-28.2"`).
- **Standard methods:** Every module must implement `ver()`, `help()`, and `h()` (short alias for help).
- **Display constants:** import `DISPLAY_WIDTH` and `PAGE_LINES` from `pico_utils` (detected at import); never redefine them locally. Output must fit `DISPLAY_WIDTH`.
- **Keys:** navigation uses `pico_utils.read_key()` / `poll_key()` / `wait_key()` (single keys, arrows as `"up"`...); `input()` only for free text; `read_line(mask="*")` for secrets; any `read_line` prompt says "Enter". Lists use `pick()` (returns `(key, index)`) or `select_list()`.
- **USB host takeover:** with the PicoCalc terminal present, keys come only from its keyboard; input on USB serial raises `pico_utils.HostTakeover` (a `BaseException`) so mpremote/Thonny get the REPL at once. Never catch `BaseException` or use bare `except:` in app code: it would swallow the handover.
- **Look:** decoration only through `pico_utils.paint()` / `title_bar()` / `key_bar()` / `bar()` (16-colour palette via `38;5;n`, every span resets). Never colour text that `wrap_text` measures or that tests compare: wrap first, paint after (`preview_print(fg=...)`). Box/block/arrow glyphs map to CP437 in the screen writer.
- **Terminal lookup:** `pico_utils._terminal()` reads `picocalc.terminal` at each use (boot.py rebuilds it); `ensure_screen()` re-attaches the writer.
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
- `/openrouter_config.json` — AI key, model, system prompt, endpoint, stream flag
- `/rss_feeds.json` — RSS feed list + display settings
- `/clock_config.json` — UTC offset, EU DST flag
- `/notes_data.json` — saved notes/todo items
- `/weather_config.json` — location (lat, lon, name)
- `/miniflux_config.json` — Miniflux URL + API key (optional)
- `/snake.json` — Snake best score and sound flag

## Testing

To verify changes:

1. Host checks for the pure logic (parsers, formatters, helpers), with both interpreters: `python3 tests/test_logic.py` and `micropython tests/test_logic.py` (MicroPython unix port: `brew install micropython`). Builtin modules can't be monkeypatched on MicroPython: stub through module attributes instead.
2. Verify output fits the screen width by visual inspection
3. On-device testing is the primary validation method. Keys can be injected without touching the device: `picocalc.keyboard.hardwarekeyBuf.extend(b"...")` (the deque holds 30). To drive the menu after `mpremote` disconnects, arm a `machine.Timer` one-shot that injects them (the alarm pool fits about a dozen timers next to Wi-Fi and the display; use one periodic timer for longer scripts). Inject `\x15` (Ctrl+U) to save a screenshot to `/sd`, or from a script `picocalc_system.screenshot_bmp(memoryview(picocalc.display), '/_shot.bmp')` (flash), copy it with `mpremote resume cp :_shot.bmp .` and convert with `sips -s format png`. Draw the screen from the same script (`menu._draw(0, menu._hints())`) instead of driving the launcher when possible.
4. Connecting `mpremote` while the launcher runs makes it hand over (`REPL (USB host)` on screen); `machine.reset()` at the end of a session brings the launcher back.

## Build and deploy

No build step. Copy the root `.py` files (not `tests/`) to the Pico root via USB (Thonny, `mpremote cp *.py :`, or MicroPico VS Code extension), with the PicoCalc switched on. With the PicoCalc off, `mpremote`'s default soft reset re-runs `boot.py`, whose terminal then raises `EIO` into the raw-REPL handshake: switch it on, or skip the reset with `mpremote resume ...`. The tests also run on the device: `mpremote resume run tests/test_logic.py`.
