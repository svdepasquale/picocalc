try:
    from harness import *  # noqa
except ImportError:  # on the device tools/test_device.sh puts harness.py in front
    pass

# Host checks for the games: the list's scores, the keyboard as a pad, the
# sound, then Snake, Tetris and Space Raid: their logic, and each one played
# through on a recording display.
# Run from the repo root with either interpreter:
#   python3 tests/test_games.py
#   micropython tests/test_games.py

import games

try:
    import framebuf
except ImportError:
    framebuf = None


class FakeRng:
    def __init__(self, values):
        self.values = list(values)

    def randint(self, low, high):
        return self.values.pop(0) if self.values else low


class ScriptPad:
    # a Pad that plays a script: each poll() takes the next (presses, held);
    # after the script, "quit"
    def __init__(self, script):
        self.script = list(script)
        self.held = set()

    def poll(self):
        if not self.script:
            self.held = set()
            return ("quit",)
        presses, held = self.script.pop(0)
        self.held = set(held)
        return presses

    def clear(self):
        self.held = set()


_FILES = (HERE + "/_tmp_games_sd.json", HERE + "/_tmp_games_flash.json", HERE + "/_tmp_snake_old.json")


def with_store(body, sd=True):
    # the scores in tests/, on the "card" or not
    saved = swap(games, {"SD_FILE": _FILES[0], "FLASH_FILE": _FILES[1], "OLD_SNAKE": _FILES[2], "_sd_ok": lambda: sd})
    games._STORE[0] = None
    try:
        body()
    finally:
        unswap(games, saved)
        games._STORE[0] = None
        for path in _FILES:
            rm_tmp(path)


def no_sleep(modules):
    # what each module sleeps with, swapped for nothing; returns the undo
    return [(m, swap(m, {"_sleep_ms": lambda ms: None})) for m in modules]


def undo(saved):
    for module, old in saved:
        unswap(module, old)


# ── scores ──────────────────────────────────────


def test_scores_on_the_card():
    def body():
        write_file(_FILES[2], '{"best": 7, "sound": false}')
        check("Snake's old best", games.best("snake"), 7)
        check("its sound switch", games.option("sound", True), False)
        check("not a best", games.record("snake", 5), False)
        check("nothing to save", games.flush(), True)
        check("a best", games.record("snake", 9), True)
        check("saved at once", games.flush(), True)
        check("on the card", pu.load_json(_FILES[0]), {"snake": 9, "sound": False})
        games.set_option("nokia", True)
        check("option kept", games.option("nokia", False), True)

    with_store(body)


def test_scores_without_card():
    def body():
        check("nothing yet", games.best("tetris"), 0)
        games.record("tetris", 1200)
        check("flash waits for the end", games.flush(), False)
        check("not written", pu.load_json(_FILES[1]), None)
        check("written at the end", games.flush(True), True)
        check("on flash", pu.load_json(_FILES[1]), {"tetris": 1200})
        games._STORE[0] = None
        check("read back", games.best("tetris"), 1200)

    with_store(body, sd=False)


# ── the pad ─────────────────────────────────────


class FakeKbd:
    # the keyboard MCU's FIFO behind the driver's read_reg16()
    def __init__(self):
        self.events = []
        self.temp = bytearray(2)
        self.isShift = self.isCtrl = self.isAlt = False

    def read_reg16(self, reg):
        if reg == 0x04:
            self.temp[0] = len(self.events)
            self.temp[1] = 0
        else:
            self.temp[0], self.temp[1] = self.events.pop(0)
        return self.temp


class FakePoll:
    ready = False

    def poll(self, timeout):
        return [1] if self.ready else []


def test_pad_reads_the_fifo():
    kbd = FakeKbd()
    stdin = FakePoll()
    ticks = []
    saved = swap(
        games,
        {
            "_picocalc": lambda name: kbd,
            "_terminal": lambda: object(),
            "_stdin_poll": lambda: stdin,
            "_screen_on": lambda: None,
        },
    )
    old_tick = pu._TICK[0]
    pu._TICK[0] = lambda: ticks.append(1)
    try:
        pad = games.Pad()
        kbd.events = [(1, 0xB5), (1, 0x20)]
        check("presses", pad.poll(), ["up", " "])
        check("held", sorted(pad.held), [" ", "up"])
        kbd.events = [(1, 0xB5), (3, 0x20)]
        check("a repeat is no press", pad.poll(), [])
        check("released", sorted(pad.held), ["up"])
        kbd.events = [(1, 0xA5), (1, ord("c")), (3, 0xA5), (1, ord("W")), (1, 0x91), (1, 0xC1)]
        check("ctrl-c, shift-w, power", pad.poll(), ["quit", "w", "power"])
        check("ctrl let go in the driver too", kbd.isCtrl, False)
        check("power is never held", "power" in pad.held, False)
        kbd.events = [(1, 0xA2)]
        pad.poll()
        check("shift kept for the driver", kbd.isShift, True)
        check("nothing pending", pad.poll(), ())
        check("music pumped every poll", len(ticks), 5)
        stdin.ready = True
        try:
            pad.poll()
            check("host takeover", "no", "raised")
        except pu.HostTakeover:
            pass
        pad.clear()
        check("cleared", pad.held, set())
    finally:
        unswap(games, saved)
        pu._TICK[0] = old_tick


def test_pad_without_keyboard():
    keys = ["left", None]
    saved = swap(games, {"_picocalc": lambda name: None, "_poll_key": lambda: keys.pop(0)})
    try:
        pad = games.Pad()
        check("one key", pad.poll(), ("left",))
        check("held for its frame", pad.held, {"left"})
        check("none", pad.poll(), ())
        check("let go", pad.held, set())
    finally:
        unswap(games, saved)


# ── sound ───────────────────────────────────────


class _PWM:
    log = []

    def __init__(self, pin):
        self.pin = pin

    def freq(self, hz):
        _PWM.log.append(("freq", self.pin, hz))

    def duty_u16(self, duty):
        _PWM.log.append(("duty", self.pin, duty))

    def deinit(self):
        _PWM.log.append(("deinit", self.pin))


class _Machine:
    PWM = _PWM

    @staticmethod
    def Pin(n):
        return n


class _Music:
    track = None

    def playing(self):
        return self.track


def test_sound_beeps_unless_music_plays():
    saved = (sys.modules.get("machine"), sys.modules.get("music"))
    music = _Music()
    sys.modules["machine"] = _Machine
    sys.modules["music"] = music

    def body():
        _PWM.log = []
        sound = games.Sound()
        sound.beep(440, 0)
        check("both speakers", _PWM.log, [("freq", 26, 440), ("duty", 26, games.DUTY), ("duty", 27, games.DUTY)])
        sound.update()
        check("stopped after its time", _PWM.log[-2:], [("duty", 26, 0), ("duty", 27, 0)])
        music.track = "song.wav"
        _PWM.log = []
        sound.beep(440)
        check("quiet over the music", _PWM.log, [])
        music.track = None
        check("switched off", sound.toggle(), False)
        check("switch kept", games.option("sound", True), False)
        sound.beep(440)
        sound.close()
        check("released", _PWM.log[-1], ("deinit", 27))

    try:
        with_store(body)
    finally:
        for name, module in zip(("machine", "music"), saved):
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


# ── snake ───────────────────────────────────────

import snake


def test_snake_moves_and_turns():
    s = snake.new_game(10, 5, FakeRng([0, 0]))
    check("start", s["snake"], [(3, 2), (4, 2), (5, 2)])
    check("food", s["food"], (0, 0))
    check("moved", snake.step(s), "moved")
    check("head right", s["snake"][-1], (6, 2))
    check("length kept", len(s["snake"]), 3)
    snake.turn(s, (-1, 0))
    snake.step(s)
    check("reverse ignored", s["snake"][-1], (7, 2))
    snake.turn(s, (0, -1))
    snake.turn(s, (-1, 0))
    snake.step(s)
    check("first queued turn", s["snake"][-1], (7, 1))
    snake.step(s)
    check("second queued turn", s["snake"][-1], (6, 1))
    check("cells match", s["cells"], set(s["snake"]))


def test_snake_eats_and_dies():
    s = snake.new_game(10, 5, FakeRng([6, 2]))
    check("ate", snake.step(s), "ate")
    check("grew", len(s["snake"]), 4)
    check("score", s["score"], 1)
    for _ in range(3):
        snake.step(s)
    check("wall", snake.step(s), "dead")
    loop = snake.new_game(5, 5, FakeRng([4, 4]))
    loop["snake"] = [(1, 1), (2, 1), (2, 2), (1, 2)]
    loop["cells"] = set(loop["snake"])
    loop["dir"] = (0, -1)
    check("tail cell is free", snake.step(loop), "moved")
    bite = snake.new_game(5, 5, FakeRng([4, 4]))
    bite["snake"] = [(0, 0), (1, 0), (2, 0), (2, 1), (1, 1)]
    bite["cells"] = set(bite["snake"])
    bite["dir"] = (0, -1)
    check("own body", snake.step(bite), "dead")


def test_snake_food_and_turn_queue():
    s = snake.new_game(4, 1, FakeRng([1, 0] * 60))
    check("crowded: first free cell", s["food"], (3, 0))
    s["cells"].add((3, 0))
    check("full field", snake.place_food(s), None)
    R, U, L, D = (1, 0), (0, -1), (-1, 0), (0, 1)
    s = snake.new_game(10, 5, FakeRng([0, 0]))
    for heading in (R, R, U):
        snake.turn(s, heading)
    snake.step(s)
    check("repeats take no slot", s["snake"][-1], (5, 1))
    s = snake.new_game(10, 5, FakeRng([0, 0]))
    for heading in (U, D, U, L):
        snake.turn(s, heading)
    check("reverse and repeat dropped", s["turns"], [U, L])


def test_snake_plays_to_the_end():
    # The snake runs into the wall; the score box takes q: back, best kept,
    # nothing written to flash (the file on the "card").
    clock = [0]

    def ticks():
        clock[0] += 10
        return clock[0]

    def body(fake, vt):
        pad = ScriptPad([(("m",), ()), (("m",), ())] + [((), ())] * 300)
        saved = swap(snake, {"_Pad": lambda: pad, "_ticks_ms": ticks})
        sleeps = no_sleep((games, snake))
        keys_from([ord("q")])
        try:
            with_store(lambda: check("best", snake.play(), 0))
        finally:
            pu._key_byte = REAL_KEY_BYTE
            unswap(snake, saved)
            undo(sleeps)
        check("drew cells", len([c for c in fake.calls if c[0] == "fill_rect"]) > 20, True)
        check("crashed into the wall", len([c for c in fake.calls if c[0] == "line"]), 2)

    with_display(body)


# ── tetris ──────────────────────────────────────

import tetris


def test_tetris_pieces_and_bag():
    check("T turns", tetris.SHAPES[tetris.T][1], ((2, 1), (1, 0), (1, 1), (1, 2)))
    check("I turns", tetris.SHAPES[tetris.I][1], ((2, 0), (2, 1), (2, 2), (2, 3)))
    check("O stays", sorted(tetris.SHAPES[tetris.O][1]), sorted(tetris.SHAPES[tetris.O][0]))
    g = tetris.Game(FakeRng([]))
    seen = [g.piece] + list(g.queue)
    for _ in range(4):
        g.spawn()
        seen.append(g.queue[-1])
    check("a bag holds all seven", sorted(seen[:7]), [1, 2, 3, 4, 5, 6, 7])
    check("spawn in the middle, last row showing", (g.x, g.y), ((tetris.W - tetris.SIZES[g.piece]) // 2, 1))


def test_tetris_moves_and_kicks():
    g = tetris.Game(FakeRng([]))
    g.spawn(tetris.I)
    check("turn", g.turn(1), True)
    while g.shift(-1):
        pass
    check("against the wall", (g.x, g.rot), (-2, 1))
    check("kicked off the wall", (g.turn(1), g.x, g.rot), (True, 0, 2))
    check("and back", (g.turn(-1), g.rot), (True, 1))
    g.spawn(tetris.O)
    check("O never turns", g.turn(1), False)
    g.spawn(tetris.T)
    g.board[2] = bytearray(b"\x01" * 3 + b"\x00" * 7)
    check("no room", g.shift(-4), False)


def test_tetris_lines_score_level():
    g = tetris.Game(FakeRng([]))
    for r in range(18, 22):
        for c in range(9):
            g.board[r][c] = 2
    g.spawn(tetris.I)
    g.turn(1)
    while g.shift(1):
        pass
    rows = g.hard_drop()
    check("four lines", rows, [18, 19, 20, 21])
    check("tetris score", g.score, 800 + 2 * 17)
    check("board empty", sum(sum(row) for row in g.board), 0)
    g.lines = 9
    for c in range(9):
        g.board[21][c] = 2
    g.spawn(tetris.I)
    g.turn(1)
    while g.shift(1):
        pass
    g.hard_drop()
    check("level up at 10 lines", (g.lines, g.level), (10, 2))


def test_tetris_gravity_lock_hold():
    g = tetris.Game(FakeRng([]))
    y = g.y
    g.tick(999)
    check("a second a row", g.y, y)
    g.tick(1)
    check("one row down", g.y, y + 1)
    g.y = g.ghost_y()
    check("waits on the floor", g.tick(tetris.LOCK_MS - 1), None)
    g.shift(1) or g.shift(-1)
    check("a move restarts it", g.tick(tetris.LOCK_MS - 1), None)
    check("then locks", g.tick(1), [])
    first = g.piece
    check("hold", g.hold_piece(), True)
    check("held", g.hold, first)
    check("once per piece", g.hold_piece(), False)
    g.hard_drop()
    check("hold again", g.hold_piece(), True)
    check("the held one comes back", g.piece, first)


def test_tetris_game_over():
    g = tetris.Game(FakeRng([]))
    for r in range(1, 4):
        g.board[r] = bytearray(b"\x00" + b"\x03" * 9)
    check("no room to come in", g.spawn(), False)
    check("over", g.over, True)
    check("nothing moves", (g.shift(-1), g.turn(1), g.tick(5000)), (False, False, None))


def test_tetris_plays_through():
    def body(fake, vt):
        script = [(("left",), ("left",))] * 8 + [(("up",), ()), (("z",), ()), (("c",), ()), ((), ("down",))]
        script += [((" ",), ())] * 30
        pad = ScriptPad(script)
        saved = swap(tetris, {"_Pad": lambda: pad})
        sleeps = no_sleep((games, tetris))
        keys_from([ord("q")])
        try:
            with_store(lambda: check("scored", tetris.play() > 0, True))
        finally:
            pu._key_byte = REAL_KEY_BYTE
            unswap(tetris, saved)
            undo(sleeps)
        texts = [c[1] for c in fake.calls if c[0] == "text" and isinstance(c[1], str)]
        check("panels drawn", "NEXT" in texts, True)

    with_display(body)


# ── space raid ──────────────────────────────────

import space


def _world():
    w = space.World(FakeRng([]))
    w.spawns = []
    w.inv = 0
    return w


def _foe(w, kind, x, y):
    art = space.FOE_ART[kind]
    foe = space.Foe(kind, x, y, space.FOE_STATS[kind][0], len(art[0]), len(art))
    w.foes.append(foe)
    return foe


def test_space_shots_kill():
    w = _world()
    _foe(w, space.DRONE, 40, w.py)
    for _ in range(20):
        w.step({" "})
        if w.kills:
            break
    check("one kill", (w.kills, w.score), (1, 10))
    check("a blast", len(w.blasts) > 0, True)


def test_space_hit_and_respawn():
    w = _world()
    w.bolts.append([w.px + 3, w.py + 3, 0, 0])
    w.step(())
    check("hurt", (w.lives, w.state), (space.LIVES - 1, "dying"))
    for _ in range(40):
        w.step(())
    check("back", (w.state, w.inv > 0, w.bolts), ("play", True, []))
    w.lives = 1
    w.inv = 0
    _foe(w, space.ROCK, w.px + 2, w.py)
    w.step(())
    for _ in range(40):
        w.step(())
    check("game over", (w.lives, w.state), (0, "over"))


def test_space_pods_and_specials():
    w = _world()
    w.kills = space.ITEM_EVERY - 1
    w._killed(_foe(w, space.DRONE, 50, 20))
    check("a pod", [i[2] for i in w.items], ["missile"])
    w.items = [[w.px, w.py, "missile"], [w.px, w.py, "missile"]]
    w.step(())
    check("missiles", (w.special, w.ammo), ("missile", 2 * space.AMMO))
    w.items = [[w.px, w.py, "beam"], [w.px, w.py, "life"]]
    w.step(())
    check("a beam replaces them", (w.special, w.ammo, w.lives), ("beam", space.AMMO, space.LIVES + 1))
    gunner = _foe(w, space.GUNNER, 60, w.py)
    check("beam fired", w.fire_special(), True)
    for _ in range(4):
        w.step(())
    check("beam kills in its row", gunner.hp <= 0 and w.kills > space.ITEM_EVERY - 1, True)
    w.special = "missile"
    w.ammo = 1
    rock = _foe(w, space.ROCK, 40, w.py)
    w.fire_special()
    for _ in range(12):
        w.step(())
    check("a missile is worth six shots", rock.hp <= 0, True)
    check("out of ammunition", w.fire_special(), False)


def test_space_waves_boss_level():
    w = space.World(FakeRng([]))
    check("waves", len(w.spawns), sum(wave[2] for wave in space.LEVELS[0]))
    w.spawns = []
    w.foes = []
    w.step(())
    check("then the boss", w.boss is not None, True)
    for _ in range(40):
        w.step(())
    check("it stops at the right", w.boss.x, space.FW - w.boss.w - 6)
    w.boss.hp = 1
    w.shots.append([w.boss.x - 2, w.boss.y + 2])
    w.step(())
    check("cleared", (w.state, w.boss, w.score), ("clear", None, 500))
    for _ in range(75):
        w.step(())
    check("level 2", (w.level, w.state, len(w.spawns) > 0), (2, "play", True))


def test_space_draws_a_frame():
    if framebuf is None or sys.platform == "rp2":
        return  # CPython has no framebuf; the device no 51 KB for a second screen
    display = framebuf.FrameBuffer(bytearray(320 * 160), 320, 320, framebuf.GS4_HMSB)
    view = space.View(display)
    w = _world()
    _foe(w, space.WAVE, 50, 40)
    w.shots.append([30, 30])
    w.items.append([60, 60, "beam"])
    w.step({" "})
    view.render(w)
    field = memoryview(display)[space.FIELD_Y * 160 :]
    top = w.py * 3 * 160
    check("ship drawn", any(field[top + row * 160 + i] for row in range(21) for i in range(9, 26)), True)
    check("title row left alone", bytes(memoryview(display)[: space.FIELD_Y * 160]) == bytes(space.FIELD_Y * 160), True)


def test_space_plays_through():
    if framebuf is None or sys.platform == "rp2":
        return  # the device: no second 51 KB screen for a test
    display = framebuf.FrameBuffer(bytearray(320 * 160), 320, 320, framebuf.GS4_HMSB)
    pad = ScriptPad([((" ",), (" ", "up"))] * 30 + [(("n",), ()), (("p",), ())])
    saved = swap(space, {"_Pad": lambda: pad})
    sleeps = no_sleep((games, space))
    import gfx

    old = (gfx.surface, gfx._terminal, gfx._clear_screen)
    gfx.surface = lambda: display
    gfx._terminal = lambda: FakeVt()
    gfx._clear_screen = lambda: None
    keys_from([ord("q")])
    try:
        with_store(lambda: check("best kept", space.play(), games.best("space")))
    finally:
        pu._key_byte = REAL_KEY_BYTE
        gfx.surface, gfx._terminal, gfx._clear_screen = old
        gfx._FB[0] = None
        pu._CONSOLE[0] = None
        unswap(space, saved)
        undo(sleeps)


run(globals())
