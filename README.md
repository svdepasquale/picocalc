# PicoCalc Wi-Fi + OpenRouter AI (Manual Workflow)

Minimal Wi-Fi + AI chat setup for PicoCalc + Pico 2W, optimized for small display and keyboard.

Screen tuning in this version:
- SSID preview clipped to 12 chars
- network menu shows top 6 entries
- shorter status/error messages for less line wrapping

## Files
- `wifi_manager.py` → connect logic + interactive setup
- `openrouter_ai.py` → OpenRouter API client + compact chat output

Copy these files to Pico root:
- `/wifi_manager.py`
- `/openrouter_ai.py`

Created automatically after first successful connect:
- `/wifi_credentials.json`

Created when AI key/model is configured:
- `/openrouter_config.json`

## Runtime behavior
- Manual-first workflow (recommended for PicoCalc session behavior).
- Connect Wi-Fi from REPL, then run AI commands.

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

## First-time setup
1. Upload `wifi_manager.py` and `openrouter_ai.py`.
2. Open serial monitor.
3. Run `import wifi_manager as w; w.ac()`.
4. Run AI commands after Wi-Fi is connected.

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

## Current version
- `wifi_manager`: `2026-02-15.17`
- `openrouter_ai`: `2026-02-15.7`
