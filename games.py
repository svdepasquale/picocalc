"""Games: Snake, Tetris and Space Raid (a side-scrolling shooter after the
old Nokia's Space Impact). While a game runs it reads the keyboard itself
(which keys are held, not the driver's repeats) and draws straight into the
framebuffer. The best scores go to /sd/games.json: a game never writes
flash (a flash write with the panel refresh on can hang the PicoCalc: the
Snake freeze of 2026-10-12, see CLAUDE.md)."""

import gc
import os
import sys

import gfx
import pico_utils as _pu
from pico_utils import activity as _activity, load_json as _load_json, pick as _pick
from pico_utils import poll_key as _poll_key, read_key as _read_key, save_json as _save_json
from pico_utils import screen_off as _screen_off, screen_on as _screen_on
from pico_utils import sleep_ms as _sleep_ms, ticks_add as _ticks_add
from pico_utils import ticks_diff as _ticks_diff, ticks_ms as _ticks_ms
from pico_utils import _TICK, _picocalc, _stdin_poll, _terminal
from pico_utils import BLACK, BCYAN, BGREEN, BRED, BWHITE, BYELLOW, GREY, WHITE

MODULE_VERSION = "2026-10-12.1"

# (module, name, what it is)
GAMES = (
    ("snake", "Snake", "eat and grow"),
    ("tetris", "Tetris", "clear the lines"),
    ("space", "Space Raid", "shoot the waves"),
)
_BACK = ("q", "Q", "esc", "eof", "left")


# ── scores ──────────────────────────────────────
# One small JSON file for every game: {"sound": true, "snake": 12, ...}. On
# the SD card when there is one: SD writes go over SPI and leave the flash
# (and core1, which refreshes the panel from it) alone. Without a card it
# falls back to flash, written once when a game is left, not after each one.

SD_DIR = "/sd"
SD_FILE = "/sd/games.json"
FLASH_FILE = "games.json"
OLD_SNAKE = "snake.json"  # Snake's own file before the games section: read once

_STORE = [None, None, False]  # data, path, changed since saved


def _int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _sd_ok():
    try:
        return os.stat(SD_DIR)[0] & 0x4000 != 0
    except OSError:
        return False


def store():
    """The scores and switches, loaded once: a dict."""
    if _STORE[0] is None:
        path = SD_FILE if _sd_ok() else FLASH_FILE
        data = _load_json(path)
        if not isinstance(data, dict):
            data = {}
        if "snake" not in data:
            old = _load_json(OLD_SNAKE)
            if isinstance(old, dict):
                data["snake"] = _int(old.get("best"))
                if "sound" not in data and "sound" in old:
                    data["sound"] = bool(old["sound"])
        _STORE[0] = data
        _STORE[1] = path
        _STORE[2] = False
    return _STORE[0]


def best(game):
    return _int(store().get(game))


def option(name, default):
    return store().get(name, default)


def set_option(name, value):
    data = store()
    if data.get(name) != value:
        data[name] = value
        _STORE[2] = True


def record(game, score):
    """A finished game's score: True if it beat the best (kept)."""
    if score <= best(game):
        return False
    store()[game] = int(score)
    _STORE[2] = True
    return True


def flush(final=False):
    """Save what changed: at once on the SD card; on flash only when final
    (a game is left), so a session without a card writes flash once."""
    if not _STORE[2]:
        return True
    if _STORE[1] != SD_FILE and not final:
        return False
    if _save_json(_STORE[1], _STORE[0]):
        _STORE[2] = False
        return True
    return False


# ── keys: the keyboard as a game pad ────────────
# pico_utils' keys come from the firmware driver's byte stream, which drops
# key releases: a held arrow repeats every 100 ms after 300 ms. A game needs
# to know what is held, so while one runs it reads the keyboard MCU's FIFO
# itself (register 0x09: state, key; 1 pressed, also the repeats of arrows
# and printable keys; 2 held; 3 released; clockworkpi's keyboard firmware).
# Nothing else reads the FIFO meanwhile: the driver does only from stdin
# reads. One I2C read for the count costs ~5 ms at the driver's 10 kHz
# (measured 2026-10-12), plus one per event: one poll per frame.

_REG_KEY = 0x04
_REG_FIF = 0x09
_RELEASED = 3
_NAMES = {
    0xB1: "esc",
    0xB4: "left",
    0xB5: "up",
    0xB6: "down",
    0xB7: "right",
    0x0A: "enter",
    0x08: "backspace",
    0x09: "tab",
    0xD4: "del",
}
# the driver's modifier flags, kept true while the game has the FIFO: a lost
# release would leave Ctrl on and turn every later letter into a control code
_MODIFIERS = {0xA1: "isAlt", 0xA2: "isShift", 0xA3: "isShift", 0xA5: "isCtrl"}
_NONE = ()


class Pad:
    """Keys for a game loop: poll() once per frame returns the keys pressed
    since the last poll; `held` is the keys down now. Without the PicoCalc
    keyboard (host tests, other firmware) it falls back to poll_key(), and a
    key counts as held for the frame it came in."""

    def __init__(self):
        kbd = _picocalc("keyboard")
        if kbd is None or _terminal() is None or not hasattr(kbd, "read_reg16"):
            kbd = None
        self.kbd = kbd
        self.held = set()
        self.stdin = _stdin_poll() if kbd is not None else None

    def clear(self):
        # before handing the keys back to read_key(): the driver gets the
        # releases from now on
        self.held = set()

    def poll(self):
        tick = _TICK[0]
        if tick is not None:
            tick()  # music playing in the background: its buffers
        kbd = self.kbd
        if kbd is None:
            key = _poll_key()
            if key is None:
                if self.held:
                    self.held = set()
                return _NONE
            self.held = {key}
            return (key,)
        if self.stdin and self.stdin.poll(0):
            _screen_on()  # the REPL that follows needs a screen
            raise _pu.HostTakeover
        try:
            count = kbd.read_reg16(_REG_KEY)[0] & 0x1F
        except Exception:  # keyboard MCU unpowered or busy
            return _NONE
        if not count:
            return _NONE
        _activity()
        presses = []
        held = self.held
        for _ in range(count):
            try:
                event = kbd.read_reg16(_REG_FIF)
            except Exception:
                break
            state = event[0]
            code = event[1]
            mod = _MODIFIERS.get(code)
            if mod is not None:
                setattr(kbd, mod, state != _RELEASED)
                continue
            name = _NAMES.get(code)
            if name is None:
                if code == _pu.POWER_KEY:
                    presses.append("power")  # never released: not held
                    continue
                if not 32 <= code < 127:
                    continue
                name = chr(code).lower()
            if state == _RELEASED:
                held.discard(name)
            elif name not in held:  # a repeat of a held key is not a press
                held.add(name)
                presses.append("quit" if name == "c" and getattr(kbd, "isCtrl", False) else name)
        return presses


# ── sound ───────────────────────────────────────
# Square-wave beeps on the speakers' PWM slice 5 (GP26 left, GP27 right),
# started by beep() and stopped by update() in a later frame: nothing waits.
# Silent while music plays in the background: music.py drives the same slice
# by DMA, and a beep would retune it.

SPEAKERS = (26, 27)
DUTY = 8000


class Sound:
    def __init__(self):
        self.on = bool(option("sound", True))
        self.pwm = None
        self.until = None

    def muted(self):
        if not self.on:
            return True
        music = sys.modules.get("music")
        return music is not None and music.playing() is not None

    def beep(self, freq, ms=40):
        if self.muted():
            return
        try:
            if self.pwm is None:
                from machine import PWM, Pin

                self.pwm = tuple(PWM(Pin(p)) for p in SPEAKERS)
            self.pwm[0].freq(int(freq))
            for pwm in self.pwm:  # the slice's new period: both duties again
                pwm.duty_u16(DUTY)
            self.until = _ticks_add(_ticks_ms(), ms)
        except Exception:
            self.pwm = None

    def update(self):
        if self.until is not None and _ticks_diff(_ticks_ms(), self.until) >= 0:
            self.quiet()

    def quiet(self):
        self.until = None
        for pwm in self.pwm or ():
            try:
                pwm.duty_u16(0)
            except Exception:
                pass

    def tune(self, notes):
        """Play (freq, ms) notes now, waiting: jingles between rounds."""
        if self.muted():
            return
        for freq, ms in notes:
            if freq:
                self.beep(freq, ms)
            _sleep_ms(ms)
            self.quiet()

    def toggle(self):
        self.on = not self.on
        set_option("sound", self.on)
        self.quiet()
        return self.on

    def close(self):
        self.quiet()
        for pwm in self.pwm or ():
            try:
                pwm.deinit()
            except Exception:
                pass
        self.pwm = None


# ── screen ──────────────────────────────────────


class Screen:
    """The framebuffer for a game: gfx.begin() unless the launcher has it
    already. fb is None without a PicoCalc display."""

    def __init__(self):
        self.own = gfx._FB[0] is None and gfx.available()
        if self.own:
            gfx.begin()
        self.fb = gfx._FB[0]

    def close(self):
        if self.own:
            gfx.end()
        elif self.fb is not None:
            gfx.app()  # the console carries on from a clean screen


class Clock:
    """A steady frame rate: wait() sleeps out what is left of the frame."""

    def __init__(self, period_ms):
        self.period = period_ms
        self.next = _ticks_ms()

    def reset(self):
        self.next = _ticks_ms()

    def wait(self):
        self.next = _ticks_add(self.next, self.period)
        left = _ticks_diff(self.next, _ticks_ms())
        if left > 0:
            _sleep_ms(left)
        else:
            self.next = _ticks_ms()  # behind: no catching up in a burst


def title(text, right=b""):
    """The game's title bar: name left, `right` (its score...) right."""
    gfx.fill_rect(0, 0, gfx.WIDTH, gfx.TITLE_H, _pu.BLUE)
    gfx.text(text, gfx.CHAR_W, 2, BWHITE)
    if right:
        right = gfx.cp(right)
        gfx.text(right, gfx.WIDTH - (len(right) + 1) * gfx.CHAR_W, 2, BWHITE)


def _keys_width(pairs):
    return sum((len(k) + len(v) + 3) * gfx.CHAR_W for k, v in pairs) - 2 * gfx.CHAR_W


def box(lines, keys=(), color=BCYAN):
    """A framed box in the middle of the screen: (text, colour) lines, then a
    row of (key, label) hints. Returns its (x, y, w, h)."""
    rows = [(gfx.cp(t), c) for t, c in lines]
    pairs = tuple((gfx.cp(k), gfx.cp(v)) for k, v in keys)
    width = max([len(t) * gfx.CHAR_W for t, _ in rows] + [_keys_width(pairs) if pairs else 0])
    w = min(gfx.WIDTH - 8, width + 32)
    h = len(rows) * 14 + (18 if pairs else 0) + 20
    x = (gfx.WIDTH - w) // 2
    y = (gfx.HEIGHT - h) // 2
    gfx.fill_rect(x, y, w, h, color)
    gfx.fill_rect(x + 2, y + 2, w - 4, h - 4, BLACK)
    ty = y + 12
    for text, c in rows:
        gfx.center(text, ty, c)
        ty += 14
    if pairs:
        gfx.key_bar(pairs, (gfx.WIDTH - _keys_width(pairs)) // 2, ty + 6)
    return x, y, w, h


_RESUME = ("enter", " ", "p", "P")
_LEAVE = ("q", "Q", "esc", "eof", "backspace")


def pause(pad, sound, key=None):
    """The pause box (the power button also switches the screen off). True:
    play on (the caller redraws), False: quit."""
    sound.quiet()
    pad.clear()
    box((("Paused", BYELLOW),), (("Enter", "play"), ("m", "sound"), ("q", "quit")))
    if key == "power":
        _screen_off()  # the key that wakes it does nothing else
    while True:
        try:
            key = _read_key()
        except KeyboardInterrupt:
            return False
        if key in _RESUME:
            return True
        if key in _LEAVE:
            return False
        if key in ("m", "M"):
            sound.toggle()


def game_over(pad, sound, score, best_score, new_best, extra=None):
    """Score box after a game: True plays again, False leaves."""
    pad.clear()
    sound.quiet()
    lines = [("Game over", BRED), ("Score {}".format(score), WHITE)]
    if extra:
        lines.append((extra, GREY))
    if new_best:
        lines.append(("New best!", BGREEN))
    else:
        lines.append(("Best {}".format(best_score), GREY))
    box(lines, (("Enter", "again"), ("q", "back")))
    if new_best:
        sound.tune(((523, 90), (659, 90), (784, 90), (1047, 180)))
    else:
        sound.tune(((392, 120), (330, 120), (262, 220)))
    while True:
        try:
            key = _read_key()
        except KeyboardInterrupt:
            return False
        if key in ("enter", " "):
            return True
        if key in _LEAVE:
            return False


# ── the list ────────────────────────────────────


def play(name):
    """Run one game by module name; it is unloaded afterwards."""
    gc.collect()
    try:
        __import__(name).play()
    finally:
        sys.modules.pop(name, None)
        gc.collect()


def show():
    """The games list: Enter (or 1-3) plays one, q goes back."""
    pos = 0
    try:
        while True:
            labels = [g[1] for g in GAMES]
            hints = []
            for g in GAMES:
                score = best(g[0])
                hints.append("best {}".format(score) if score else g[2])
            key, pos = _pick("Games", labels, hints, (("↑↓", "choose"), ("Enter", "play"), ("q", "back")), pos)
            if key in _BACK:
                return
            if key in ("enter", "right"):
                play(GAMES[pos][0])
    finally:
        flush(True)


def ver():
    print("games:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Games --")
    print("show()  s()   the list: Snake, Tetris, Space Raid")
    print("play('tetris')  one game")
    print("Best scores: /sd/games.json (no card: flash)")


def h():
    return help()


s = show
