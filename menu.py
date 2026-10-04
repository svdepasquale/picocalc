"""Launcher for standalone PicoCalc use: one key opens an app."""

import gc
import sys

from pico_utils import battery as _battery
from pico_utils import clear_screen as _clear_screen
from pico_utils import clock_synced as _clock_synced
from pico_utils import local_time as _local_time
from pico_utils import read_key as _read_key, read_line as _read_line
from pico_utils import screen_header as _screen_header, wait_key as _wait_key
from pico_utils import wifi_connected as _wifi_connected


MODULE_VERSION = "2026-10-04.1"
LOW_MEMORY = 60000  # below this, idle apps are unloaded before opening another
REFRESH_MS = 30000  # menu redraw, keeps the clock in the status line current
_BACK = ("q", "Q", "esc", "eof")
_APP_MODULES = (
    "wifi_manager",
    "openrouter_ai",
    "rss_news",
    "weather",
    "notes",
    "scientific_calc",
    "synthesizer",
    "clock_ntp",
    "sys_status",
)


def _mem_free():
    try:
        return gc.mem_free()
    except AttributeError:  # CPython
        return 1 << 30


def _load(name):
    gc.collect()
    if name not in sys.modules and _mem_free() < LOW_MEMORY:
        for other in _APP_MODULES:
            if other != name and other in sys.modules:
                del sys.modules[other]
        gc.collect()
    return __import__(name)


def _status():
    parts = []
    if _clock_synced():
        now = _local_time()
        parts.append("{:02d}:{:02d}".format(now[3], now[4]))
    bat = _battery()
    if bat is not None:
        parts.append("Bat {}%{}".format(bat[0], " chg" if bat[1] else ""))
    parts.append("WiFi " + ("on" if _wifi_connected() else "off"))
    return "  ".join(parts)


def _choose(title, body, keys):
    # Draw a sub-screen and return the next key (None for "back").
    _screen_header(title)
    if body is not None:
        body()
    print("---")
    print(keys)
    key = _read_key()
    return None if key in _BACK else key


# ── apps ────────────────────────────────────────


def _wifi():
    w = _load("wifi_manager")
    while True:
        key = _choose("WiFi", w.print_connection_status, "c connect  s saved  f forget  q back")
        if key is None:
            return
        if key == "c":
            w.ac()
            _wait_key()
        elif key == "s":
            w.saved()
            _wait_key()
        elif key == "f":
            if w.saved():
                number = _read_line("Forget #: ")
                if number:
                    w.forget(number)
            _wait_key()


def _ai():
    _load("openrouter_ai").chat()


def _news():
    n = _load("rss_news")
    while True:
        key = _choose(
            "News",
            None,
            "f feeds  m Miniflux  v last list\nd mark Miniflux read  q back",
        )
        if key is None:
            return
        if key == "f":
            if n.latest(show=False):
                n.view(1)
            else:
                _wait_key()
        elif key == "m":
            if n.mf():
                n.view(1)
            else:
                _wait_key()
        elif key == "v":
            if n.view(1) is None:
                _wait_key()
        elif key == "d":
            n.mf_done()
            _wait_key()


def _weather():
    m = _load("weather")

    def body():
        m.now()
        print("---")
        m.forecast(3)

    while True:
        key = _choose("Weather", body, "r refresh  c city  q back")
        if key is None:
            return
        if key == "c":
            city = _read_line("City: ")
            if city:
                m.set_city(city)
                _wait_key()


def _notes():
    t = _load("notes")
    while True:
        key = _choose("Notes", t.count, "v view  a add  l list  c clear done  q back")
        if key is None:
            return
        if key == "v":
            if t.view() is None:
                _wait_key()
        elif key == "a":
            t.add_lines()
            _wait_key()
        elif key == "l":
            t.ls()
            _wait_key()
        elif key == "c":
            t.clear_done()
            _wait_key()


def _calc():
    _load("scientific_calc").calc()


def _synth():
    _load("synthesizer").piano()


def _clock():
    c = _load("clock_ntp")
    while True:
        key = _choose("Clock", c.date, "l live  s sync  t timer  c countdown\nz time zone  q back")
        if key is None:
            return
        if key == "l":
            c.live()
        elif key == "s":
            c.sync()
            _wait_key()
        elif key == "t":
            c.timer_toggle()
            _wait_key()
        elif key == "c":
            secs = _read_line("Seconds: ")
            if secs:
                c.countdown(secs)
                _wait_key()
        elif key == "z":
            offset = _read_line("UTC offset in winter (Italy: 1): ")
            if offset:
                c.set_utc_offset(offset)
                print("EU summer time? y/n")
                c.set_dst(_read_key() in ("y", "Y"))
                _wait_key()


def _system():
    _load("sys_status").info()
    _wait_key()


_APPS = (
    ("1", "WiFi", _wifi),
    ("2", "AI chat", _ai),
    ("3", "News", _news),
    ("4", "Weather", _weather),
    ("5", "Notes", _notes),
    ("6", "Calculator", _calc),
    ("7", "Synth piano", _synth),
    ("8", "Clock", _clock),
    ("9", "System", _system),
)


def _draw():
    _screen_header("PicoCalc")
    print(_status())
    print("")
    for key, label, _ in _APPS:
        print("  {}  {}".format(key, label))
    print("")
    print("  q  REPL")


def run():
    """Press a number to open an app; q (or Esc) leaves to the REPL."""
    redraw = True
    while True:
        if redraw:
            _draw()
            redraw = False
        try:
            key = _read_key(REFRESH_MS)
        except KeyboardInterrupt:
            break
        if key is None:
            # only the status line (row 4) changes: no full-screen flicker
            print("\x1b[4;1H\x1b[K" + _status(), end="")
            continue
        if key in _BACK:
            break
        for app_key, _, app in _APPS:
            if key != app_key:
                continue
            redraw = True
            try:
                app()
            except KeyboardInterrupt:
                pass
            except MemoryError:
                gc.collect()
                print("Out of memory.")
                _wait_key()
            except Exception as error:
                print("Error:", error)
                _wait_key()
            break
    _clear_screen()
    print("REPL. Menu: import menu; menu.run()")


def ver():
    print("menu:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Menu --")
    print("run()         Open the launcher")
    print("main.py opens it at boot;")
    print("delete main.py for a plain REPL")


def h():
    return help()
