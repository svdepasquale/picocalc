# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this toolkit, please **do not** open a public GitHub issue.

Instead, report it privately via one of:

- GitHub Security Advisories: [create a private advisory](https://github.com/svdepasquale/picocalc/security/advisories/new)
- Email: `silvio.depasquale@pm.me`

Please include:

- A description of the vulnerability and its potential impact
- Steps to reproduce on a PicoCalc + Pico 2W
- Affected files or modules
- Any suggested mitigation

You will receive an acknowledgement within **72 hours** and a status update within **7 days**.

## Scope

In scope:

- Credential handling in `wifi_manager.py` and `openrouter_ai.py`
- JSON config files created on the Pico flash (`/wifi_credentials.json`, `/openrouter_config.json`, etc.)
- Input validation in any module that performs HTTP requests or parses external data (`rss_news.py`, `weather.py`, `openrouter_ai.py`)
- Memory safety / crash-inducing inputs exploitable remotely (e.g. crafted RSS feeds or AI responses)

Out of scope:

- Physical access attacks (the device runs MicroPython on unauthenticated flash)
- Issues in upstream MicroPython or `mip`-installed packages (`urequests`, `ntptime`) — report to the respective projects
- Wi-Fi credentials stored in plaintext on flash — this is a known limitation of the target hardware and documented behavior

## Secrets

API keys (OpenRouter) and Wi-Fi credentials are stored in JSON files on the device's flash. Do not commit these files, and do not share device images without wiping `/wifi_credentials.json` and `/openrouter_config.json`.
