"""Snake for the PicoCalc keyboard: arrows or WASD steer, p pauses, m turns
the sound off and on, q quits. The best score is kept on the SD card
(games.py): the old snake.json on flash is read once, never written."""

import random

import games
from games import Pad as _Pad, Screen as _Screen, Sound as _Sound
from pico_utils import sleep_ms as _sleep_ms, ticks_add as _ticks_add
from pico_utils import ticks_diff as _ticks_diff, ticks_ms as _ticks_ms
from pico_utils import BLACK, BGREEN, BRED, BWHITE, BYELLOW, GREEN, GREY, WHITE


MODULE_VERSION = "2026-10-12.1"
START_MS = 150  # time per step at the start
FASTEST_MS = 60
SPEEDUP_MS = 4  # per food eaten
POLL_MS = 20  # keys are read this often between steps
WIDTH = 30  # field, in cells
HEIGHT = 28
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
    """Queue a turn; two fit, so a quick up-then-left lands within a step.
    A press along the last queued heading (or the current one), or back on
    it, changes nothing and takes no slot."""
    turns = state["turns"]
    dx, dy = turns[-1] if turns else state["dir"]
    if heading in ((dx, dy), (-dx, -dy)):
        return
    if len(turns) < 2:
        turns.append(heading)


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
# Straight into the framebuffer: a step erases the tail's cell and draws two
# others (~60 us), where the screen console took a cursor move and a coloured
# character for each.

CELL = 10
X0 = (320 - WIDTH * CELL) // 2
Y0 = 22
_STATUS_Y = Y0 + HEIGHT * CELL + 7


def _xy(cell):
    return X0 + cell[0] * CELL, Y0 + cell[1] * CELL


def _body(fb, cell):
    x, y = _xy(cell)
    fb.fill_rect(x + 1, y + 1, CELL - 2, CELL - 2, GREEN)


def _head(fb, cell, heading):
    x, y = _xy(cell)
    fb.fill_rect(x + 1, y + 1, CELL - 2, CELL - 2, BGREEN)
    # an eye on the side it looks to
    ex = x + (6 if heading[0] > 0 else 2 if heading[0] < 0 else 3)
    ey = y + (6 if heading[1] > 0 else 2 if heading[1] < 0 else 3)
    fb.fill_rect(ex, ey, 2, 2, BLACK)


def _food(fb, cell):
    x, y = _xy(cell)
    fb.fill_rect(x + 2, y + 2, CELL - 4, CELL - 3, BRED)
    fb.fill_rect(x + 1, y + 3, CELL - 2, CELL - 5, BRED)
    fb.fill_rect(x + 5, y, 2, 2, BGREEN)


def _erase(fb, cell):
    x, y = _xy(cell)
    fb.fill_rect(x, y, CELL, CELL, BLACK)


def _status(fb, score, best):
    fb.fill_rect(0, _STATUS_Y, 170, 8, BLACK)
    x = games.gfx.text("Score {}".format(score), X0, _STATUS_Y, BWHITE)
    games.gfx.text("Best {}".format(max(best, score)), x + 18, _STATUS_Y, GREY)


def _draw(fb, state, best):
    fb.fill_rect(0, 12, 320, 308, BLACK)
    games.title("Snake")
    w = WIDTH * CELL
    h = HEIGHT * CELL
    fb.fill_rect(X0 - 4, Y0 - 4, w + 8, 2, GREY)
    fb.fill_rect(X0 - 4, Y0 + h + 2, w + 8, 2, GREY)
    fb.fill_rect(X0 - 4, Y0 - 4, 2, h + 8, GREY)
    fb.fill_rect(X0 + w + 2, Y0 - 4, 2, h + 8, GREY)
    for cell in state["snake"][:-1]:
        _body(fb, cell)
    _head(fb, state["snake"][-1], state["dir"])
    if state["food"] is not None:
        _food(fb, state["food"])
    _status(fb, state["score"], best)
    games.gfx.key_bar(_KEYS, 180, _STATUS_Y)


_KEYS = tuple((games.gfx.cp(k), games.gfx.cp(v)) for k, v in (("p", "pause"), ("m", "sound"), ("q", "quit")))


def _crash(fb, cell):
    x, y = _xy(cell)
    fb.fill_rect(x, y, CELL, CELL, BRED)
    fb.line(x + 2, y + 2, x + CELL - 3, y + CELL - 3, BWHITE)
    fb.line(x + CELL - 3, y + 2, x + 2, y + CELL - 3, BWHITE)


def _game(state, fb, pad, sound, best):
    # One game: (score, True at the end, False when it was left).
    _draw(fb, state, best)
    delay = START_MS
    due = _ticks_add(_ticks_ms(), delay)
    while True:
        for key in pad.poll():
            if key in _HEADINGS:
                turn(state, _HEADINGS[key])
            elif key in ("m", "M"):
                sound.toggle()
            elif key == "quit":
                return state["score"], False
            elif key in ("p", "P", " ", "esc", "q", "Q", "power"):
                if not games.pause(pad, sound, key):
                    return state["score"], False
                _draw(fb, state, best)
                due = _ticks_add(_ticks_ms(), delay)
        if _ticks_diff(_ticks_ms(), due) >= 0:
            due = _ticks_add(_ticks_ms(), delay)
            tail, old_head = state["snake"][0], state["snake"][-1]
            result = step(state)
            if result == "dead":
                _crash(fb, old_head)
                sound.beep(98, 300)
                _sleep_ms(500)
                return state["score"], True
            if result == "moved":
                _erase(fb, tail)
            _body(fb, old_head)
            _head(fb, state["snake"][-1], state["dir"])
            if result == "ate":
                delay = max(FASTEST_MS, delay - SPEEDUP_MS)
                sound.beep(1320, 25)
                _status(fb, state["score"], best)
                if place_food(state) is None:  # the snake fills the field
                    return state["score"], True
                _food(fb, state["food"])
        sound.update()
        wait = min(POLL_MS, _ticks_diff(due, _ticks_ms()))
        if wait > 0:
            _sleep_ms(wait)


def play():
    """Games until q; returns the best score."""
    screen = _Screen()
    if screen.fb is None:
        print("Snake draws on the PicoCalc screen.")
        return games.best("snake")
    pad = _Pad()
    sound = _Sound()
    best = games.best("snake")
    try:
        while True:
            score, finished = _game(new_game(WIDTH, HEIGHT), screen.fb, pad, sound, best)
            new = games.record("snake", score)
            best = max(best, score)
            games.flush()
            if not finished or not games.game_over(pad, sound, score, best, new):
                break
    finally:
        sound.close()
        screen.close()
        games.flush(True)
    return best


# ── standard ────────────────────────────────────


def ver():
    print("snake:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Snake --")
    print("play()  p()  Arrows/WASD steer, p pause,")
    print("             m sound, q quit")
    print("Best score: /sd/games.json")


def h():
    return help()


p = play
