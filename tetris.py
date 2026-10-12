"""Tetris: left/right move (held: they repeat), up or x turns, z turns back,
down drops faster, Space drops, c holds a piece for later, p pauses, m turns
the sound off and on, q quits. The best score is kept on the SD card
(games.py)."""

import random

import games
import gfx
from games import Clock as _Clock, Pad as _Pad, Screen as _Screen, Sound as _Sound
from pico_utils import sleep_ms as _sleep_ms
from pico_utils import BLACK, BLUE, BBLUE, BCYAN, BGREEN, BMAGENTA, BRED, BWHITE, BYELLOW
from pico_utils import CYAN, GREEN, GREY, MAGENTA, RED, WHITE, YELLOW

MODULE_VERSION = "2026-10-12.1"

W = 10
H = 22  # 20 rows on screen under 2 hidden ones, where pieces come in
HIDDEN = 2
NEXT = 3  # pieces shown ahead
FRAME_MS = 33
LOCK_MS = 500  # this long on the floor, a piece locks
LOCK_RESETS = 15  # moves and turns on the floor that restart it, per piece
DAS_MS = 170  # a held left/right repeats after this, one column a frame
# ms per row by level, 1-15 (the guideline's (0.8 - (level - 1) * 0.007) ** (level - 1) s)
GRAVITY = (1000, 793, 618, 473, 355, 262, 190, 135, 94, 64, 43, 28, 18, 11, 7)
LINE_SCORES = (0, 100, 300, 500, 800)  # times the level


# ── pieces ──────────────────────────────────────

I, O, T, S, Z, J, L = 1, 2, 3, 4, 5, 6, 7
_SPAWN = (  # box side, cells in the spawn state (x right, y down)
    (4, ((0, 1), (1, 1), (2, 1), (3, 1))),
    (2, ((0, 0), (1, 0), (0, 1), (1, 1))),
    (3, ((1, 0), (0, 1), (1, 1), (2, 1))),
    (3, ((1, 0), (2, 0), (0, 1), (1, 1))),
    (3, ((0, 0), (1, 0), (1, 1), (2, 1))),
    (3, ((0, 0), (0, 1), (1, 1), (2, 1))),
    (3, ((2, 0), (0, 1), (1, 1), (2, 1))),
)


def _turns(n, cells):
    # the four states 0, R, 2, L: each a quarter turn clockwise in the box
    states = [tuple(cells)]
    for _ in range(3):
        states.append(tuple((n - 1 - y, x) for x, y in states[-1]))
    return tuple(states)


SHAPES = (None,) + tuple(_turns(n, cells) for n, cells in _SPAWN)
SIZES = (0,) + tuple(n for n, _ in _SPAWN)

# SRS wall kicks for a clockwise turn from state 0, R, 2 and L, as published
# (x right, y up); a turn back from state s tries those of s - 1, negated
_KICKS = (
    ((0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)),
    ((0, 0), (1, 0), (1, -1), (0, 2), (1, 2)),
    ((0, 0), (1, 0), (1, 1), (0, -2), (1, -2)),
    ((0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)),
)
_KICKS_I = (
    ((0, 0), (-2, 0), (1, 0), (-2, -1), (1, 2)),
    ((0, 0), (-1, 0), (2, 0), (-1, 2), (2, -1)),
    ((0, 0), (2, 0), (-1, 0), (2, 1), (-1, -2)),
    ((0, 0), (1, 0), (-2, 0), (1, -2), (-2, 1)),
)


# ── game logic (no screen) ──────────────────────


class Game:
    """One game: the board (rows of piece numbers, 0 empty), the falling
    piece (piece, rot, x, y: its box's top left), score, lines, level."""

    def __init__(self, rng=random, level=1):
        self.rng = rng
        self.board = [bytearray(W) for _ in range(H)]
        self.bag = []
        self.queue = []
        self.hold = 0
        self.held = False  # the hold was used for this piece
        self.score = 0
        self.lines = 0
        self.start = level
        self.level = level
        self.over = False
        self.locked = (0, 0)  # rows of the last piece that locked
        self.piece = self.rot = self.x = self.y = 0
        self.fall = self.lock_ms = self.resets = 0
        self.spawn()

    def _refill(self):
        # the next pieces, from bags of all seven in a shuffled order
        while len(self.queue) < NEXT:
            if not self.bag:
                bag = [I, O, T, S, Z, J, L]
                for i in range(6, 0, -1):
                    j = self.rng.randint(0, i)
                    bag[i], bag[j] = bag[j], bag[i]
                self.bag = bag
            self.queue.append(self.bag.pop())

    def spawn(self, piece=0):
        """The next piece (or `piece`) at the top; False and game over when
        it has no room."""
        if not piece:
            self._refill()
            piece = self.queue.pop(0)
            self._refill()
        self.piece = piece
        self.rot = 0
        self.x = (W - SIZES[piece]) // 2
        self.y = HIDDEN - 1  # its lowest row on the first visible one
        self.fall = self.lock_ms = self.resets = 0
        self.held = False
        if not self.fits(piece, 0, self.x, self.y):
            self.over = True
            return False
        return True

    def fits(self, piece, rot, x, y):
        board = self.board
        for cx, cy in SHAPES[piece][rot]:
            cx += x
            cy += y
            if cx < 0 or cx >= W or cy < 0 or cy >= H or board[cy][cx]:
                return False
        return True

    def landed(self):
        return not self.fits(self.piece, self.rot, self.x, self.y + 1)

    def ghost_y(self):
        """Where a drop would put the piece."""
        y = self.y
        while self.fits(self.piece, self.rot, self.x, y + 1):
            y += 1
        return y

    def _moved(self):
        # a move or a turn on the floor starts the lock delay again, so often
        if self.lock_ms and self.resets < LOCK_RESETS:
            self.lock_ms = 0
            self.resets += 1

    def shift(self, dx):
        if self.over or not self.fits(self.piece, self.rot, self.x + dx, self.y):
            return False
        self.x += dx
        self._moved()
        return True

    def turn(self, d=1):
        """A quarter turn, 1 clockwise or -1 back, kicked off walls and
        blocks the SRS way. True if it turned."""
        piece = self.piece
        if self.over or piece == O:
            return False
        new = (self.rot + d) % 4
        kicks = (_KICKS_I if piece == I else _KICKS)[self.rot if d == 1 else new]
        for kx, ky in kicks:
            if d != 1:
                kx = -kx
                ky = -ky
            x = self.x + kx
            y = self.y - ky
            if self.fits(piece, new, x, y):
                self.x = x
                self.y = y
                self.rot = new
                self._moved()
                return True
        return False

    def soft_drop(self):
        """One row down now, a point for it; False on the floor."""
        if self.over or self.landed():
            return False
        self.y += 1
        self.fall = 0
        self.score += 1
        return True

    def hard_drop(self):
        """Straight down, two points a row, and lock: the rows it cleared."""
        if self.over:
            return []
        y = self.ghost_y()
        self.score += 2 * (y - self.y)
        self.y = y
        return self.lock()

    def lock(self):
        """The piece joins the board, full rows go: their numbers (counted
        before they went), then the next piece comes."""
        piece = self.piece
        board = self.board
        rows = [self.y + cy for _, cy in SHAPES[piece][self.rot]]
        for cx, cy in SHAPES[piece][self.rot]:
            board[self.y + cy][self.x + cx] = piece
        self.locked = (min(rows), max(rows))
        full = [r for r in range(H) if min(board[r])]
        if full:
            kept = [row for row in board if not min(row)]
            self.board = [bytearray(W) for _ in full] + kept
            self.lines += len(full)
            self.score += LINE_SCORES[len(full)] * self.level
            self.level = max(self.start, 1 + self.lines // 10)
        self.spawn()
        return full

    def hold_piece(self):
        """Put the piece aside and take the one held before (the next one the
        first time); once per piece."""
        if self.over or self.held:
            return False
        current = self.piece
        self.spawn(self.hold)
        self.hold = current
        self.held = True
        return True

    def tick(self, ms):
        """ms of play: the fall, then on the floor the lock delay. The rows a
        lock cleared (a list, maybe empty), or None when nothing locked."""
        if self.over:
            return None
        if self.landed():
            self.fall = 0
            self.lock_ms += ms
            if self.lock_ms >= LOCK_MS:
                return self.lock()
            return None
        self.lock_ms = 0
        self.fall += ms
        step = GRAVITY[min(self.level, len(GRAVITY)) - 1]
        while self.fall >= step:
            self.fall -= step
            self.y += 1
            if self.landed():
                self.fall = 0
                break
        return None


# ── screen ──────────────────────────────────────
# Straight into the framebuffer: a cell is two or three fill_rect() calls,
# and only what changed is drawn again (the piece when it moves, rows when
# a piece locks, the side panels when their numbers change).

CELL = 14
BX = (320 - W * CELL) // 2  # board left
BY = 24  # board top (the first visible row)
MINI = 8  # preview cells
_COLORS = (
    None,
    (BCYAN, CYAN),
    (BYELLOW, YELLOW),
    (BMAGENTA, MAGENTA),
    (BGREEN, GREEN),
    (BRED, RED),
    (BBLUE, BLUE),
    (WHITE, GREY),
)
_LEFT = 8  # side panels' x
_RIGHT = BX + W * CELL + 14
_KEY_PAIRS = tuple(
    (gfx.cp(k), gfx.cp(v))
    for k, v in (("←→", "move"), ("↑ x", "turn"), ("z", "back"), ("↓", "down"), ("Spc", "drop"), ("c", "hold"), ("p", "pause"))
)


def _block(fb, x, y, piece, size=CELL):
    bright, dark = _COLORS[piece]
    fb.fill_rect(x, y, size - 1, size - 1, dark)
    fb.fill_rect(x, y, size - 2, size - 2, bright)
    if size == CELL:
        fb.fill_rect(x + 2, y + 2, 2, 2, BWHITE)


def _ghost(fb, x, y):
    fb.fill_rect(x, y, CELL - 1, CELL - 1, GREY)
    fb.fill_rect(x + 1, y + 1, CELL - 3, CELL - 3, BLACK)


def _mini(fb, piece, x, y, dim=False):
    # a piece in its spawn state, cells of MINI pixels, in a 4x2 cell area
    fb.fill_rect(x, y, 4 * MINI, 2 * MINI, BLACK)
    if not piece:
        return
    cells = SHAPES[piece][0]
    left = min(cx for cx, _ in cells)
    top = min(cy for _, cy in cells)
    width = max(cx for cx, _ in cells) - left + 1
    x += (4 - width) * MINI // 2
    for cx, cy in cells:
        px = x + (cx - left) * MINI
        py = y + (cy - top) * MINI
        if dim:
            fb.fill_rect(px, py, MINI - 1, MINI - 1, GREY)
        else:
            _block(fb, px, py, piece, MINI)


class View:
    def __init__(self, fb):
        self.fb = fb
        self.shown = None  # screen cells of the piece and its ghost as drawn
        self.key = None  # (piece, rot, x, y) as drawn
        self.stats = None
        self.hold = None
        self.queue = None

    def draw_all(self, g, best):
        fb = self.fb
        fb.fill_rect(0, 12, 320, 308, BLACK)
        games.title("Tetris")
        right = BX + W * CELL
        bottom = BY + (H - HIDDEN) * CELL
        fb.fill_rect(BX - 4, BY - 4, W * CELL + 7, 2, GREY)
        fb.fill_rect(BX - 4, bottom + 1, W * CELL + 7, 2, GREY)
        fb.fill_rect(BX - 4, BY - 4, 2, bottom - BY + 7, GREY)
        fb.fill_rect(right + 1, BY - 4, 2, bottom - BY + 7, GREY)
        for r in range(HIDDEN, H):
            self.row(g, r)
        for label, y in (("HOLD", 26), ("SCORE", 84), ("LEVEL", 118), ("LINES", 152), ("BEST", 186)):
            fb.text(label, _LEFT, y, GREY)
        fb.text("NEXT", _RIGHT, 26, GREY)
        y = 214
        for pair in _KEY_PAIRS:
            gfx.key_bar((pair,), _RIGHT - 6, y)
            y += 13
        self.shown = self.key = self.stats = self.hold = self.queue = None
        self.panel(g, best)
        self.piece(g)

    def row(self, g, r):
        fb = self.fb
        y = BY + (r - HIDDEN) * CELL
        cells = g.board[r]
        x = BX
        for c in range(W):
            piece = cells[c]
            if piece:
                _block(fb, x, y, piece)
            else:
                fb.fill_rect(x, y, CELL - 1, CELL - 1, BLACK)
            x += CELL

    def _erase(self):
        if self.shown:
            fb = self.fb
            for x, y in self.shown:
                fb.fill_rect(x, y, CELL - 1, CELL - 1, BLACK)
        self.shown = self.key = None

    def piece(self, g):
        if g.over:
            return
        key = (g.piece, g.rot, g.x, g.y)
        if key == self.key:
            return
        self._erase()
        fb = self.fb
        cells = SHAPES[g.piece][g.rot]
        shown = []
        for top, ghost in ((g.ghost_y(), True), (g.y, False)):
            for cx, cy in cells:
                r = top + cy
                if r < HIDDEN:
                    continue
                x = BX + (g.x + cx) * CELL
                y = BY + (r - HIDDEN) * CELL
                if ghost:
                    _ghost(fb, x, y)
                else:
                    _block(fb, x, y, g.piece)
                shown.append((x, y))
        self.shown = shown
        self.key = key

    def locked(self, g, rows, sound):
        """After a lock: the rows it filled, or the cleared ones flashed and
        the board again."""
        self._erase()
        fb = self.fb
        if rows:
            for r in rows:
                if r >= HIDDEN:
                    fb.fill_rect(BX, BY + (r - HIDDEN) * CELL, W * CELL - 1, CELL - 1, BWHITE)
            sound.beep(1568 if len(rows) == 4 else 880 + 110 * len(rows), 90)
            _sleep_ms(90)
            sound.quiet()
            for r in range(HIDDEN, H):
                self.row(g, r)
        else:
            sound.beep(196, 25)
            for r in range(max(HIDDEN, g.locked[0]), g.locked[1] + 1):
                self.row(g, r)

    def panel(self, g, best):
        fb = self.fb
        stats = (g.score, g.level, g.lines, max(best, g.score))
        if stats != self.stats:
            for value, y in zip(stats, (96, 130, 164, 198)):
                fb.fill_rect(_LEFT, y, BX - _LEFT - 6, 8, BLACK)
                fb.text(str(value), _LEFT, y, BWHITE)
            self.stats = stats
        if self.hold != (g.hold, g.held):
            _mini(fb, g.hold, _LEFT, 40, g.held)
            self.hold = (g.hold, g.held)
        if self.queue != g.queue:
            for i, piece in enumerate(g.queue):
                _mini(fb, piece, _RIGHT, 40 + i * 28)
            self.queue = list(g.queue)


# ── playing ─────────────────────────────────────


def _round(g, view, pad, sound, best):
    # One game; False if it was left before the end.
    view.draw_all(g, best)
    clock = _Clock(FRAME_MS)
    das = None  # [key, dx, ms held] while left or right is held
    while not g.over:
        for key in pad.poll():
            if key in ("left", "right"):
                dx = -1 if key == "left" else 1
                g.shift(dx)
                das = [key, dx, 0]
            elif key in ("up", "x"):
                g.turn(1)
            elif key == "z":
                g.turn(-1)
            elif key == " ":
                view.locked(g, g.hard_drop(), sound)
            elif key == "c":
                g.hold_piece()
            elif key == "m":
                sound.toggle()
            elif key == "quit":
                return False
            elif key in ("p", "esc", "q", "power"):
                if not games.pause(pad, sound, key):
                    return False
                view.draw_all(g, best)
                clock.reset()
                das = None
        if g.over:
            break
        if das is not None:
            if das[0] in pad.held:
                das[2] += FRAME_MS
                if das[2] >= DAS_MS:
                    g.shift(das[1])
            else:
                das = None
        if "down" in pad.held:
            g.soft_drop()
        rows = g.tick(FRAME_MS)
        if rows is not None:
            level = g.level
            view.locked(g, rows, sound)
            if g.level != level:
                sound.beep(1319, 120)
        view.piece(g)
        view.panel(g, best)
        sound.update()
        clock.wait()
    return True


def play(level=1):
    """Games until q; returns the best score."""
    screen = _Screen()
    if screen.fb is None:
        print("Tetris draws on the PicoCalc screen.")
        return games.best("tetris")
    pad = _Pad()
    sound = _Sound()
    best = games.best("tetris")
    try:
        while True:
            g = Game(level=level)
            finished = _round(g, View(screen.fb), pad, sound, best)
            new = games.record("tetris", g.score)
            best = max(best, g.score)
            games.flush()
            if not finished:
                break
            extra = "Lines {}  Level {}".format(g.lines, g.level)
            if not games.game_over(pad, sound, g.score, best, new, extra):
                break
    finally:
        sound.close()
        screen.close()
        games.flush(True)
    return best


# ── standard ────────────────────────────────────


def ver():
    print("tetris:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Tetris --")
    print("play()  p()   left/right move, up/x turn, z back,")
    print("              down faster, Space drop, c hold,")
    print("              p pause, m sound, q quit")
    print("Best score: /sd/games.json")


def h():
    return help()


p = play
