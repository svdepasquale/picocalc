"""Apps from the SD card: every .py in /sd/apps (or /apps on flash) runs from
a list, as if it were main.py. A header in the first lines names it:
    # picocalc-app: Name | what it does
LofiFren's three-field form (Name | Category | what it does) works too."""

import gc
import os
import sys

from pico_utils import clear_screen as _clear_screen, ensure_screen as _ensure_screen
from pico_utils import paint as _paint, pick as _pick, screen_header as _screen_header
from pico_utils import wait_key as _wait_key
from pico_utils import _CONSOLE, GREY


MODULE_VERSION = "2026-10-07.1"
APP_DIRS = ("/sd/apps", "/apps")
HEADER = "# picocalc-app:"
HEADER_LINES = 5  # the header is looked for in the first lines only
_QUIT = ("q", "Q", "esc", "eof", "left", "backspace")


# ── discovery ───────────────────────────────────


def parse_header(line):
    """'# picocalc-app: Name | Category | about' -> (name, category, about);
    two fields are name and about. None when the line isn't a header."""
    line = line.strip()
    if not line.startswith(HEADER):
        return None
    parts = [part.strip() for part in line[len(HEADER) :].split("|")]
    if not parts[0]:
        return None
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], parts[1], parts[-1]


def _read_header(path):
    try:
        with open(path) as f:
            for _ in range(HEADER_LINES):
                line = f.readline()
                if not line:
                    break
                info = parse_header(line)
                if info:
                    return info
    except Exception:  # unreadable, or not UTF-8
        pass
    return None


def discover(dirs=APP_DIRS):
    """[(name, category, about, path)] for the .py files in `dirs`, by name.
    Skips dot files (macOS leaves ._name copies on FAT cards)."""
    found = []
    for folder in dirs:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for fname in names:
            if not fname.endswith(".py") or fname.startswith("."):
                continue
            path = folder.rstrip("/") + "/" + fname
            info = _read_header(path) or (fname[:-3], "", "")
            found.append((info[0], info[1], info[2], path))
    found.sort(key=lambda app: app[0].lower())
    return found


# ── running ─────────────────────────────────────


def _print_error(error):
    try:
        sys.print_exception(error)
    except AttributeError:  # CPython
        print("{}: {}".format(type(error).__name__, error))


def _compile(path):
    gc.collect()
    with open(path) as f:
        source = f.read()
    try:
        return compile(source, path, "exec")
    except NameError:  # builds without compile(): exec() takes the text
        return source


def _take_screen():
    # The app gave gfx's console up (gfx.end(), or console_suspend() and an
    # error): the rest would be drawn by the firmware terminal. resume() is
    # not enough after end(), which drops the framebuffer too.
    import gfx

    if _CONSOLE[0] is not gfx.CON or gfx._FB[0] is None:
        gfx.begin()
        gfx.app()


def run_app(path, name=None):
    """Run one app file as __main__. q in the app, an error, sys.exit() or
    Ctrl+C come back here; modules it imported are dropped afterwards, the
    working directory and the launcher's screen console are put back."""
    folder = path.rsplit("/", 1)[0] or "/"
    added = folder not in sys.path
    if added:
        sys.path.append(folder)  # sibling imports; never shadows the toolkit
    loaded = set(sys.modules)
    cwd = os.getcwd()  # the toolkit and its config files are found through it
    console = _CONSOLE[0] is not None  # run from the launcher
    _clear_screen()
    ended = "end of"
    failure = None
    try:
        exec(_compile(path), {"__name__": "__main__", "__file__": path})
    except (SystemExit, KeyboardInterrupt):
        pass
    except MemoryError:
        ended = "stopped"
    except Exception as error:
        failure = error
        ended = "error in"
    finally:
        for module in list(sys.modules):
            if module not in loaded:
                del sys.modules[module]
        if added and folder in sys.path:
            sys.path.remove(folder)
        os.chdir(cwd)
        gc.collect()
        _ensure_screen()
        if console:
            _take_screen()
    # Reported only now: taking the screen back clears it.
    if failure is not None:
        print("")
        _print_error(failure)
        failure = None
    elif ended == "stopped":
        print("Out of memory.")
    _wait_key(_paint("-- {} {}: any key --".format(ended, name or path), GREY))
    return ended == "end of"


def launcher():
    """List the apps; Enter (or the row's number) runs one, q goes back."""
    pos = 0
    while True:
        found = discover()
        if not found:
            _screen_header("Apps")
            print("No apps yet: put .py files in /sd/apps.")
            print("Optional first line:")
            print("  # picocalc-app: Name | what it does")
            print("")
            _wait_key()
            return None
        labels = []
        hints = []
        for name, category, about, path in found:
            labels.append(name + ("  " + about if about else ""))
            hints.append(category or ("SD" if path.startswith("/sd/") else "flash"))
        key, pos = _pick("Apps", labels, hints, pos=min(pos, len(found) - 1))
        if key in ("enter", "right"):
            run_app(found[pos][3], found[pos][0])
        elif key in _QUIT:
            _clear_screen()
            return None


def ls():
    """Print the apps found, with their files."""
    found = discover()
    if not found:
        print("No apps in", ", ".join(APP_DIRS))
    for name, category, about, path in found:
        print(name, "-", about or category or "", "(" + path + ")")
    return len(found)


# ── standard ────────────────────────────────────


def ver():
    print("apps:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Apps (.py in /sd/apps or /apps) --")
    print("launcher()  l()   Pick one and run it")
    print("run_app(path) r() Run one file as __main__")
    print("ls()              List what was found")
    print("Header: # picocalc-app: Name | about")


def h():
    return help()


l = launcher
r = run_app
