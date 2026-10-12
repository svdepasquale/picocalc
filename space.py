"""Space Raid: a side-scrolling shooter after the old Nokia's Space Impact.
Arrows fly the ship, Space (or x, z) fires while held, c (or v, Enter)
fires the special weapon the pods give: missiles or a beam. A boss closes
every level. p pauses, n switches the Nokia LCD look, m the sound, q quits.
The best score is kept on the SD card (games.py)."""

import random

import games
import gfx
from games import Clock as _Clock, Pad as _Pad, Screen as _Screen, Sound as _Sound
from pico_utils import sleep_ms as _sleep_ms
from pico_utils import BLACK, BLUE, BBLUE, BCYAN, BGREEN, BMAGENTA, BRED, BWHITE, BYELLOW
from pico_utils import GREY, WHITE, YELLOW

try:
    import framebuf
except ImportError:  # CPython: the game logic only
    framebuf = None

MODULE_VERSION = "2026-10-12.1"

S = 3  # screen pixels per game pixel: the Nokia's chunky look
FW = 106  # field, in game pixels
FH = 102
FIELD_Y = 14  # screen row of the field's top
BAND = 51  # the field is drawn in bands of this many screen rows, 8 KB each
FRAME_MS = 40
LIVES = 3
SHOT_EVERY = 5  # frames between shots while fire is held
SHOTS_MAX = 6
INVULNERABLE = 75  # frames after a new ship comes in
ITEM_EVERY = 8  # a pod every so many kills
AMMO = 3  # specials a pod gives

FIRE = (" ", "x", "z", "f")
SPECIAL = ("c", "v", "enter")


# ── sprites ─────────────────────────────────────
# '.' is clear; other characters are colours (the table after each).

SHIP = (
    "###.......",
    ".####.....",
    "..######..",
    ".###oo####",
    "..######..",
    ".####.....",
    "###.......",
)
DRONE, WAVE, DART, GUNNER, DIVER, ROCK = range(6)
FOE_ART = (
    ("..###..", ".#####.", "##o#o##", "#######", ".#.#.#.", "#.#.#.#"),
    ("...##...", ".######.", "#o#oo#o#", ".######.", "..#..#.."),
    ("....####", "########", "....####"),
    ("....####", "..######", "###oo###", "########", "###oo###", "..######", "....####"),
    ("...#...", "..###..", ".##o##.", "###o###", ".##o##.", "..###..", "...#..."),
    ("..####..", ".#o#####", "########", "###.####", "#####o##", "########", ".######.", "..####.."),
)
FOE_COLORS = (
    (BGREEN, BYELLOW),
    (BMAGENTA, WHITE),
    (BYELLOW, BYELLOW),
    (BRED, BYELLOW),
    (BBLUE, WHITE),
    (GREY, WHITE),
)
# hp, points, speed: game pixels a frame, 0 for one every other frame
FOE_STATS = (
    (1, 10, 1),
    (1, 15, 1),
    (1, 20, 2),
    (3, 30, 0),
    (2, 25, 1),
    (6, 40, 0),
)
BOSS_ART = (
    (
        ".....######.....",
        "...##########...",
        "..####oooo####..",
        ".##############.",
        "######....######",
        "#####......#####",
        "#####......#####",
        "######....######",
        ".##############.",
        "..####oooo####..",
        "...##########...",
        ".....######.....",
    ),
    (
        "##............##",
        ".##..######..##.",
        "..############..",
        "...##oo##oo##...",
        "################",
        "#####......#####",
        "################",
        "...##oo##oo##...",
        "..############..",
        ".##..######..##.",
        "##............##",
    ),
    (
        "......####......",
        "....########....",
        "..####o##o####..",
        "################",
        "###..######..###",
        "##....####....##",
        "###..######..###",
        "################",
        "..####o##o####..",
        "....########....",
        "......####......",
    ),
)
BOSS_COLORS = ((BRED, BYELLOW), (BMAGENTA, BCYAN), (BGREEN, BYELLOW))
BLAST_ART = (
    (".#.#.", "#.#.#", ".###.", "#.#.#", ".#.#."),
    ("#..#..#", ".#.#.#.", "..#.#..", "##...##", "..#.#..", ".#.#.#.", "#..#..#"),
)

# Waves per level: (frame, kind, count, y, dy, gap): `count` foes of a kind,
# the first at height y and each next dy lower, one every `gap` frames. After
# the last one has gone, the boss. Level 4 is level 1 again, tougher.
LEVELS = (
    (
        (0, DRONE, 5, 20, 0, 14),
        (100, DRONE, 5, 70, 0, 14),
        (200, WAVE, 6, 45, 0, 12),
        (320, DART, 4, 12, 22, 8),
        (420, DRONE, 6, 8, 14, 10),
        (560, WAVE, 5, 25, 0, 12),
        (560, WAVE, 5, 70, 0, 12),
        (720, GUNNER, 3, 20, 28, 30),
        (850, DIVER, 4, 45, 0, 22),
        (980, ROCK, 2, 25, 40, 50),
        (1100, DART, 6, 4, 16, 6),
    ),
    (
        (0, WAVE, 6, 30, 0, 10),
        (90, DIVER, 4, 60, 0, 18),
        (200, GUNNER, 3, 10, 35, 25),
        (300, DART, 8, 4, 12, 5),
        (420, DRONE, 8, 15, 9, 8),
        (520, ROCK, 3, 10, 30, 30),
        (640, WAVE, 6, 20, 0, 10),
        (640, WAVE, 6, 75, 0, 10),
        (780, GUNNER, 4, 15, 22, 20),
        (900, DIVER, 6, 45, 0, 14),
        (1020, DART, 8, 90, -11, 5),
    ),
    (
        (0, DART, 6, 10, 15, 6),
        (60, DRONE, 8, 80, -9, 8),
        (160, GUNNER, 4, 12, 24, 18),
        (260, DIVER, 6, 45, 0, 12),
        (380, ROCK, 4, 5, 26, 22),
        (480, WAVE, 8, 50, 0, 8),
        (560, GUNNER, 5, 8, 20, 16),
        (680, DART, 10, 4, 10, 4),
        (760, DIVER, 6, 20, 10, 12),
        (880, ROCK, 3, 30, 25, 25),
        (960, WAVE, 8, 30, 0, 8),
        (960, DRONE, 8, 75, 0, 8),
    ),
)
# up and down for the wave foes, in game pixels
SINE = (0, 2, 4, 6, 7, 8, 9, 10, 10, 10, 9, 8, 7, 6, 4, 2, 0, -2, -4, -6, -7, -8, -9, -10, -10, -10, -9, -8, -7, -6, -4, -2)

# events of a frame, for the sound (SHOT: a special weapon fired)
SHOT, HIT, KILL, HURT, POD, BOSS, CLEAR = 1, 2, 4, 8, 16, 32, 64


def _size(art):
    return len(art[0]), len(art)


class Foe:
    def __init__(self, kind, x, y, hp, w, h):
        self.kind = kind
        self.x = x
        self.y = y
        self.base = y
        self.hp = hp
        self.full = hp
        self.w = w
        self.h = h
        self.t = 0
        self.flash = 0


# ── game logic (no screen) ──────────────────────


class World:
    """Everything in the field, in game pixels; step() is one frame."""

    def __init__(self, rng=random):
        self.rng = rng
        self.score = 0
        self.lives = LIVES
        self.level = 0
        self.special = None  # "missile" or "beam"
        self.ammo = 0
        self.kills = 0
        self.ship_w, self.ship_h = _size(SHIP)
        self.stars = []
        for _ in range(28):
            speed = rng.randint(1, 3)
            self.stars.append([rng.randint(0, 319), rng.randint(0, FH * S - 1), speed])
        self.events = 0
        self.next_level()

    def next_level(self):
        self.level += 1
        self.loop = (self.level - 1) // len(LEVELS)  # rounds through the levels
        spawns = []
        for start, kind, count, y, dy, gap in LEVELS[(self.level - 1) % len(LEVELS)]:
            for i in range(count):
                spawns.append((start + i * gap, kind, y + i * dy))
        spawns.sort(key=lambda s: s[0])
        self.spawns = spawns
        self.t = 0
        self.foes = []
        self.boss = None
        self.shots = []  # [x, y]
        self.missiles = []  # [x, y]
        self.bolts = []  # [x, y, dx, dy]
        self.items = []  # [x, y, kind]
        self.blasts = []  # [x, y, frames left, big]
        self.beam = 0  # frames the beam still shows
        self.state = "play"  # "dying", "clear", "over"
        self.timer = 0
        self.respawn()
        self.inv = 25

    def respawn(self):
        self.px = 6
        self.py = (FH - self.ship_h) // 2
        self.inv = INVULNERABLE
        self.cool = 0
        self.bolts = []

    def _spawn(self, kind, y):
        art = FOE_ART[kind]
        w, h = _size(art)
        hp = FOE_STATS[kind][0] + self.loop
        self.foes.append(Foe(kind, FW, max(0, min(FH - h, y)), hp, w, h))

    def _boss(self):
        art = BOSS_ART[(self.level - 1) % len(BOSS_ART)]
        w, h = _size(art)
        hp = 40 + 20 * (self.level - 1)
        boss = Foe(-1, FW, (FH - h) // 2, hp, w, h)
        boss.dy = 1
        self.boss = boss

    def fire_special(self):
        """The special weapon, if there is ammunition: True if it fired."""
        if self.state != "play" or not self.ammo:
            return False
        self.ammo -= 1
        if self.special == "missile":
            self.missiles.append([self.px + self.ship_w, self.py + self.ship_h // 2 - 1])
        else:
            self.beam = 8
        self.events |= SHOT
        return True

    def _hurt(self):
        if self.inv or self.state != "play":
            return
        self.lives -= 1
        self.blasts.append([self.px, self.py, 12, True])
        self.state = "dying"
        self.timer = 40
        self.events |= HURT

    def _killed(self, foe):
        self.blasts.append([foe.x + foe.w // 2 - 3, foe.y + foe.h // 2 - 3, 8, foe.kind < 0])
        if foe.kind < 0:
            self.score += 500 * self.level
            self.state = "clear"
            self.timer = 75
            self.bolts = []
            self.events |= CLEAR
            return
        self.score += FOE_STATS[foe.kind][1] * (1 + self.loop)
        self.kills += 1
        self.events |= KILL
        if self.kills % ITEM_EVERY == 0:
            kinds = ("missile", "beam", "missile", "life")
            self.items.append([foe.x, foe.y, kinds[(self.kills // ITEM_EVERY - 1) % 4]])

    def step(self, held=()):
        """One frame with these keys held. Sets self.events (SHOT, HIT...)."""
        self.events = 0
        self.t += 1
        for star in self.stars:
            star[0] -= star[2]
            if star[0] < 0:
                star[0] += 320
                star[1] = self.rng.randint(0, FH * S - 1)
        state = self.state
        if state == "dying":
            self.timer -= 1
            if self.timer <= 0:
                if self.lives <= 0:
                    self.state = "over"
                else:
                    self.state = "play"
                    self.respawn()
        elif state == "clear":
            self.timer -= 1
            if self.timer <= 0:
                self.next_level()
                return
        if self.state == "play":
            self._ship(held)
        self._spawning()
        self._move_foes()
        self._move_shots()
        self._collide()
        blasts = self.blasts
        i = len(blasts) - 1
        while i >= 0:
            blasts[i][2] -= 1
            if blasts[i][2] <= 0:
                del blasts[i]
            i -= 1

    def _ship(self, held):
        if self.inv:
            self.inv -= 1
        dx = ("right" in held) - ("left" in held)
        dy = ("down" in held) - ("up" in held)
        self.px = max(0, min(FW * 3 // 5, self.px + 2 * dx))
        self.py = max(0, min(FH - self.ship_h, self.py + 2 * dy))
        if self.cool:
            self.cool -= 1
        elif len(self.shots) < SHOTS_MAX:
            for key in FIRE:
                if key in held:
                    self.shots.append([self.px + self.ship_w, self.py + self.ship_h // 2])
                    self.cool = SHOT_EVERY
                    break

    def _spawning(self):
        spawns = self.spawns
        while spawns and spawns[0][0] <= self.t:
            _, kind, y = spawns.pop(0)
            self._spawn(kind, y)
        if not spawns and not self.foes and self.boss is None and self.state == "play":
            self._boss()
            self.events |= BOSS

    def _move_foes(self):
        py = self.py
        t = self.t
        fire = max(24, 60 - 12 * self.loop)
        foes = self.foes
        for foe in foes:
            foe.t += 1
            kind = foe.kind
            speed = FOE_STATS[kind][2]
            foe.x -= speed if speed else foe.t & 1
            if kind == WAVE:
                foe.y = max(0, min(FH - foe.h, foe.base + SINE[(foe.t >> 1) & 31]))
            elif kind == DIVER and foe.t & 1:
                if foe.y + foe.h // 2 < py + 3:
                    foe.y = min(FH - foe.h, foe.y + 1)
                elif foe.y + foe.h // 2 > py + 3:
                    foe.y = max(0, foe.y - 1)
            elif kind == GUNNER and foe.t % fire == 0 and foe.x < FW - 8 and self.state == "play":
                aim = py + 3 - foe.y - foe.h // 2
                self.bolts.append([foe.x - 2, foe.y + foe.h // 2, -2, (aim > 6) - (aim < -6)])
            if foe.flash:
                foe.flash -= 1
        i = len(foes) - 1
        while i >= 0:
            if foes[i].x + foes[i].w < 0:
                del foes[i]
            i -= 1
        boss = self.boss
        if boss is not None and self.state != "clear":
            boss.t += 1
            if boss.flash:
                boss.flash -= 1
            if boss.x > FW - boss.w - 6:
                boss.x -= 1
            else:
                boss.y += boss.dy
                if boss.y <= 2 or boss.y >= FH - boss.h - 2:
                    boss.dy = -boss.dy
                if boss.t % max(20, 45 - 5 * self.loop - 2 * self.level) == 0 and self.state == "play":
                    mid = boss.y + boss.h // 2
                    for dy in (-1, 0, 1):
                        self.bolts.append([boss.x - 2, mid, -2, dy])
        bolts = self.bolts
        i = len(bolts) - 1
        while i >= 0:
            bolt = bolts[i]
            bolt[0] += bolt[2]
            if t & 1:
                bolt[1] += bolt[3]
            if bolt[0] < -2 or bolt[1] < -2 or bolt[1] > FH:
                del bolts[i]
            i -= 1
        items = self.items
        i = len(items) - 1
        while i >= 0:
            items[i][0] -= 1
            if items[i][0] < -6:
                del items[i]
            i -= 1

    def _move_shots(self):
        for shots, speed in ((self.shots, 4), (self.missiles, 3)):
            i = len(shots) - 1
            while i >= 0:
                shots[i][0] += speed
                if shots[i][0] >= FW:
                    del shots[i]
                i -= 1
        if self.beam:
            self.beam -= 1

    def _hit(self, foe, damage):
        foe.hp -= damage
        foe.flash = 2
        self.events |= HIT

    def _collide(self):
        targets = self.foes if self.boss is None else self.foes + [self.boss]
        beam_y = self.py + self.ship_h // 2
        for foe in targets:
            if foe.hp <= 0:
                continue
            fx = foe.x
            fy = foe.y
            fx2 = fx + foe.w
            fy2 = fy + foe.h
            shots = self.shots
            i = len(shots) - 1
            while i >= 0:
                x, y = shots[i]
                if x + 3 > fx and x < fx2 and fy <= y < fy2:
                    del shots[i]
                    self._hit(foe, 1)
                i -= 1
            missiles = self.missiles
            i = len(missiles) - 1
            while i >= 0:
                x, y = missiles[i]
                if x + 5 > fx and x < fx2 and y + 2 > fy and y < fy2:
                    del missiles[i]
                    self._hit(foe, 6)
                    self.blasts.append([x, y - 2, 6, False])
                i -= 1
            if self.beam and fy <= beam_y < fy2 and fx2 > self.px:
                self._hit(foe, 1)
            if foe.hp <= 0:
                self._killed(foe)
            elif self.state == "play" and not self.inv:
                if fx < self.px + self.ship_w - 1 and fx2 > self.px + 1 and fy < self.py + self.ship_h - 1 and fy2 > self.py + 1:
                    if foe.kind >= 0:
                        foe.hp = 0
                        self._killed(foe)
                    self._hurt()
        foes = self.foes
        i = len(foes) - 1
        while i >= 0:
            if foes[i].hp <= 0:
                del foes[i]
            i -= 1
        if self.boss is not None and self.boss.hp <= 0:
            self.boss = None
        if self.state != "play":
            return
        px = self.px
        py = self.py
        if not self.inv:
            for bolt in self.bolts:
                if px + 1 < bolt[0] + 2 and bolt[0] < px + self.ship_w - 1 and py + 1 < bolt[1] + 2 and bolt[1] < py + self.ship_h - 1:
                    self._hurt()
                    return
        items = self.items
        i = len(items) - 1
        while i >= 0:
            x, y, kind = items[i]
            if x < px + self.ship_w and x + 5 > px and y < py + self.ship_h and y + 5 > py:
                del items[i]
                self.events |= POD
                if kind == "life":
                    self.lives = min(9, self.lives + 1)
                elif kind == self.special:
                    self.ammo = min(9, self.ammo + AMMO)
                else:
                    self.special = kind
                    self.ammo = AMMO
            i -= 1


# ── screen ──────────────────────────────────────
# The field is drawn a band at a time into one 8 KB FrameBuffer, which is
# then copied over the band's rows of the framebuffer: every frame repaints
# everything, nothing flickers (the panel never shows a half-drawn band), and
# it needs no 49 KB second buffer.

_ROW_BYTES = 160
_NOKIA = (0xC79B, 0x4287, 0x8511)  # the 3310's LCD: light, dark, stars (RGB565)


def _sprite(rows, colors, scale=S):
    # rows of characters as a FrameBuffer `scale` times bigger; colour 0 is
    # transparent when blitted
    pw = len(rows[0]) * scale
    ph = len(rows) * scale
    stride = pw + (pw & 1)
    fb = framebuf.FrameBuffer(bytearray(stride * ph // 2), pw, ph, framebuf.GS4_HMSB, stride)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch != ".":
                fb.fill_rect(x * scale, y * scale, scale, scale, colors[0] if ch == "#" else colors[1])
    return fb


class View:
    def __init__(self, fb):
        self.fb = fb
        self.lcd = False  # the Nokia look
        self.saved = None  # the palette before it
        self.hud = None
        if framebuf is None or fb is None:
            self.band = None
            return
        self.buf = bytearray(_ROW_BYTES * BAND)
        self.band = framebuf.FrameBuffer(self.buf, 320, BAND, framebuf.GS4_HMSB)
        self.dmv = memoryview(fb)
        self.ship = _sprite(SHIP, (BCYAN, WHITE))
        self.mini = _sprite(SHIP, (BWHITE, BWHITE), 1)
        self.foes = [_sprite(art, colors) for art, colors in zip(FOE_ART, FOE_COLORS)]
        self.white = [_sprite(art, (WHITE, WHITE)) for art in FOE_ART]
        self.bosses = [_sprite(art, colors) for art, colors in zip(BOSS_ART, BOSS_COLORS)]
        self.boss_white = [_sprite(art, (WHITE, WHITE)) for art in BOSS_ART]
        self.blasts = [_sprite(art, (BYELLOW, BRED)) for art in BLAST_ART]

    # the Nokia look: two greens through the palette, the drawing unchanged

    def nokia(self, on):
        try:
            import picocalcdisplay
        except ImportError:
            return
        if on and self.saved is None:
            current = bytearray(bytes(picocalcdisplay.getLUTview())[:64])
            self.saved = bytes(current)
            light, dark, mid = _NOKIA
            for i in range(16):
                value = light if i in (0, 15) else mid if i == 8 else dark
                current[2 * i] = value >> 8
                current[2 * i + 1] = value & 0xFF
            # firmware bug: setLUT() copies twice its length in bytes, so 32
            # bytes of a 64-byte buffer set palette entries 0-31 (pico_utils)
            picocalcdisplay.setLUT(memoryview(current)[:32])
        elif not on and self.saved is not None:
            picocalcdisplay.setLUT(memoryview(bytearray(self.saved))[:32])
            self.saved = None
        self.lcd = on

    def title(self, w, force=False):
        hud = (w.level, w.lives, w.special, w.ammo, w.score)
        if hud == self.hud and not force:
            return
        self.hud = hud
        if self.fb is None:
            return
        special = ""
        if w.special:
            special = "{}{}  ".format("M" if w.special == "missile" else "B", w.ammo)
        games.title("Space Raid", "Lv{}  {}{:06d}".format(w.level, special, w.score))
        if self.band is not None:
            for i in range(min(w.lives, 5)):
                self.fb.blit(self.mini, 84 + i * 13, 2, 0)

    def render(self, w):
        band = self.band
        if band is None:
            return
        dmv = self.dmv
        buf = self.buf
        stars = w.stars
        foes = w.foes
        boss = w.boss
        ship_on = w.state == "play" and not (w.inv & 2)
        px = w.px * S
        py = w.py * S
        beam_y = (w.py + w.ship_h // 2) * S
        for top in range(0, FH * S, BAND):
            bottom = top + BAND
            band.fill(BLACK)
            for star in stars:
                y = star[1]
                if top <= y < bottom:
                    band.fill_rect(star[0], y - top, 2 if star[2] == 3 else 1, 1, WHITE if star[2] == 3 else GREY)
            for foe in foes:
                y = foe.y * S
                if y < bottom and y + foe.h * S > top:
                    band.blit((self.white if foe.flash else self.foes)[foe.kind], foe.x * S, y - top, 0)
            if boss is not None:
                y = boss.y * S
                if y < bottom and y + boss.h * S > top:
                    art = (w.level - 1) % len(BOSS_ART)
                    band.blit((self.boss_white if boss.flash else self.bosses)[art], boss.x * S, y - top, 0)
                if top == 0:
                    band.fill_rect(80, 2, 160, 4, GREY)
                    band.fill_rect(80, 2, 160 * max(0, boss.hp) // boss.full, 4, BRED)
            for x, y in w.shots:
                y = y * S
                if top <= y < bottom:
                    band.fill_rect(x * S, y - top, 9, 3, BYELLOW)
            for x, y in w.missiles:
                y = y * S
                if y + 6 > top and y < bottom:
                    band.fill_rect(x * S, y - top, 12, 6, BRED)
                    band.fill_rect(x * S + 12, y - top + 1, 3, 4, WHITE)
            for x, y, _, _ in w.bolts:
                y = y * S
                if y + 6 > top and y < bottom:
                    band.fill_rect(x * S, y - top, 6, 6, BRED)
                    band.fill_rect(x * S + 2, y - top + 2, 2, 2, BYELLOW)
            for x, y, kind in w.items:
                y = y * S
                if y + 15 > top and y < bottom:
                    color = BGREEN if kind == "life" else BCYAN
                    band.rect(x * S, y - top, 15, 15, color)
                    band.text("+" if kind == "life" else kind[0].upper(), x * S + 4, y - top + 4, color)
            for x, y, left, big in w.blasts:
                y = y * S
                if y + 21 > top and y < bottom:
                    band.blit(self.blasts[1 if big or left < 4 else 0], x * S, y - top, 0)
            if w.beam and top <= beam_y < bottom:
                band.fill_rect(px + w.ship_w * S, beam_y - top - 1, 320, 3, BCYAN)
            if ship_on and py < bottom and py + w.ship_h * S > top:
                band.blit(self.ship, px, py - top, 0)
            if w.state == "clear" or (w.t < 50 and w.state == "play"):
                text = "LEVEL {} CLEAR".format(w.level) if w.state == "clear" else "LEVEL {}".format(w.level)
                y = FH * S // 2 - 4
                if top <= y < bottom:
                    band.text(text, (320 - len(text) * 8) // 2, y - top, BYELLOW)
            start = (FIELD_Y + top) * _ROW_BYTES
            dmv[start : start + len(buf)] = buf


# ── playing ─────────────────────────────────────

_TONES = (  # event, frequency, ms: the first one that happened sounds
    (HURT, 110, 260),
    (CLEAR, 1047, 300),
    (BOSS, 147, 300),
    (POD, 1319, 80),
    (KILL, 220, 60),
    (HIT, 440, 20),
    (SHOT, 1760, 12),
)


def _sound(sound, events):
    for event, freq, ms in _TONES:
        if events & event:
            sound.beep(freq, ms)
            return


def _round(w, view, pad, sound):
    # One game; False if it was left before the end.
    view.fb.fill_rect(0, 12, 320, 308, BLACK)
    view.title(w, True)
    clock = _Clock(FRAME_MS)
    while w.state != "over":
        for key in pad.poll():
            if key in SPECIAL:
                w.fire_special()
            elif key == "m":
                sound.toggle()
            elif key == "n":
                view.nokia(not view.lcd)
                games.set_option("nokia", view.lcd)
            elif key == "quit":
                return False
            elif key in ("p", "esc", "q", "power"):
                if not games.pause(pad, sound, key):
                    return False
                view.fb.fill_rect(0, 12, 320, 308, BLACK)
                view.title(w, True)
                clock.reset()
        w.step(pad.held)
        if w.events:
            _sound(sound, w.events)
        view.render(w)
        view.title(w)
        sound.update()
        clock.wait()
    _sleep_ms(400)
    return True


def play():
    """Games until q; returns the best score."""
    screen = _Screen()
    if screen.fb is None or framebuf is None:
        print("Space Raid draws on the PicoCalc screen.")
        return games.best("space")
    pad = _Pad()
    sound = _Sound()
    view = View(screen.fb)
    best = games.best("space")
    try:
        view.nokia(bool(games.option("nokia", False)))
        while True:
            w = World()
            finished = _round(w, view, pad, sound)
            new = games.record("space", w.score)
            best = max(best, w.score)
            games.flush()
            if not finished:
                break
            extra = "Level {}".format(w.level)
            if not games.game_over(pad, sound, w.score, best, new, extra):
                break
    finally:
        view.nokia(False)
        sound.close()
        screen.close()
        games.flush(True)
    return best


# ── standard ────────────────────────────────────


def ver():
    print("space:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Space Raid --")
    print("play()  p()   arrows fly, Space fires,")
    print("              c special, p pause, n Nokia look,")
    print("              m sound, q quit")
    print("Best score: /sd/games.json")


def h():
    return help()


p = play
