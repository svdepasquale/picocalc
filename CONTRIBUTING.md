# Contributing to PicoCalc Toolkit

Thanks for considering a contribution. This toolkit runs MicroPython on the PicoCalc (Raspberry Pi Pico 2W) with a 320×320 screen and a tiny keyboard, so the constraints are unusual. Contributions are welcome as:

- Bug reports (please include the Pico firmware version and a minimal reproducer)
- New modules that fit the existing flat structure and the 32-char display width
- Memory or performance improvements
- Documentation and example improvements

## Constraints to respect

- **MicroPython, not CPython.** No f-strings, no `async`/`await` (unless the module is designed for it), no PyPI dependencies.
- **Flat root layout.** All `.py` files must live in the repo root — MicroPython imports from `/` on the device.
- **32 characters wide, 8 lines per page.** Output must fit without wrapping.
- **Memory is tight** (~260 KB RAM). Call `gc.collect()` before large allocations and paginate long output.
- **No hardcoded secrets.** Wi-Fi credentials and API keys go in JSON config files on flash.
- **Every module exposes `ver()`, `help()`, `h()`** and a short alias for each interactive function.

See [CLAUDE.md](CLAUDE.md) for the full coding conventions.

## Before opening a PR

1. **Open an issue first** for new modules or API changes. Align on scope before writing code.
2. **Keep PRs focused.** One module or one fix per PR.
3. **Bump `MODULE_VERSION`** in the files you change (format `YYYY-MM-DD.N`).

## Local checks

There is no test harness yet. Minimum verification:

```bash
# Syntax check with standard Python
python3 -m py_compile module.py

# Visual check for 32-char width
awk 'length > 32 {print NR": "length" chars: "$0}' module.py
```

On-device testing via serial REPL is the primary validation. Please note the Pico revision and MicroPython build in your PR description.

## Commit style

- Short imperative subject, lowercase, no trailing period (e.g. `rss: paginate long preview output`)
- Reference the issue number in the body if applicable

## Signing off

By submitting a pull request you agree to license your contribution under the [MIT License](LICENSE) that covers this repository.

## Security issues

Do **not** open a public issue for security vulnerabilities. See [SECURITY.md](SECURITY.md).
