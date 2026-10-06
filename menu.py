"""Launcher for standalone PicoCalc use: an app's key (or arrows + Enter) opens it."""

import gc
import sys

import gfx
from pico_utils import DISPLAY_WIDTH
from pico_utils import ensure_screen as _ensure_screen
from pico_utils import key_bar as _key_bar, load_json as _load_json, paint as _paint
from pico_utils import read_key as _read_key, read_line as _read_line
from pico_utils import screen_header as _screen_header
from pico_utils import wait_key as _wait_key, wifi_connected as _wifi_connected
from pico_utils import refresh_status as _refresh_status
from pico_utils import HostTakeover as _HostTakeover, wrap_text as _wrap_text
from pico_utils import BLACK, BCYAN, BWHITE, BYELLOW, GREY, WHITE


MODULE_VERSION = "2026-10-06.2"
LOW_MEMORY = 60000  # below this, idle apps are unloaded before opening another
REFRESH_MS = 30000  # status bar refresh while the menu waits
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
    "files",
    "music",
    "apps",
    "snake",
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
            _refresh_status()
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
            _refresh_status()


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


def _files():
    _load("files").browse()


def _music():
    _load("music").player()


def _apps():
    _load("apps").launcher()


def _snake():
    _load("snake").play()


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


def _hint_apps():
    import os

    count = 0
    for folder in ("/sd/apps", "/apps"):
        try:
            count += len([n for n in os.listdir(folder) if n.endswith(".py")])
        except OSError:
            pass
    return "{} found".format(count) if count else ""


def _hint_music():
    import os

    count = 0
    for folder in ("/sd/music", "/sd"):
        try:
            count += len([n for n in os.listdir(folder) if n.lower().endswith(".wav") and n[0] != "."])
        except OSError:
            pass
    return "{} tracks".format(count) if count else ""


def _hint_snake():
    best = (_load_json("snake.json") or {}).get("best")
    return "best {}".format(best) if best else ""


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
    ("0", "▬", "Files", _files, None),
    ("m", "ƒ", "Music", _music, _hint_music),
    ("a", "►", "Apps", _apps, _hint_apps),
    ("s", "§", "Snake", _snake, _hint_snake),
)
_KEY_INDEX = dict((app[0], i) for i, app in enumerate(_APPS))
_ABOUT = {
    "1": (
        "Join a saved network or scan for a new one, forget old ones. The menu joins a saved network by itself at start.",
        (("c", "connect"), ("s", "saved"), ("f", "forget")),
    ),
    "2": (
        "Ask Claude, or any OpenRouter model or a server on your network. Answers appear as they are written.",
        (("Enter", "send"), ("empty line", "back")),
    ),
    "3": (
        "Headlines from your RSS feeds and unread entries from Miniflux, one story per screen.",
        (("f", "feeds"), ("m", "Miniflux"), ("v", "last list"), ("d", "mark read")),
    ),
    "4": (
        "Weather now and for the next days from Open-Meteo, for the city you choose.",
        (("r", "refresh"), ("c", "city")),
    ),
    "5": (
        "Notes and todos kept on flash: add them, tick them off, clear the done ones.",
        (("v", "view"), ("a", "add"), ("l", "list"), ("c", "clear done")),
    ),
    "6": (
        "Scientific calculator: trig, logs, conversions. Start a line with +5 or *2 to go on from the last result.",
        (("Enter", "result"), ("h", "help"), ("q", "back")),
    ),
    "7": (
        "The keyboard turns into a piano on the speakers: z to m play the notes, s d g h j the sharps.",
        (("+/-", "octave"), ("1-4", "wave"), ("q", "back")),
    ),
    "8": (
        "Clock set from the internet, a big live clock, a stopwatch and a countdown that beeps.",
        (("l", "live"), ("s", "sync"), ("t", "timer"), ("c", "countdown"), ("z", "zone")),
    ),
    "9": (
        "Memory, flash, battery, Wi-Fi and uptime at a glance, plus a key test and the colour chart.",
        (("k", "key test"), ("c", "colours")),
    ),
    "0": (
        "Browse the flash and the SD card: read text files, edit them, run .py files, delete.",
        (("e", "edit"), ("r", "run"), ("d", "delete"), ("n", "new file")),
    ),
    "m": (
        "WAV files from the SD card on the speakers or the headphones. Convert MP3s on the computer with tools/to_wav.sh.",
        (("Enter", "play"), ("Space", "pause"), ("+/-", "volume"), ("\u2190\u2192", "track")),
    ),
    "a": (
        "Your own programs: every .py file in /sd/apps shows up here and runs with one key.",
        (("Enter", "run"), ("q", "back")),
    ),
    "s": (
        "Classic Snake: eat, grow and speed up without biting yourself. The best score is kept.",
        (("arrows/WASD", "steer"), ("p", "pause"), ("m", "sound")),
    ),
}


# ── drawing ─────────────────────────────────────
# Straight into the framebuffer (gfx), never print(): a full draw takes
# ~40 ms and a move ~10 ms, against ~550 and ~100 ms through the terminal
# (measured 2026-10-06). A move still repaints only two rows and the panel.

_X = gfx.CHAR_W  # left margin
_ROW_H = 10  # 8-pixel font, 2 pixels of air
_LIST_Y = gfx.TITLE_H + 4  # first app row
_PANEL_Y = _LIST_Y + len(_APPS) * _ROW_H + 6  # separator above the panel
_PANEL_TEXT = 3  # description lines
_PANEL_H = (_PANEL_TEXT + 2) * _ROW_H + 6
_TEXT_COLS = gfx.COLUMNS - 3
_FOOTER_Y = gfx.HEIGHT - 16  # separator, then the key bar
_BLOCK = 6  # logo pixel pitch
# Text in font codes and logo blocks, made once: each conversion costs more
# than drawing the text (gfx timings).
_FOOTER_KEYS = tuple(
    (gfx.cp(k), gfx.cp(v)) for k, v in (("↑↓", "choose"), ("Enter/key", "open"), ("q", "REPL"))
)
_CELLS = []  # (key, icon, label) per app
_PANELS = {}  # app key -> (title, description lines, key pairs)
_LOGO = []  # (x, y, colour) per block
_LOGO_FONT = {
    "P": ("████ ", "█   █", "████ ", "█    ", "█    "),
    "I": ("███", " █ ", " █ ", " █ ", "███"),
    "C": (" ████", "█    ", "█    ", "█    ", " ████"),
    "O": (" ███ ", "█   █", "█   █", "█   █", " ███ "),
    "A": (" ███ ", "█   █", "█████", "█   █", "█   █"),
    "L": ("█    ", "█    ", "█    ", "█    ", "█████"),
}


def _cells():
    if not _CELLS:
        _CELLS.extend(tuple(gfx.cp(part) for part in app[:3]) for app in _APPS)
    return _CELLS


def _row(index, hints, selected):
    key, icon, label = _cells()[index]
    y = _LIST_Y + index * _ROW_H
    gfx.fill_rect(0, y, gfx.WIDTH, _ROW_H, BCYAN if selected else BLACK)
    y += 1
    gfx.text(key, _X, y, BLACK if selected else BYELLOW)
    gfx.text(icon, _X * 4, y, BLACK if selected else BCYAN)
    gfx.text(label, _X * 7, y, BLACK if selected else WHITE)
    if hints[index]:
        gfx.text(hints[index], _X * 20, y, BLACK if selected else GREY)


def _panel_text(index):
    key = _APPS[index][0]
    if key not in _PANELS:
        _, icon, label = _cells()[index]
        about, keys = _ABOUT[key]
        lines = [gfx.cp(line) for line in _wrap_text(about, _TEXT_COLS)[:_PANEL_TEXT]]
        pairs = tuple((gfx.cp(k), gfx.cp(v)) for k, v in keys)
        _PANELS[key] = (icon + b" " + label, lines, pairs)
    return _PANELS[key]


def _panel(index, hints):
    # What the highlighted app does, and its keys.
    title, lines, keys = _panel_text(index)
    gfx.fill_rect(0, _PANEL_Y + 1, gfx.WIDTH, _PANEL_H, BLACK)
    y = _PANEL_Y + 5
    x = gfx.text(title, _X, y, BCYAN)
    if hints[index]:
        gfx.text(hints[index], x + 2 * gfx.CHAR_W, y, GREY)
    for i, line in enumerate(lines):
        gfx.text(line, _X, y + (i + 1) * _ROW_H, WHITE)
    gfx.key_bar(keys, _X, y + (_PANEL_TEXT + 1) * _ROW_H + 2)


def _word(word, line):
    return " ".join(_LOGO_FONT[ch][line] for ch in word)


def _machine_line():
    # Bytes in font codes: 0xFA is the CP437 middle dot.
    impl = sys.implementation
    board = getattr(impl, "_machine", "").split(" with ")[0].replace("Raspberry Pi ", "")
    parts = (board, "MicroPython " + ".".join(str(n) for n in impl.version[:3]))
    parts = [p for p in parts if p] + ["{} KB free".format(_mem_free() // 1024)]
    return b"  \xfa  ".join(p.encode() for p in parts)


def _logo_top():
    top = _PANEL_Y + _PANEL_H + 1
    return top + (_FOOTER_Y - top - 5 * _BLOCK - 16) // 2


def _logo():
    # PICOCALC in block letters and a status line, between panel and footer.
    top = _logo_top()
    if not _LOGO:
        cells = len(_word("PICO", 0)) + 2 + len(_word("CALC", 0))
        x0 = (gfx.WIDTH - cells * _BLOCK) // 2
        for line in range(5):
            x = x0
            for word, color in (("PICO", BCYAN), ("CALC", BWHITE)):
                for ch in _word(word, line):
                    if ch != " ":
                        _LOGO.append((x, top + line * _BLOCK, color))
                    x += _BLOCK
                x += 2 * _BLOCK
    for x, y, color in _LOGO:
        gfx.fill_rect(x, y, _BLOCK - 1, _BLOCK - 1, color)
    gfx.center(_machine_line(), top + 5 * _BLOCK + 8, GREY)


def _draw(selected, hints):
    gfx.begin()
    gfx.clear()
    gfx.title_bar("PicoCalc")
    for index in range(len(_APPS)):
        _row(index, hints, index == selected)
    gfx.hline(0, _PANEL_Y, gfx.WIDTH, GREY)
    _panel(selected, hints)
    _logo()
    gfx.hline(0, _FOOTER_Y, gfx.WIDTH, GREY)
    gfx.key_bar(_FOOTER_KEYS, _X, _FOOTER_Y + 5)


def _move(old, new, hints):
    _row(old, hints, False)
    _row(new, hints, True)
    _panel(new, hints)


def _hints():
    out = []
    for app in _APPS:
        try:
            out.append(app[4]() if app[4] else "")
        except Exception:
            out.append("")
    return out


def _launch(index):
    gfx.app()  # what the app prints is drawn by gfx's console
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
    gfx.app()
    _screen_header("PicoCalc")
    print("Connecting Wi-Fi...  " + _paint("q", BYELLOW) + " skips while connecting")
    try:
        w.acs()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print("WiFi:", error)
    _refresh_status()


def run(connect=True):
    """An app's key (or arrows + Enter) opens it; q (or Esc) leaves to the
    REPL, and so does mpremote/Thonny writing on USB.
    connect: join a saved Wi-Fi network first."""
    _ensure_screen()
    gfx.begin()
    note = ""
    try:
        _loop(connect)
    except _HostTakeover:
        note = " (USB host)"
    finally:
        gfx.end()  # never leave the REPL on the console
    print("REPL{}. Menu: import go".format(note))


def _loop(connect):
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
            gfx.title_bar("PicoCalc")
            continue
        if key in _BACK:
            break
        if key in ("up", "down"):
            old = selected
            selected = (selected + (1 if key == "down" else -1)) % len(_APPS)
            _move(old, selected, hints)
            continue
        if key == "enter":
            index = selected
        elif key in _KEY_INDEX:
            index = _KEY_INDEX[key]
        else:
            continue
        selected = index
        _launch(index)
        hints = _hints()
        redraw = True


def ver():
    print("menu:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Menu --")
    print("run()         Open the launcher (q: REPL)")
    print("import go     Same, from the REPL")


def h():
    return help()
