# PicoCalc Toolkit (Manual Workflow)

Minimal Wi-Fi + AI + RSS + Clock + Notes + Weather + System toolkit for PicoCalc + Pico 2W, optimized for small display and keyboard.

Screen tuning in this version:
- SSID preview clipped to 12 chars
- network menu shows top 6 entries
- shorter status/error messages for less line wrapping

## Files
- `wifi_manager.py` → connect logic + interactive setup
- `openrouter_ai.py` → OpenRouter API client + compact chat output
- `rss_news.py` → RSS reader with preview-first output + manual feed management
- `sys_status.py` → RAM, flash, uptime, IP, CPU diagnostics
- `clock_ntp.py` → NTP sync, local time, timer, countdown
- `notes.py` → persistent notes/todo with viewer
- `weather.py` → current weather + forecast via Open-Meteo (free, no API key)

Copy all `.py` files to Pico root.

Data files created automatically:
- `/wifi_credentials.json` → saved Wi-Fi networks
- `/openrouter_config.json` → AI key, model, system prompt
- `/rss_feeds.json` → RSS feed list + display settings
- `/clock_config.json` → UTC offset
- `/notes_data.json` → saved notes/todo items
- `/weather_config.json` → location (lat, lon, name)

## Runtime behavior
- Manual-first workflow (recommended for PicoCalc session behavior).
- Connect Wi-Fi from REPL, then run AI/RSS commands.

## Viewer navigation (AI + RSS)
Both `ai.view()` and `n.view()` use the same controls:
- `n` / `→` → next (wraps around)
- `p` / `←` → prev (wraps around)
- `d` / `↑` → full detail
- `q` / `↓` → quit viewer
- `#` → jump to item number

For long paged output (e.g. `ai.ask(...)`, `n.read(...)`):
- press `Enter` (or `n` / `→`) to continue pages
- press `q` (or `↓`) to stop paging

## PicoCalc commands
- `import wifi_manager as w`
- `w.acs()` → saved-only connect
- `w.ac()` → interactive setup/select network
- `w.st()` → print current Wi-Fi status
- `w.ver()` → print module version
- `w.help()` / `w.h()` → short command list

## OpenRouter AI setup
Order of use:
- Connect Wi-Fi first, then use AI commands.

1. Make sure Wi-Fi is connected (`w.st()`).
2. Ensure `urequests` exists on device.
	- If missing, run: `import mip; mip.install('urequests')`
3. Import AI module:
	- `import openrouter_ai as ai`
4. Save your key:
	- `ai.set_api_key('sk-or-v1-...')`
5. (Optional) choose a model:
	- `ai.set_model('openai/gpt-4o-mini')`
6. Ask a question:
	- `ai.ask('Explain DNS in simple words')`

The output is wrapped and paged for PicoCalc screen:
- width: 32 chars
- page size: 8 lines
- prompt and response size limits to reduce memory pressure

### AI commands
- `import openrouter_ai as ai`
- `ai.ask('your question')` → one request
- `ai.chat()` → prompt loop (`Q>`; empty line exits)
- `ai.chat_view()` → prompt loop + open viewer after each answer
- `ai.responses()` → list cached recent responses
- `ai.resp_clear()` → clear cached responses to free RAM
- `ai.view(1)` / `ai.v(1)` → continuous response browser
- `ai.set_api_key('sk-or-v1-...')` → save API key
- `ai.set_model('openai/gpt-4o-mini')` → set default model
- `ai.set_system_prompt('...')` → save custom response style/rules
- `ai.clear_system_prompt()` → remove custom system prompt
- `ai.presets()` → list quick style presets
- `ai.preset('brief')` / `ai.p('teacher')` → apply preset quickly
- `ai.mem_on()` / `ai.mem_off()` → enable/disable short rolling memory
- `ai.mem_status()` → show memory state and usage
- `ai.mem_clear()` → clear memory buffer
- `ai.show_config()` → show model + key presence
- `ai.ver()` / `ai.help()` / `ai.h()`

System prompt behavior:
- default system prompt is optimized for concise, small-screen output
- custom system prompt is saved in `openrouter_config.json`
- used on every request before memory/user messages

Preset names:
- `brief` → very short bullet-style replies
- `teacher` → step-by-step simple explanations
- `code` → practical coding-focused responses

Memory behavior (Pico-safe):
- default: enabled
- keeps only last 6 messages (rolling window)
- uses little RAM while giving short multi-turn context

## RSS news setup
Recommended UX on PicoCalc:
- preview-first (faster, less memory, more readable on 32-char width)
- open one item in detail only when needed (`read(...)`)
- browse multiple items in sequence with viewer mode (`view(...)`)

Order of use:
- Connect Wi-Fi first, then use RSS commands.

1. Ensure `urequests` exists on device.
	- If missing, run: `import mip; mip.install('urequests')`
2. Import RSS module:
	- `import rss_news as n`
3. Show current feed list:
	- `n.feeds()`
4. Read latest previews:
	- `n.latest()`
5. Open one item in detail:
	- `n.read(1)`

Manual feed management from PicoCalc:
- `n.add_feed('BBC', 'https://feeds.bbci.co.uk/news/world/rss.xml')`
- `n.add_feed_prompt()`
- `n.rm_feed(2)` or `n.rm_feed('BBC')`
- `n.reset_feeds()`

RSS config safety/performance notes:
- feed config is sanitized automatically at load (invalid/duplicate entries removed)
- max configured feeds: 12
- HTTP client is initialized once per `n.latest()` run (less overhead across many feeds)
- read-only commands (`n.feeds()`, `n.latest()`, `n.view()`, `n.setup()`) avoid config writes

Display/throughput tuning:
- `n.set_preview(110)` → preview chars per item (range: 48..320)
- `n.set_items_per_feed(2)` → items per feed fetch (range: 1..4)

Default feeds in this version:
- CNN
- ANSA
- Al Jazeera

RSS command list:
- `import rss_news as n`
- `n.latest()` / `n.l()` → fetch and show previews from all feeds
- `n.latest(1)` / `n.latest('CNN')` → fetch one feed only
- `n.view(1)` / `n.v(1)` → continuous news browser
- `n.read(1)` / `n.r(1)` → show selected cached item details
- `n.feeds()` / `n.f()` → list configured feeds
- `n.add_feed(name, url)` / `n.add_feed_prompt()`
- `n.rm_feed(index_or_name)` / `n.reset_feeds()`
- `n.set_preview(chars)` / `n.set_items_per_feed(count)`
- `n.setup()` / `n.ver()` / `n.help()` / `n.h()`

## System status
- `import sys_status as s`
- `s.info()` / `s.a()` → full system overview (RAM, flash, uptime, IP, CPU)
- `s.ram()` → free/used RAM after gc.collect
- `s.flash()` / `s.df()` → flash storage usage
- `s.uptime()` → time since boot
- `s.ip()` → current IP, gateway, DNS
- `s.freq()` → CPU frequency
- `s.ls()` / `s.ls('/lib')` → list files with sizes
- `s.gc_run()` → force garbage collect and show freed bytes
- `s.ver()` / `s.help()` / `s.h()`

## Clock + NTP
Order of use: connect Wi-Fi first, then sync time.

1. `import clock_ntp as c`
2. `c.sync()` → sync from NTP (requires Wi-Fi)
3. `c.now()` / `c.n()` → show local time
4. `c.date()` / `c.d()` → show day + date + time
5. `c.utc()` → show UTC time
6. `c.epoch()` → raw epoch seconds

Timezone:
- `c.set_utc_offset(1)` → CET (Central European Time)
- `c.set_utc_offset(2)` → CEST (summer) or EET
- `c.set_utc_offset(-5)` → US Eastern
- Saved to `clock_config.json`

Timer/Stopwatch:
- `c.timer_start()` / `c.ts()` → start
- `c.timer_stop()` / `c.tp()` → stop and show elapsed
- `c.timer_check()` / `c.tc()` → check without stopping

Countdown:
- `c.countdown(120)` / `c.cd(120)` → wait 120s with progress updates, then print TIME!
- Ctrl+C to cancel

Full command list:
- `c.ver()` / `c.help()` / `c.h()`

## Notes / Todo
Persistent notes stored in `notes_data.json` on flash. Max 50 notes.

- `import notes as t`
- `t.add('Buy milk')` → quick add (title auto-generated)
- `t.add('details here', title='Shopping')` → with explicit title
- `t.add_lines()` → multi-line input (empty line = done)
- `t.add_prompt()` → interactive title + note prompt
- `t.ls()` / `t.l()` → list all notes with [x]/[ ] status
- `t.show(1)` / `t.s(1)` → show full note
- `t.view(1)` / `t.v(1)` → continuous viewer (n/p/d/q/arrows)
- `t.edit(1, 'new text')` → replace note body
- `t.done(1)` → mark as done [x]
- `t.undone(1)` → unmark
- `t.rm(1)` → delete note
- `t.clear_done()` → remove all done notes at once
- `t.count()` → total / done / open summary
- `t.ver()` / `t.help()` / `t.h()`

## Weather
Uses Open-Meteo API (free, no API key required). Requires Wi-Fi.

Default location: Rome (41.9, 12.5). Change with:
- `m.set_location(48.85, 2.35, 'Paris')`
- `m.set_location(40.71, -74.01, 'New York')`
- Saved to `weather_config.json`

Commands:
- `import weather as m`
- `m.now()` / `m.w()` → current temperature, wind, conditions
- `m.forecast()` / `m.fc()` → 3-day forecast (min/max temp + conditions)
- `m.forecast(7)` / `m.fc(7)` → up to 7 days
- `m.show_location()` → show current lat/lon/name
- `m.ver()` / `m.help()` / `m.h()`

Weather output fits PicoCalc 32-char display without wrapping.

## First-time setup
1. Upload all `.py` files to Pico root.
2. Open serial monitor.
3. Run `import wifi_manager as w; w.ac()`.
4. Run other commands after Wi-Fi is connected.

## Remove old startup files on PicoCalc
Run in REPL:
- `import os`
- `print(os.listdir())`
- `os.remove('main.py')`   # ignore error if missing
- `os.remove('boot.py')`   # ignore error if missing
- `print(os.listdir())`

## Troubleshooting
- If disconnected after startup: run `w.acs()`, then `w.st()`.
- If still disconnected: run `w.ac()` and re-enter password.
- Reset credentials: delete `/wifi_credentials.json` and run setup again.
- If version mismatch after upload: reset or reconnect REPL, then `w.ver()`.
- If AI says `Missing urequests.`: run `import mip; mip.install('urequests')`.
- If AI says `No API key.`: run `ai.set_api_key('sk-or-v1-...')`.
- If OpenRouter returns HTTP error: verify key/model and internet access.
- If RSS says `Missing urequests.`: run `import mip; mip.install('urequests')`.
- If RSS returns no items: try `n.latest('CNN')` to test one source only.
- If feed fails repeatedly: remove and re-add URL (`n.rm_feed(...)`, `n.add_feed(...)`).
- If NTP sync fails: check Wi-Fi connection, retry `c.sync()`.
- If ntptime missing: run `import mip; mip.install('ntptime')`.
- If weather shows wrong location: run `m.set_location(lat, lon, 'name')`.

## Quick reference (all aliases)
```
import wifi_manager as w   # w.ac() w.acs() w.st()
import openrouter_ai as ai # ai.ask() ai.chat() ai.v()
import rss_news as n       # n.l() n.r(1) n.v(1) n.f()
import sys_status as s     # s.a() s.ram() s.df() s.ls()
import clock_ntp as c      # c.n() c.d() c.ts() c.tp()
import notes as t          # t.l() t.s(1) t.v(1)
import weather as m        # m.w() m.fc()
```

## Current version
- `wifi_manager`: `2026-02-15.17`
- `openrouter_ai`: `2026-03-01.13`
- `rss_news`: `2026-03-01.9`
- `sys_status`: `2026-03-01.2`
- `clock_ntp`: `2026-03-01.2`
- `notes`: `2026-03-01.2`
- `weather`: `2026-03-01.2`
