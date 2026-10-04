"""Snake for the PicoCalc keyboard: arrows or WASD steer, p pauses, m turns
the sound off and on, q quits. The best score is kept in snake.json."""

import random

from pico_utils import DISPLAY_WIDTH, PAGE_LINES
from pico_utils import clear_screen as _clear_screen, load_json as _load_json
from pico_utils import paint as _paint, poll_key as _poll_key, read_key as _read_key
from pico_utils import save_json as _save_json, sleep_ms as _sleep_ms
from pico_utils import ticks_add as _ticks_add, ticks_diff as _ticks_diff
from pico_utils import ticks_ms as _ticks_ms, title_bar as _title_bar
from pico_utils import BGREEN, BRED, BYELLOW, GREEN, GREY


MODULE_VERSION = "2026-10-04.1"
CONFIG_FILE = "snake.json"
START_MS = 150  # time per step at the start
FASTEST_MS = 60
SPEEDUP_MS = 4  # per food eaten
SPEAKER_PIN = 26
_TOP = 3  # screen row of field row 0 (title bar, wall)
_BODY = "█"
_FOOD = "■"
_QUIT = ("q", "Q", "esc", "eof")
_HEADINGS = {
    "up": (0, -1), "w": (0, -1), "W": (0, -1),
    "down": (0, 1), "s": (0, 1), "S": (0, 1),
    "left": (-1, 0), "a": (-1, 0), "A": (-1, 0),
    "right": (1, 0), "d": (1, 0), "D": (1, 0),
}


# ── game logic (no screen) ──────────────────────


def new_game(width, height, rng=random):
    """A three-cell snake in the middle heading right, and some food."""
    x, y = width // 2, height // 2
    snake = [(x - 2, y), (x - 1, y), (x, y)]  # tail first
    state = {
        "w": width,
        "h": height,
        "snake": snake,
        "cells": set(snake),
        "dir": (1, 0),
        "turns": [],
        "score": 0,
        "food": None,
    }
    place_food(state, rng)
    return state


def turn(state, heading):
    """Queue a turn; two fit, so a quick up-then-left lands within a step."""
    if len(state["turns"]) < 2:
        state["turns"].append(heading)


def place_food(state, rng=random):
    """Food on a free cell; None when the snake fills the field."""
    cells = state["cells"]
    width, height = state["w"], state["h"]
    state["food"] = None
    if len(cells) >= width * height:
        return None
    for _ in range(50):
        cell = (rng.randint(0, width - 1), rng.randint(0, height - 1))
        if cell not in cells:
            state["food"] = cell
            return cell
    for y in range(height):  # crowded field: the first free cell
        for x in range(width):
            if (x, y) not in cells:
                state["food"] = (x, y)
                return state["food"]
    return None


def step(state):
    """Move one cell: "dead" (wall or itself), "ate" or "moved". The cell
    the tail is leaving is free to enter."""
    dx, dy = state["dir"]
    turns = state["turns"]
    while turns:
        nx, ny = turns.pop(0)
        if (nx, ny) != (dx, dy) and (nx, ny) != (-dx, -dy):
            dx, dy = nx, ny
            break
    state["dir"] = (dx, dy)
    snake = state["snake"]
    cells = state["cells"]
    head = (snake[-1][0] + dx, snake[-1][1] + dy)
    if not (0 <= head[0] < state["w"] and 0 <= head[1] < state["h"]):
        return "dead"
    if head in cells and head != snake[0]:
        return "dead"
    ate = head == state["food"]
    if not ate:
        cells.discard(snake.pop(0))
    snake.append(head)
    cells.add(head)
    if ate:
        state["score"] += 1
        return "ate"
    return "moved"


# ── screen ──────────────────────────────────────


def _cell(cell, text):
    return "\x1b[{};{}H{}".format(_TOP + cell[1], 2 + cell[0], text)


def _at(cell, text):
    print(_cell(cell, text), end="")


def _status(state, best, sound, text=None):
    row = _TOP + state["h"] + 1
    if text is None:
        score = state["score"]
        text = "Score {}  Best {}   ".format(score, max(best, score)) + "  ".join(
            _paint(key, BYELLOW) + " " + label
            for key, label in (("p", "pause"), ("m", "sound " + ("on" if sound else "off")), ("q", "quit"))
        )
    print("\x1b[{};1H\x1b[K{}\x1b[{};1H".format(row, text, row + 1), end="")


def _draw_board(state, best, sound):
    # Walls by cursor moves, not lines of spaces: the screen is clear and
    # the terminal draws every character it gets.
    width, height = state["w"], state["h"]
    side = _paint("\u2551", GREY)
    out = [_title_bar("Snake"), "\n", _paint("\u2554" + "\u2550" * width + "\u2557", GREY)]
    for y in range(_TOP, _TOP + height):
        out.append("\x1b[{};1H{}\x1b[{};{}H{}".format(y, side, y, width + 2, side))
    out.append("\x1b[{};1H".format(_TOP + height) + _paint("\u255a" + "\u2550" * width + "\u255d", GREY))
    for cell in state["snake"][:-1]:
        out.append(_cell(cell, _paint(_BODY, GREEN)))
    out.append(_cell(state["snake"][-1], _paint(_BODY, BGREEN)))
    if state["food"] is not None:
        out.append(_cell(state["food"], _paint(_FOOD, BRED)))
    _clear_screen()
    print("\x1b[?25l" + "".join(out), end="")  # no blinking cursor on the board
    _status(state, best, sound)


def _blip():
    try:
        from machine import PWM, Pin

        pwm = PWM(Pin(SPEAKER_PIN))
        pwm.freq(1320)
        pwm.duty_u16(8000)
        _sleep_ms(15)
        pwm.duty_u16(0)
        pwm.deinit()
    except Exception:
        pass


def _game(width, height, best, sound):
    # One game: returns (score, sound, quit).
    state = new_game(width, height)
    _draw_board(state, best, sound)
    _park = "\x1b[{};1H".format(_TOP + height + 2)
    delay = START_MS
    due = _ticks_add(_ticks_ms(), delay)
    while True:
        key = _poll_key()
        while key is not None:
            if key in _HEADINGS:
                turn(state, _HEADINGS[key])
            elif key in _QUIT:
                return state["score"], sound, True
            elif key in ("p", "P", " "):
                _status(state, best, sound, "Paused: any key")
                _read_key()
                _status(state, best, sound)
                due = _ticks_add(_ticks_ms(), delay)
            elif key in ("m", "M"):
                sound = not sound
                _status(state, best, sound)
            key = _poll_key()
        if _ticks_diff(due, _ticks_ms()) > 0:
            _sleep_ms(5)
            continue
        due = _ticks_add(_ticks_ms(), delay)
        tail, old_head = state["snake"][0], state["snake"][-1]
        result = step(state)
        if result == "dead":
            _at(old_head, _paint("X", BRED))
            return state["score"], sound, False
        # One write per step; the cursor parks under the field.
        out = _cell(tail, " ") if result == "moved" else ""
        out += _cell(old_head, _paint(_BODY, GREEN)) + _cell(state["snake"][-1], _paint(_BODY, BGREEN))
        print(out + _park, end="")
        if result == "ate":
            delay = max(FASTEST_MS, delay - SPEEDUP_MS)
            if sound:
                _blip()
            if place_food(state) is None:  # the snake fills the field
                return state["score"], sound, False
            _at(state["food"], _paint(_FOOD, BRED))
            _status(state, best, sound)


def play():
    """Games until q; returns the best score."""
    config = _load_json(CONFIG_FILE) or {}
    try:
        best = int(config.get("best") or 0)
    except (TypeError, ValueError):
        best = 0
    sound = bool(config.get("sound", True))
    saved = (best, sound)
    width = max(10, DISPLAY_WIDTH - 2)
    height = max(5, PAGE_LINES - 2)
    try:
        while True:
            score, sound, quit = _game(width, height, best, sound)
            record = score > best
            best = max(best, score)
            if quit:
                break
            done = "New best: {}!".format(score) if record else "Game over: {}.".format(score)
            row = _TOP + height + 1
            print(
                "\x1b[{};1H\x1b[K{}  {} again  {} back".format(
                    row, _paint(done, BGREEN if record else BRED), _paint("Enter", BYELLOW), _paint("q", BYELLOW)
                ),
                end="",
            )
            key = None
            while key not in ("enter", " ") and key not in _QUIT:
                key = _read_key()
            if key in _QUIT:
                break
    except KeyboardInterrupt:
        pass
    finally:
        if (best, sound) != saved:
            _save_json(CONFIG_FILE, {"best": best, "sound": sound})
        _clear_screen()
        print("\x1b[?25h", end="")
    return best


# ── standard ────────────────────────────────────


def ver():
    print("snake:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Snake --")
    print("play()  p()  Arrows/WASD steer, p pause,")
    print("             m sound, q quit")
    print("Best score: snake.json")


def h():
    return help()


p = play
