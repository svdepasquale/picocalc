# The music player: WAV parsing, conversion, track lists, and playback on a
# model of the PWM/DMA hardware (foreground and in the background).
try:
    from harness import *
except ImportError:
    pass  # on the device tools/test_device.sh puts harness.py in front


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
        ("no frames rejected", _wav_bytes(2, 22050, 16, b"")),
        ("less than a frame rejected", _wav_bytes(2, 22050, 16, b"\x01\x02\x03")),
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

    def active(self, value=None):
        if value is not None:  # active(1): trigger with the registers as set
            if value:
                self.start(_SIM[0])
            elif self.busy:
                _SIM[0].errors.append("active(0) on a busy channel")
            return value
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
        "STEP": 64,
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
    saved = swap(music, stubs)
    modules = {"rp2": sys.modules.get("rp2"), "machine": sys.modules.get("machine")}
    sys.modules["rp2"] = _SimRp2
    sys.modules["machine"] = _SimMachine
    sim.file = wav
    try:
        result = music.play_file("x.wav", "x")
    except Exception as error:
        result = error
    finally:
        unswap(music, saved)
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
    check("slow SD: no old samples replayed: dropouts instead", (sim.stale, stats["gaps"] > 0), (0, True))
    check("slow SD: every frame once, in order, the end too", (sim.words, sim.jumps), (total, 0))
    check("slow SD: frames played", stats["frames"], total)
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
    result, sim = _play_sim(10 * 256 + 37, 4000, 200, keys=[(300, "b")])
    check("play_file: b ignored (nothing would pump it)", (result, sim.file.closed, sim.errors), ("end", True, []))

    def no_memory(n):
        raise MemoryError("memory allocation failed")

    result, sim = _play_sim(1000, 4000, 200, extra={"bytearray": no_memory})
    check("no memory: raised, file closed", (isinstance(result, MemoryError), sim.file.closed), (True, True))
    out = []
    waits = []
    picks = [("enter", 0), ("q", 0)]

    class NoMemoryPlayer:
        def __init__(self, path, name=None):
            raise MemoryError("memory allocation failed")

    saved = swap(
        music,
        {
            "songs": lambda: [("a", "/sd/music/a.wav", 10)],
            "_pick": lambda title, labels, hints=None, pos=0: picks.pop(0),
            "Player": NoMemoryPlayer,
            "_wait_key": lambda *args: waits.append(1),
            "_clear_screen": lambda: None,
            "print": lambda *args, **kw: out.append(" ".join(str(a) for a in args)),
        },
    )
    try:
        check("player stays up on MemoryError", music.player(), None)
        told = [line for line in out if "Can't play: memory" in line]
        check("told, then back to the list", (len(told), waits, picks), (1, [1], []))
    finally:
        unswap(music, saved)



def test_wav_huge_format_chunk():
    # a declared "fmt " size of 2 GB: one read that big was attempted
    import music

    wav = bytearray(_wav_bytes(1, 22050, 16, bytes(16)))
    wav[16:20] = (0x7FFFFFF0).to_bytes(4, "little")
    try:
        music.wav_info(io.BytesIO(bytes(wav)))
        got = "read"
    except ValueError as error:
        got = str(error)
    check("refused, nothing allocated", got, "no audio data")



def _bg_setup(frames, rate, kbs):
    # stubs for a background run of two tracks of `frames` each
    import music

    sim = _PlaySim(rate, kbs, ())
    _SIM[0] = sim
    pcm = bytearray(4 * frames)
    for i in range(frames):
        pcm[4 * i] = i & 0xFF
        pcm[4 * i + 1] = i >> 8 & 0xFF
    data = _wav_bytes(2, rate, 16, bytes(pcm))
    files = []

    def opener(path, mode="r"):
        files.append(_SimWav(data, sim))
        sim.heard = sim.last = -1  # a new track counts from its frame 0
        return files[-1]

    stubs = {
        "FRAMES": 256,
        "STEP": 64,
        "convert": _sim_convert,
        "open": opener,
        "print": lambda *args, **kw: None,
        "_sleep_ms": sim.sleep,
        "_ticks_ms": lambda: sim.t // 1000,
        "_sd_card": lambda: None,
    }
    saved = swap(music, stubs)
    modules = {"rp2": sys.modules.get("rp2"), "machine": sys.modules.get("machine")}
    sys.modules["rp2"] = _SimRp2
    sys.modules["machine"] = _SimMachine
    return sim, files, saved, modules


def _bg_teardown(saved, modules):
    import music

    unswap(music, saved)
    for name in modules:
        if modules[name] is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = modules[name]
    _SIM[0] = None
    music._bg_clear()


def test_music_background_playlist():
    # b on the player: the playlist goes on from the key waits' tick alone
    import music

    frames = 3 * 256 + 37
    sim, files, saved, modules = _bg_setup(frames, 4000, 200)
    tracks = [("a", "/sd/music/a.wav", 10), ("b", "/sd/music/b.wav", 10)]
    try:
        p = music.Player(tracks[0][1], "a").start()
        music._bg_start(p, tracks, 0)
        check("tick set, the mark shown", (pu._TICK[0] is music._bg_tick, pu._EXTRA[0]), (True, "►"))
        check("playing", music.playing(), "a")
        for _ in range(4000):
            if music._BG[0] is None:
                break
            sim.run(5000)  # a key wait's pass, then its tick
            music._bg_tick()
        check("both tracks to the end", (music._BG[0], sim.words), (None, 2 * frames))
        check("no old samples, no aborts", (sim.stale, sim.errors), (0, []))
        check("files closed", [f.closed for f in files], [True, True])
        check("tick and mark gone", (pu._TICK[0], pu._EXTRA[0]), (None, ""))
    finally:
        _bg_teardown(saved, modules)


def test_music_player_interrupted():
    # Ctrl+C (or a USB host taking over) on the now-playing screen: the player
    # stops there, drained, instead of being left to the garbage collector,
    # whose close() aborts channels that may still be busy
    import music

    sim, files, saved, modules = _bg_setup(10 * 256, 4000, 200)

    def key():
        sim.run(300)
        if sim.t >= 200000:
            raise KeyboardInterrupt
        return None

    picks = [("enter", 0)]
    more = swap(
        music,
        {
            "songs": lambda: [("a", "/sd/music/a.wav", 10)],
            "_pick": lambda title, labels, hints=None, pos=0: picks.pop(0),
            "_poll_key": key,
            "_screen_header": lambda *args: None,
        },
    )
    try:
        try:
            music.player()
            got = "returned"
        except KeyboardInterrupt:
            got = "interrupted"
        check("Ctrl+C goes through", got, "interrupted")
        check("stopped: file closed, nothing aborted", (files[0].closed, sim.errors), (True, []))
        check("pacer off, channels idle", (sim.mem.get(sim.csr, 0) & 1, [d.busy for d in sim.dmas]), (0, [False, False]))
    finally:
        unswap(music, more)
        _bg_teardown(saved, modules)


def test_music_background_stop():
    import music

    sim, files, saved, modules = _bg_setup(4 * 256, 4000, 200)
    try:
        p = music.Player("/sd/music/a.wav", "a").start()
        music._bg_start(p, [("a", "/sd/music/a.wav", 10)], 0)
        for _ in range(20):
            sim.run(5000)
            music._bg_tick()
        check("stopped mid-track", (music.background_stop(), music.playing()), (True, None))
        check("drained, closed, nothing aborted", (sim.errors, files[0].closed), ([], True))
        check("nothing to stop then", music.background_stop(), False)
    finally:
        _bg_teardown(saved, modules)


run(globals())
