"""Launcher for standalone PicoCalc use: a number (or arrows + Enter) opens an app."""

import gc
import sys

from pico_utils import DISPLAY_WIDTH
from pico_utils import clear_screen as _clear_screen, ensure_screen as _ensure_screen
from pico_utils import key_bar as _key_bar, load_json as _load_json, paint as _paint
from pico_utils import read_key as _read_key, read_line as _read_line
from pico_utils import screen_header as _screen_header, title_bar as _title_bar
from pico_utils import wait_key as _wait_key, wifi_connected as _wifi_connected
from pico_utils import BLACK, BCYAN, BYELLOW, GREY


MODULE_VERSION = "2026-10-04.3"
LOW_MEMORY = 60000  # below this, idle apps are unloaded before opening another
REFRESH_MS = 30000  # status bar refresh while the menu waits
_FIRST_ROW = 3  # screen row of the first app (title bar, blank line)
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


def _choose(title, body, keys):
    # A sub-screen: optional body, a hint bar, then the next key (None = back).
    _screen_header(title)
    if body is not None:
        body()
    print(_paint("─" * DISPLAY_WIDTH, GREY))
    _key_bar(keys + (("q", "back"),))
    key = _read_key()
    return None if key in _BACK else key


# ── apps ────────────────────────────────────────


def _wifi():
    w = _load("wifi_manager")
    while True:
        key = _choose(
            "WiFi",
            w.print_connection_status,
            (("c", "connect"), ("s", "saved"), ("f", "forget")),
        )
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
                number = _read_line("Forget # then Enter: ")
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
            (("f", "feeds"), ("m", "Miniflux"), ("v", "last list"), ("d", "mark read")),
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
        print("")
        m.forecast(3)

    while True:
        key = _choose("Weather", body, (("r", "refresh"), ("c", "city")))
        if key is None:
            return
        if key == "c":
            city = _read_line("City then Enter: ")
            if city:
                m.set_city(city)
                _wait_key()


def _notes():
    t = _load("notes")
    while True:
        key = _choose(
            "Notes",
            t.count,
            (("v", "view"), ("a", "add"), ("l", "list"), ("c", "clear done")),
        )
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


def _set_zone(c):
    print("1 Italy/CET  2 UK  3 UTC  4 other")
    zone = _read_key()
    if zone == "1":
        c.set_utc_offset(1)
        c.set_dst(True)
    elif zone == "2":
        c.set_utc_offset(0)
        c.set_dst(True)
    elif zone == "3":
        c.set_utc_offset(0)
        c.set_dst(False)
    elif zone == "4":
        offset = _read_line("UTC offset in winter, then Enter: ")
        if offset:
            c.set_utc_offset(offset)
            print("EU summer time? y/n")
            c.set_dst(_read_key() in ("y", "Y"))
    _wait_key()


def _clock():
    c = _load("clock_ntp")
    while True:
        key = _choose(
            "Clock",
            c.date,
            (("l", "live"), ("s", "sync"), ("t", "timer"), ("c", "countdown"), ("z", "zone")),
        )
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
            secs = _read_line("Seconds then Enter: ")
            if secs:
                c.countdown(secs)
                _wait_key()
        elif key == "z":
            _set_zone(c)


def _system():
    s = _load("sys_status")
    while True:
        key = _choose("System", lambda: s.info(False), (("k", "key test"), ("c", "colours")))
        if key is None:
            return
        if key == "k":
            s.keys()
            _wait_key()
        elif key == "c":
            s.colors()
            _wait_key()


# ── hints: one cheap line of state next to some apps ──


def _hint_wifi():
    return "on" if _wifi_connected() else "off"


def _hint_ai():
    config = _load_json("openrouter_config.json") or {}
    model = str(config.get("model") or "anthropic/claude-sonnet-5.5")
    return model.split("/")[-1]


def _hint_weather():
    config = _load_json("weather_config.json") or {}
    return str(config.get("name") or "")


def _hint_notes():
    notes = _load_json("notes_data.json")
    if not isinstance(notes, list) or not notes:
        return ""
    open_count = len([n for n in notes if isinstance(n, dict) and not n.get("done")])
    return "{} open".format(open_count)


_APPS = (
    ("1", "≈", "WiFi", _wifi, _hint_wifi),
    ("2", "»", "AI chat", _ai, _hint_ai),
    ("3", "¶", "News", _news, None),
    ("4", "°", "Weather", _weather, _hint_weather),
    ("5", "≡", "Notes", _notes, _hint_notes),
    ("6", "±", "Calculator", _calc, None),
    ("7", "∩", "Synth piano", _synth, None),
    ("8", "Θ", "Clock", _clock, None),
    ("9", "■", "System", _system, None),
)


def _row_text(index, hints, selected):
    key, icon, label, _, _ = _APPS[index]
    hint = hints[index]
    if selected:
        text = " {}  {}  {:<13}{}".format(key, icon, label, hint)
        return _paint("{:<{}}".format(text, DISPLAY_WIDTH)[:DISPLAY_WIDTH], BLACK, BCYAN)
    return " {}  {}  {:<13}{}".format(
        _paint(key, BYELLOW), _paint(icon, BCYAN), label, _paint(hint, GREY)
    )


def _paint_row(index, hints, selected):
    print("\x1b[{};1H\x1b[K{}".format(_FIRST_ROW + index, _row_text(index, hints, selected)), end="")


def _draw(selected, hints):
    _clear_screen()
    print(_title_bar("PicoCalc"))
    print("")
    for index in range(len(_APPS)):
        print(_row_text(index, hints, index == selected))
    print("")
    print(_paint("─" * DISPLAY_WIDTH, GREY))
    _key_bar((("1-9", "open"), ("↑↓", "choose"), ("Enter", "open"), ("q", "REPL")))


def _hints():
    out = []
    for app in _APPS:
        try:
            out.append(app[4]() if app[4] else "")
        except Exception:
            out.append("")
    return out


def _launch(index):
    try:
        _APPS[index][3]()
    except KeyboardInterrupt:
        pass
    except MemoryError:
        gc.collect()
        print("Out of memory.")
        _wait_key()
    except Exception as error:
        print("Error:", error)
        _wait_key()


def _autoconnect():
    # Saved networks only; q/Esc skips while it tries.
    if _wifi_connected():
        return
    w = _load("wifi_manager")
    if not w.load_credentials():
        return
    _screen_header("PicoCalc")
    print("Connecting Wi-Fi...  " + _paint("q", BYELLOW) + " skips while connecting")
    try:
        w.acs()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print("WiFi:", error)


def run(connect=True):
    """1-9 or arrows + Enter open an app; q (or Esc) leaves to the REPL.
    connect: join a saved Wi-Fi network first."""
    _ensure_screen()
    if connect:
        _autoconnect()
    selected = 0
    hints = _hints()
    redraw = True
    while True:
        if redraw:
            _draw(selected, hints)
            redraw = False
        try:
            key = _read_key(REFRESH_MS)
        except KeyboardInterrupt:
            break
        if key is None:
            print("\x1b[1;1H" + _title_bar("PicoCalc"), end="")
            continue
        if key in _BACK:
            break
        if key in ("up", "down"):
            old = selected
            selected = (selected + (1 if key == "down" else -1)) % len(_APPS)
            _paint_row(old, hints, False)
            _paint_row(selected, hints, True)
            continue
        if key == "enter":
            index = selected
        elif len(key) == 1 and "1" <= key <= "9":
            index = int(key) - 1
        else:
            continue
        selected = index
        _launch(index)
        hints = _hints()
        redraw = True
    _clear_screen()
    print("REPL. Menu: import go")


def ver():
    print("menu:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Menu --")
    print("run()         Open the launcher")
    print("import go     Same, from the REPL")


def h():
    return help()
