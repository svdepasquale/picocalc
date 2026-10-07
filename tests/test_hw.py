try:
    from harness import *  # noqa
except ImportError:  # on the device tools/test_device.sh puts harness.py in front
    pass

# Host checks for music, synthesizer, snake, clock_ntp and sys_status.
# Run from the repo root with either interpreter:
#   python3 tests/test_hw.py
#   micropython tests/test_hw.py


# ── synthesizer ─────────────────────────────────

import synthesizer as synth


def test_rtttl_parse():
    notes = synth._rtttl_parse("t:d=4,o=5,b=120:8e6,p,4c#.5,a")
    check("count", len(notes), 4)
    check("e6 eighth", (int(notes[0][0]), notes[0][1]), (1318, 250))
    check("rest", notes[1], (0, 500))
    check("c#5 dotted quarter", (int(notes[2][0]), notes[2][1]), (554, 750))
    check("a5 default octave", int(notes[3][0]), 880)
    check("bad song", synth._rtttl_parse("nope"), None)


class _RecPWM:
    # machine.PWM stand-in that records what is done to the speaker pins
    log = []

    def __init__(self, pin):
        self.pin = pin

    def freq(self, hz):
        _RecPWM.log.append(("freq", self.pin))

    def duty_u16(self, duty):
        _RecPWM.log.append(("duty", self.pin, duty))

    def deinit(self):
        _RecPWM.log.append(("deinit", self.pin))


class _RecMachine:
    PWM = _RecPWM


_RecMachine.Pin = lambda n: n


class _InterruptedI2S:
    def write(self, data):
        raise KeyboardInterrupt


def test_synth_ctrl_c_stops_the_tune():
    def interrupted(ms):
        raise KeyboardInterrupt

    out = []
    saved = (sys.modules.get("machine"), synth._sleep_ms, synth._poll_key, synth._init_audio, synth._use_pwm)
    sys.modules["machine"] = _RecMachine
    synth._sleep_ms = interrupted
    synth._poll_key = lambda: None
    synth.print = lambda *args, **kw: out.append(" ".join(str(a) for a in args))
    del _RecPWM.log[:]
    try:
        try:
            synth._play_freq_pwm(440, 50)
            check("pwm: Ctrl+C reaches the caller", "returned", "KeyboardInterrupt")
        except KeyboardInterrupt:
            pass
        silent = [("duty", 26, 0), ("duty", 27, 0), ("deinit", 26), ("deinit", 27)]
        check("pwm: silenced and released first", _RecPWM.log[-4:], silent)
        del _RecPWM.log[:]
        check("seq stops", synth.seq("C D E F"), False)
        check("on its first note", (_RecPWM.log.count(("deinit", 26)), out[-1]), (1, "Stopped."))
        synth._init_audio = lambda: _InterruptedI2S()
        try:
            synth._play_freq_i2s(440, 50)
            check("i2s: Ctrl+C reaches the caller", "returned", "KeyboardInterrupt")
        except KeyboardInterrupt:
            pass
        synth._use_pwm = False
        synth._sleep_ms = lambda ms: None
        check("rtttl stops", synth.rtttl("t:d=4,o=5,b=120:c,d,e"), False)
    finally:
        if saved[0] is None:
            sys.modules.pop("machine", None)
        else:
            sys.modules["machine"] = saved[0]
        synth._sleep_ms, synth._poll_key, synth._init_audio, synth._use_pwm = saved[1:]
        del synth.print


# snake


class FakeRng:
    def __init__(self, values):
        self.values = list(values)

    def randint(self, low, high):
        return self.values.pop(0) if self.values else low


def test_snake_moves_and_turns():
    import snake

    s = snake.new_game(10, 5, FakeRng([0, 0]))
    check("start", s["snake"], [(3, 2), (4, 2), (5, 2)])
    check("food placed", s["food"], (0, 0))
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
    import snake

    s = snake.new_game(10, 5, FakeRng([6, 2]))
    check("food ahead", s["food"], (6, 2))
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


def test_snake_food_placement():
    import snake

    s = snake.new_game(4, 1, FakeRng([1, 0] * 60))
    check("crowded: first free cell", s["food"], (3, 0))
    s["cells"].add((3, 0))
    check("full field", snake.place_food(s), None)
    check("no food left", s["food"], None)


def test_snake_turn_queue():
    import snake

    R, U, L, D = (1, 0), (0, -1), (-1, 0), (0, 1)
    s = snake.new_game(10, 5, FakeRng([0, 0]))
    for heading in (R, R, U):  # right, right, up within one step
        snake.turn(s, heading)
    snake.step(s)
    check("repeats take no slot: the up lands", s["snake"][-1], (5, 1))
    s = snake.new_game(10, 5, FakeRng([0, 0]))
    for heading in (U, D, U, L):
        snake.turn(s, heading)
    check("reverse and repeat of the queued turn dropped", s["turns"], [U, L])
    snake.step(s)
    snake.step(s)
    check("up then left", s["snake"][-1], (4, 1))


# music


def _wav_bytes(channels, rate, bits, data, extra=b"", kind=1, ext=False):
    fmt = (
        (0xFFFE if ext else kind).to_bytes(2, "little")
        + channels.to_bytes(2, "little")
        + rate.to_bytes(4, "little")
        + (rate * channels * bits // 8).to_bytes(4, "little")
        + (channels * bits // 8).to_bytes(2, "little")
        + bits.to_bytes(2, "little")
    )
    if ext:
        fmt += (22).to_bytes(2, "little") + bytes(6) + kind.to_bytes(2, "little") + bytes(14)
    body = b"WAVE" + b"fmt " + len(fmt).to_bytes(4, "little") + fmt + extra
    body += b"data" + len(data).to_bytes(4, "little") + data
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def test_wav_info():
    import music

    data = bytes(range(16))
    listc = b"LIST" + (5).to_bytes(4, "little") + b"abcde" + b"\x00"  # odd size, padded
    fllr = b"FLLR" + (8).to_bytes(4, "little") + bytes(8)  # afconvert's filler
    wav = _wav_bytes(2, 22050, 16, data, extra=listc + fllr)
    info = music.wav_info(io.BytesIO(wav))
    check("stereo 16-bit", info[:3], (2, 22050, 16))
    check("data offset", wav[info[3] : info[3] + info[4]], data)
    check("extensible PCM", music.wav_info(io.BytesIO(_wav_bytes(1, 8000, 8, data, ext=True)))[:3], (1, 8000, 8))
    cut = _wav_bytes(1, 22050, 16, data)[:-6]
    check("cut file clamps the size", music.wav_info(io.BytesIO(cut))[4], 10)
    for name, blob in (
        ("float rejected", _wav_bytes(1, 22050, 32, data, kind=3)),
        ("24-bit rejected", _wav_bytes(1, 22050, 24, data)),
        ("not a WAV", b"ID3" + bytes(40)),
    ):
        try:
            music.wav_info(io.BytesIO(blob))
            check(name, "accepted", "ValueError")
        except ValueError:
            pass


def test_music_convert():
    import music
    from array import array

    def word(d):
        return (d << 16) | d

    pcm = b"".join(v.to_bytes(2, "little") for v in (0, 32767, 32768, 65472))  # 0, max, min, -64
    check("16-bit mono full", music.convert_reference(pcm, 4, 1, 16, 16), [word(512), word(1023), word(0), word(511)])
    check("16-bit mono half", music.convert_reference(pcm, 3, 1, 16, 8), [word(512), word(767), word(256)])
    check("8-bit stereo", music.convert_reference(bytes([255, 0]), 1, 2, 8, 16), [1020])
    check("silence at 0", music.convert_reference(pcm, 2, 1, 16, 0), [word(512), word(512)])
    data = bytes((i * 37 + 11) % 256 for i in range(4 * 64))
    for channels, bits in ((2, 16), (1, 16), (2, 8), (1, 8)):
        frames = len(data) // (channels * bits // 8)
        frames = min(frames, 64)
        out = array("I", [0] * frames)
        music.convert(data, out, frames, channels, bits, 11)
        check(
            "convert {}ch {}-bit matches reference".format(channels, bits),
            list(out),
            music.convert_reference(data, frames, channels, bits, 11),
        )
    if sys.platform == "rp2":  # no native emitter on the unix port for arm64
        check("viper compiled", music._VIPER is not None, True)


def test_music_songs():
    import music

    folder = HERE + "/_tmp_music"
    rm_tree(folder)
    os.mkdir(folder)
    try:
        write_file(folder + "/b.wav", "x" * 10)
        write_file(folder + "/A.WAV", "")
        write_file(folder + "/._b.wav", "junk")
        write_file(folder + "/note.txt", "x")
        os.mkdir(folder + "/x.wav")
        found = music.songs((folder,))
        check("wav only, by name", [song[0] for song in found], ["A", "b"])
        check("size", found[1][2], 10)
        check("missing folder", music.songs((folder + "/nope",)), [])
    finally:
        rm_tree(folder)


# play_file on a model of the hardware, in virtual microseconds (integers:
# the same run on every interpreter): the pacer ticks at the sample rate,
# each tick moves one word of the busy DMA channel into the compare
# register, a finished channel triggers the one it chains to and queues its
# IRQ handler. Soft IRQs run while the SD driver (Python) reads, in sleeps
# and key polls, never inside the (viper) converter. Frame i of the file
# carries i in its low half, so the words that reach the speaker show every
# replay and skip.


def _swap(module, attrs):
    # Stub module attributes; returns what _unswap() puts back (None: a
    # builtin such as open or print, whose stub is deleted again)
    old = {}
    for name in attrs:
        old[name] = getattr(module, name, None)
        setattr(module, name, attrs[name])
    return old


def _unswap(module, old):
    for name in old:
        if old[name] is None:
            delattr(module, name)
        else:
            setattr(module, name, old[name])


_SIM = [None]


class _SimDMA:
    def __init__(self):
        sim = _SIM[0]
        self.channel = len(sim.dmas)
        sim.dmas.append(self)
        self.buf = None
        self.pos = self.reload = self.left = self.ctrl = 0
        self.busy = False
        self.handler = None

    @property
    def read(self):
        return self.buf

    @read.setter
    def read(self, buf):
        self.buf = buf
        self.pos = 0

    @property
    def count(self):
        return self.left

    @count.setter
    def count(self, n):
        self.reload = n

    def pack_ctrl(self, size=2, inc_read=True, inc_write=False, treq_sel=0, chain_to=0, irq_quiet=True):
        return 1 | chain_to << 1 | (treq_sel == 0x3F) << 7  # bit 7: unpaced

    def active(self):
        if self.busy and self.ctrl & 0x80:  # unpaced: runs out at once
            self.left = 0
            self.busy = False
        return self.busy

    def config(self, read=None, write=None, count=None, ctrl=None, trigger=False):
        self.read = read
        self.count = count
        self.ctrl = ctrl
        if trigger:
            self.start(_SIM[0])

    def irq(self, handler=None, hard=False):
        self.handler = handler

    def close(self):
        if self.busy:
            _SIM[0].errors.append("abort of a busy channel")
        if self.ctrl >> 1 & 0x1F != self.channel:
            _SIM[0].errors.append("closed while chained to another channel")

    def start(self, sim):
        if self.buf[0] & 0xFFFF != sim.heard + 1:
            sim.stale += 1  # not the next frames of the file: old samples
        self.busy = True
        self.left = self.reload

    def move(self, sim):
        if self.pos >= len(self.buf):  # READ_ADDR not reset before a chain
            if "read past a buffer" not in sim.errors:
                sim.errors.append("read past a buffer")
            self.pos = 0
        word = self.buf[self.pos]
        sim.mem[sim.cc] = word
        low = word & 0xFFFF
        if low != sim.last + 1:
            sim.jumps += 1
        sim.last = low
        sim.heard = max(sim.heard, low)
        sim.words += 1
        self.pos += 1
        self.left -= 1
        if self.left <= 0:
            self.busy = False
            sim.pending.append(self)
            chain = self.ctrl >> 1 & 0x1F
            if chain != self.channel:
                sim.dmas[chain].start(sim)


class _SimMem:
    def __getitem__(self, addr):
        return _SIM[0].mem.get(addr, 0)

    def __setitem__(self, addr, value):
        sim = _SIM[0]
        sim.mem[addr] = value
        if addr == sim.cc:
            sim.ramp.append(value)  # Python's writes of the level (the DMA's aren't kept)


class _SimMachine:
    mem32 = _SimMem()


_SimMachine.freq = lambda: 150000000
_SimMachine.PWM = lambda pin: pin
_SimMachine.Pin = lambda n: n


class _SimRp2:
    DMA = _SimDMA


class _SimWav:
    def __init__(self, data, sim):
        self.data = data
        self.sim = sim
        self.pos = 0
        self.closed = False

    def seek(self, offset, whence=0):
        self.pos = (0, self.pos, len(self.data))[whence] + offset
        return self.pos

    def tell(self):
        return self.pos

    def read(self, n):
        out = self.data[self.pos : self.pos + n]
        self.pos += len(out)
        return out

    def readinto(self, buf):
        out = self.read(len(buf))
        buf[: len(out)] = out
        for _ in range((len(out) + 63) // 64):
            self.sim.run(64000 // self.sim.kbs)
        return len(out)

    def close(self):
        self.closed = True


class _PlaySim:
    def __init__(self, rate, kbs, keys):
        import music

        self.period = 1000000 // rate  # us per sample
        self.kbs = kbs  # SD speed, bytes per ms
        self.keys = list(keys)  # (ms, key)
        self.t = 0
        self.tick = None
        self.mem = {}
        self.dmas = []
        self.pending = []
        self.errors = []
        self.ramp = []
        self.last = self.heard = -1
        self.words = self.jumps = self.stale = self.polls = 0
        self.csr = music._reg(music._PACER, music._CSR)
        self.cc = music._reg(music._AUDIO, music._CC)

    def run(self, us):
        end = self.t + us
        while self.mem.get(self.csr, 0) & 1:
            if self.tick is None:
                self.tick = self.t + self.period
            if self.tick > end:
                break
            self.t = self.tick
            self.tick += self.period
            for dma in self.dmas:
                if dma.busy and dma.ctrl & 1:
                    dma.move(self)
                    break
        else:
            self.tick = None  # pacer off
        self.t = end
        while self.pending:
            dma = self.pending.pop(0)
            if dma.handler is not None:
                dma.handler(dma)

    def sleep(self, ms):
        self.run(ms * 1000)

    def key(self):
        self.polls += 1
        self.run(300)  # an I2C read
        if self.keys and self.t >= self.keys[0][0] * 1000:
            return self.keys.pop(0)[1]
        return None


def _sim_convert(data, out, frames, channels, bits, volume):
    for i in range(frames):
        at = 4 * i
        out[i] = data[at] | data[at + 1] << 8 | data[at + 2] << 16 | data[at + 3] << 24


def _play_sim(frames, rate, kbs, keys=(), extra=None):
    # (play_file's result or exception, the model) for a 16-bit stereo file
    # of `frames`; `extra`: more module attributes to stub
    import music

    sim = _PlaySim(rate, kbs, keys)
    _SIM[0] = sim
    pcm = bytearray(4 * frames)
    for i in range(frames):
        pcm[4 * i] = i & 0xFF
        pcm[4 * i + 1] = i >> 8 & 0xFF
        pcm[4 * i + 2] = 0x23  # a right half unlike the left
        pcm[4 * i + 3] = 0x01
    wav = _SimWav(_wav_bytes(2, rate, 16, bytes(pcm)), sim)
    stubs = {
        "FRAMES": 256,
        "convert": _sim_convert,
        "open": lambda path, mode="r": wav,
        "print": lambda *args, **kw: None,
        "_sleep_ms": sim.sleep,
        "_ticks_ms": lambda: sim.t // 1000,
        "_poll_key": sim.key,
        "_sd_card": lambda: None,
        "_screen_header": lambda *args: None,
    }
    stubs.update(extra or {})
    saved = _swap(music, stubs)
    modules = {"rp2": sys.modules.get("rp2"), "machine": sys.modules.get("machine")}
    sys.modules["rp2"] = _SimRp2
    sys.modules["machine"] = _SimMachine
    sim.file = wav
    try:
        result = music.play_file("x.wav", "x")
    except Exception as error:
        result = error
    finally:
        _unswap(music, saved)
        for name in modules:
            if modules[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = modules[name]
        _SIM[0] = None
    return result, sim


def test_music_play_gaps():
    import music

    total = 10 * 256 + 37  # 4000 Hz stereo 16-bit: 16 bytes per ms
    result, sim = _play_sim(total, 4000, 200)
    stats = music.last_stats
    check("fast SD: to the end", result, "end")
    check("fast SD: every frame once, in order", (sim.words, sim.jumps), (total, 0))
    check("fast SD: no gaps", stats["gaps"], 0)
    check("fast SD: frames played", stats["frames"], total)
    check("fast SD: file closed, no channel aborted", (sim.file.closed, sim.errors), (True, []))
    result, sim = _play_sim(total, 4000, 8)  # the SD at half the data rate
    stats = music.last_stats
    check("slow SD: to the end", result, "end")
    check("slow SD: old samples replayed", sim.stale > 1 and sim.words > total, True)
    check("slow SD: every replay is a gap", stats["gaps"], sim.stale)
    check("slow SD: played time from frames read, not replays", stats["frames"], total)
    check("slow SD: no channel aborted", sim.errors, [])


def test_music_keys_ramp_memory():
    import music

    result, sim = _play_sim(10 * 256 + 37, 4000, 200, keys=[(300, "q")])
    check("q stops", result, "quit")
    check("q: file closed, no channel aborted", (sim.file.closed, sim.errors), (True, []))
    check("keys polled every 50 ms at most", sim.polls <= sim.t // 50000 + 1, True)
    check("ramp up: both sides to the middle", (sim.ramp[1], sim.ramp[33]), (0, 512 << 16 | 512))
    down = sim.ramp[-33:]
    check("ramp down from both sides' levels", (down[0], down[-1]), (0x0123 << 16 | sim.last, 0))
    rights = [word >> 16 for word in down]
    steps = [a - b for a, b in zip(rights, rights[1:])]
    check("right side slides down", (min(steps) >= 0, max(steps) <= 10), (True, True))

    def no_memory(n):
        raise MemoryError("memory allocation failed")

    result, sim = _play_sim(1000, 4000, 200, extra={"bytearray": no_memory})
    check("no memory: raised, file closed", (isinstance(result, MemoryError), sim.file.closed), (True, True))
    out = []
    waits = []
    picks = [("enter", 0), ("q", 0)]

    def play_file(path, name):
        raise MemoryError("memory allocation failed")

    saved = _swap(
        music,
        {
            "songs": lambda: [("a", "/sd/music/a.wav", 10)],
            "_pick": lambda title, labels, hints=None, pos=0: picks.pop(0),
            "play_file": play_file,
            "_wait_key": lambda *args: waits.append(1),
            "_clear_screen": lambda: None,
            "print": lambda *args, **kw: out.append(" ".join(str(a) for a in args)),
        },
    )
    try:
        check("player stays up on MemoryError", music.player(), None)
        check("told, then back to the list", ("Can't play: memory" in out[0], waits, picks), (True, [1], []))
    finally:
        _unswap(music, saved)


# clock


class _ClockTime:
    # clock_ntp's time module in virtual milliseconds
    ms = 0

    def time(self):
        return _ClockTime.ms // 1000

    def gmtime(self, secs):
        import time

        return time.gmtime(secs)


def test_clock_live():
    # Across the end of EU summer time (2026-10-25 01:00 UTC) and two
    # minute changes.
    import time
    import clock_ntp

    start = 1792889998 * 1000 + 500  # 00:59:58.5 UTC
    switch = 1792890000 * 1000
    stop = switch + 60200  # a key at 01:01:00.2
    Clock = _ClockTime
    Clock.ms = start

    def sleep(ms):
        Clock.ms += ms

    def poll():
        return "q" if Clock.ms >= stop else None

    def read_key(timeout_ms=None):  # the old loop's wait
        sleep(timeout_ms or 0)
        return poll()

    reads = []

    def offset():
        reads.append(Clock.ms)
        return 2 if Clock.ms < switch else 1

    drawn = []
    out = []
    saved = _swap(
        clock_ntp,
        {
            "time": Clock(),
            "_get_offset": offset,
            "_sleep_ms": sleep,
            "_poll_key": poll,
            "_read_key": read_key,
            "_block_rows": lambda rows, row, col, fg: drawn.append((Clock.ms, rows)),
            "_screen_header": lambda title: out.append("<header>"),
            "_title_bar": lambda title: "<" + title + " bar>",
            "print": lambda *args, **kw: out.append("".join(str(a) for a in args)),
        },
    )
    try:
        check("live returns", clock_ntp.live(), True)
    finally:
        _unswap(clock_ntp, saved)
    text = "".join(out)

    def digits(secs):
        t = time.gmtime(secs)
        return clock_ntp.big_lines("{:02d}:{:02d}:{:02d}".format(t[3], t[4], t[5]))

    check("zone read once a minute", len(reads), 3)
    check("one draw a second", len(drawn), 63)
    check("each within 50 ms of its second", max(ms % 1000 for ms, rows in drawn[1:]) < 50, True)
    check("summer time to its last second", drawn[1][1], digits(switch // 1000 - 1 + 7200))
    check("winter time from the switch", drawn[2][1], digits(switch // 1000 + 3600))
    check("title bar repainted each minute", text.count("\x1b[1;1H<Clock bar>"), 2)
    check("date line only when it changes", text.count("\x1b[3;1H"), 2)
    check("hint printed once", text.count("any key"), 1)


def test_clock_zone_saves():
    import clock_ntp

    saves = []
    ok = [True]
    out = []

    def save(data):
        saves.append(dict(data))
        return ok[0]

    saved = _swap(
        clock_ntp,
        {
            "_load_config": lambda: {"utc_offset": 1, "dst": "eu"},
            "_save_config": save,
            "print": lambda *args, **kw: out.append(" ".join(str(a) for a in args)),
        },
    )
    try:
        check("same offset: no flash write", (clock_ntp.set_utc_offset(1), saves), (True, []))
        check("same summer time: no flash write", (clock_ntp.set_dst(True), saves), (True, []))
        ok[0] = False
        check("failed save reported", (clock_ntp.set_utc_offset(0), out[-1]), (False, "UTC offset not saved."))
        check("failed DST save reported", (clock_ntp.set_dst(False), out[-1]), (None, "EU DST not saved."))
        ok[0] = True
        check("new offset saved", (clock_ntp.set_utc_offset(0), saves[-1]), (True, {"utc_offset": 0, "dst": "eu"}))
        check("summer time off saved", (clock_ntp.set_dst(False), saves[-1]), (False, {"utc_offset": 1}))
    finally:
        _unswap(clock_ntp, saved)


# system


class _KeyPresses:
    # _key_byte() for the key test: a press's bytes come together, then a
    # pause (a read with a timeout gets None)
    def __init__(self, presses):
        self.presses = list(presses)
        self.tail = []

    def __call__(self, timeout_ms=None):
        if timeout_ms is not None:
            return self.tail.pop(0) if self.tail else None
        press = self.presses.pop(0)
        self.tail = list(press[1:])
        return press[0]


class _LogFile:
    writes = []

    def __init__(self, path, mode):
        self.path = path
        self.mode = mode

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, text):
        _LogFile.writes.append((self.path, self.mode, text))
        return len(text)

    def flush(self):
        pass

    def close(self):
        pass


def test_status_key_log_batches():
    import sys_status

    out = []
    saved = _swap(
        sys_status,
        {
            "open": _LogFile,
            "print": lambda *args, **kw: out.append(" ".join(str(a) for a in args)),
            "screen_header": lambda title: None,
        },
    )
    del _LogFile.writes[:]
    try:
        pu._key_byte = _KeyPresses([b"a", b"\x1b[A", b"q"])
        sys_status.keys()
        log = "61               'a'\n1b 5b 41         'up'\n71               'q'\n"
        check("one write at the end", _LogFile.writes, [("keylog.txt", "a", log)])
        del _LogFile.writes[:]
        pu._key_byte = _KeyPresses([b"x"] * 44 + [b"q"])
        sys_status.keys()
        check("a write per 20 keys", [w[2].count("\n") for w in _LogFile.writes], [20, 20, 5])
        del _LogFile.writes[:]
        pu._key_byte = _KeyPresses([b"q"])
        sys_status.keys(log=False)
        check("no log, no write", _LogFile.writes, [])
    finally:
        pu._key_byte = REAL_KEY_BYTE
        _unswap(sys_status, saved)


def test_status_colour_samples():
    import sys_status

    out = []
    saved = _swap(
        sys_status,
        {
            "print": lambda *args, **kw: out.append(" ".join(str(a) for a in args)),
            "screen_header": lambda title: None,
        },
    )
    try:
        sys_status.colors()
    finally:
        _unswap(sys_status, saved)
    black = [n for n in range(16) if pu.paint(" sample ", pu.BLACK, n) in out[n]]
    white = [n for n in range(16) if pu.paint(" sample ", pu.BWHITE, n) in out[n]]
    check("black text on the light colours", black, [7, 10, 11, 13, 14, 15])
    check("white on the rest", white, [0, 1, 2, 3, 4, 5, 6, 8, 9, 12])


def test_status_text_battery():
    saved = (pu.battery, pu.wifi_connected, pu.clock_synced, pu.utc_offset_hours)
    try:
        pu.battery = lambda: (87, True)
        pu.wifi_connected = lambda: True
        pu.clock_synced = lambda: False
        pu.utc_offset_hours = lambda: 0
        pu.refresh_status()
        check("battery labelled", pu.status_text(), "WiFi  Bat 87%+")
        pu.battery = lambda: None
        pu.refresh_status()
        check("no reading, no battery", pu.status_text(), "WiFi")
    finally:
        pu.battery, pu.wifi_connected, pu.clock_synced, pu.utc_offset_hours = saved
        pu.refresh_status()


run(globals())
