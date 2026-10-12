"""Music: WAV files from the SD card on the PicoCalc speakers and headphone
jack. MP3 can't be decoded in MicroPython here: convert on the computer
first (tools/to_wav.sh makes 16-bit PCM, 22,050 Hz, stereo).

How it plays: the speakers hang off PWM slice 5 (GP26 left, GP27 right),
run with a ~146 kHz carrier and 10-bit duty. PWM slice 11 has no pins on
the RP2350A: it wraps at the sample rate and its DREQ paces two chained DMA
channels that write one 32-bit word per sample (left duty in the low half,
right in the high half) into slice 5's compare register. Python only
refills the idle buffer from the SD card and converts it (viper)."""

import gc
import os
import sys

from pico_utils import DISPLAY_WIDTH
from pico_utils import clean as _clean
from pico_utils import bar as _bar, clear_screen as _clear_screen, clip as _clip
from pico_utils import format_bytes as _format_bytes, key_bar as _key_bar
from pico_utils import paint as _paint, pick as _pick, poll_key as _poll_key
from pico_utils import screen_header as _screen_header, sleep_ms as _sleep_ms
from pico_utils import ticks_diff as _ticks_diff, ticks_ms as _ticks_ms
from pico_utils import title_bar as _title_bar, wait_key as _wait_key
from pico_utils import set_status_extra as _set_status_extra, set_tick as _set_tick
from pico_utils import BCYAN, BGREEN, BRED, BYELLOW, GREY


MODULE_VERSION = "2026-10-12.3"
MUSIC_DIRS = ("/sd/music", "/sd")
FRAMES = 4096  # samples per DMA buffer: 186 ms at 22,050 Hz
STEP = 1024  # samples read and converted per pump step
SD_BAUDRATE = 8000000  # while playing: 214 KB/s measured, same data up to 20 MHz
SD_DEFAULT_BAUDRATE = 1320000  # the firmware's sdcard driver default, restored after
MAX_BYTES_PER_S = 140000  # what the SD keeps up with next to the screen
VOLUME_MAX = 16
STALL_MS = 2000  # no finished buffer for this long while playing: stop
KEY_MS = 50  # keyboard poll period while playing: each poll is an I2C read
_QUIT = ("q", "Q", "esc", "eof")

# RP2350 PWM registers (pico-sdk hardware/regs/pwm.h, dreq.h)
_PWM_BASE = 0x400A8000
_AUDIO = 5  # slice of GP26/GP27
_PACER = 11  # no pins on the RP2350A
_DREQ_PWM_WRAP0 = 32
_CSR, _DIV, _CC, _TOP_REG = 0x00, 0x04, 0x0C, 0x10
_TOP = 1023  # 10-bit duty: 150 MHz / 1024 = 146 kHz carrier
_MID = 512

_volume = 10
last_stats = {}  # of the last play_file(): frames, gaps, ms, rate


def _reg(slice_, offset):
    return _PWM_BASE + slice_ * 0x14 + offset


# ── WAV and samples ─────────────────────────────


def wav_info(f):
    """(channels, rate, bits, data_offset, data_bytes) of a PCM WAV file, f
    positioned anywhere. Skips LIST, FLLR (afconvert) and other chunks.
    Raises ValueError for anything this player can't use."""
    f.seek(0)
    head = f.read(12)
    if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    fmt = None
    while True:
        chunk = f.read(8)
        if len(chunk) < 8:
            raise ValueError("no audio data")
        size = int.from_bytes(chunk[4:8], "little")
        if chunk[0:4] == b"fmt ":
            # at most 40 bytes (WAVE_FORMAT_EXTENSIBLE): a crafted size asked
            # for one allocation as big as the declared chunk
            body = f.read(min(size, 40))
            if size > 40:
                f.seek(size - 40, 1)
            kind = int.from_bytes(body[0:2], "little")
            channels = int.from_bytes(body[2:4], "little")
            rate = int.from_bytes(body[4:8], "little")
            bits = int.from_bytes(body[14:16], "little")
            if kind == 0xFFFE and len(body) >= 26:  # WAVE_FORMAT_EXTENSIBLE
                kind = int.from_bytes(body[24:26], "little")
            if kind != 1:
                raise ValueError("not PCM (convert with tools/to_wav.sh)")
            if channels not in (1, 2) or bits not in (8, 16):
                raise ValueError("{} ch {}-bit: use mono/stereo 8/16-bit".format(channels, bits))
            if not 4000 <= rate <= 48000:
                raise ValueError("{} Hz: use 4000-48000".format(rate))
            fmt = (channels, rate, bits)
            if size & 1:
                f.read(1)
        elif chunk[0:4] == b"data":
            if fmt is None:
                raise ValueError("data before format")
            offset = f.tell()
            end = f.seek(0, 2)
            f.seek(offset)
            if end is not None and end - offset < size:
                size = end - offset  # streamed or cut files
            if size < fmt[0] * fmt[2] // 8:
                # not one frame: the player would start a buffer it never
                # filled (a click from the stale word)
                raise ValueError("no audio data")
            return fmt + (offset, size)
        else:
            f.seek(size + (size & 1), 1)


def convert_reference(data, frames, channels, bits, volume):
    """PCM bytes -> PWM words: duty 0..1023 per side, left low, right high.
    The pure-Python twin of the viper converter (tests compare them)."""
    out = []
    step = channels * bits // 8
    for i in range(frames):
        sides = []
        for c in range(channels):
            at = i * step + c * (bits // 8)
            if bits == 16:
                s = data[at] | (data[at + 1] << 8)
                if s >= 32768:
                    s -= 65536
                s >>= 6
            else:
                s = (data[at] - 128) << 2
            sides.append(_MID + ((s * volume) >> 4))
        left = sides[0]
        right = sides[1] if channels == 2 else left
        out.append((right << 16) | left)
    return out


_VIPER_SRC = """
@micropython.viper
def conv16(src: ptr16, dst: ptr32, frames: int, channels: int, volume: int):
    j = 0
    for i in range(frames):
        s = int(src[j])
        if s >= 32768:
            s -= 65536
        left = 512 + (((s >> 6) * volume) >> 4)
        if channels == 2:
            s = int(src[j + 1])
            if s >= 32768:
                s -= 65536
            right = 512 + (((s >> 6) * volume) >> 4)
            j += 2
        else:
            right = left
            j += 1
        dst[i] = (right << 16) | left

@micropython.viper
def conv8(src: ptr8, dst: ptr32, frames: int, channels: int, volume: int):
    j = 0
    for i in range(frames):
        left = 512 + ((((int(src[j]) - 128) << 2) * volume) >> 4)
        if channels == 2:
            right = 512 + ((((int(src[j + 1]) - 128) << 2) * volume) >> 4)
            j += 2
        else:
            right = left
            j += 1
        dst[i] = (right << 16) | left
"""
_VIPER = None
if sys.implementation.name == "micropython":
    try:  # exec: a build without viper fails here, not at import
        _ns = {}
        exec(_VIPER_SRC, _ns)
        _VIPER = (_ns["conv16"], _ns["conv8"])
    except Exception:
        _VIPER = None


def convert(data, out, frames, channels, bits, volume):
    """PCM bytes into `out` (array of 32-bit words); viper when available."""
    if _VIPER is not None:
        (_VIPER[0] if bits == 16 else _VIPER[1])(data, out, frames, channels, volume)
        return
    for i, word in enumerate(convert_reference(data, frames, channels, bits, volume)):
        out[i] = word


def _clock(seconds):
    seconds = int(seconds)
    return "{}:{:02d}".format(seconds // 60, seconds % 60)


# ── tracks ──────────────────────────────────────


def _entries(folder):
    # (name, is_dir, size); the unix port's ilistdir has no size, CPython none
    if hasattr(os, "ilistdir"):
        for item in os.ilistdir(folder):
            is_dir = (item[1] & 0x4000) != 0
            if len(item) > 3 or is_dir:
                yield item[0], is_dir, item[3] if len(item) > 3 else 0
            else:
                yield item[0], False, os.stat(folder + "/" + item[0])[6]
        return
    for name in os.listdir(folder):
        st = os.stat(folder + "/" + name)
        yield name, (st[0] & 0xF000) == 0x4000, st[6]


def songs(dirs=MUSIC_DIRS):
    """[(name, path, size)] of the .wav files in `dirs`, by name; dot files
    (macOS ._ copies) and folders skipped."""
    found = []
    for folder in dirs:
        try:
            items = list(_entries(folder))
        except OSError:
            continue
        for name, is_dir, size in items:
            if is_dir or name.startswith(".") or not name.lower().endswith(".wav"):
                continue
            found.append((_clean(name[:-4]), folder.rstrip("/") + "/" + name, size))
    found.sort(key=lambda song: song[0].lower())
    return found


# ── hardware ────────────────────────────────────


def _sd_card():
    try:
        import picocalc

        return picocalc.sd.sd
    except Exception:
        return None


def _ramp(start, end):
    # Slide the compare word from `start` to `end` (left duty low, right
    # high), each side from its own level, so the speakers don't click.
    from machine import mem32

    l0, r0 = start & 0xFFFF, (start >> 16) & 0xFFFF
    l1, r1 = end & 0xFFFF, (end >> 16) & 0xFFFF
    for k in range(33):
        left = l0 + (l1 - l0) * k // 32
        right = r0 + (r1 - r0) * k // 32
        mem32[_reg(_AUDIO, _CC)] = (right << 16) | left
        _sleep_ms(1)


def _audio_on():
    from machine import PWM, Pin, mem32

    pins = (PWM(Pin(26)), PWM(Pin(27)))  # GPIO function -> PWM
    mem32[_reg(_AUDIO, _CSR)] = 0
    mem32[_reg(_AUDIO, _DIV)] = 0x10  # 1.0 (8.4 fixed point)
    mem32[_reg(_AUDIO, _TOP_REG)] = _TOP
    mem32[_reg(_AUDIO, _CC)] = 0
    mem32[_reg(_AUDIO, _CSR)] = 1
    _ramp(0, (_MID << 16) | _MID)
    return pins


def _pacer_setup(rate):
    from machine import freq, mem32

    clock = freq()
    div = 1
    period = (clock + rate // 2) // rate
    while period > 65536:
        div *= 2
        period = (clock // div + rate // 2) // rate
    mem32[_reg(_PACER, _CSR)] = 0
    mem32[_reg(_PACER, _DIV)] = div << 4
    mem32[_reg(_PACER, _TOP_REG)] = period - 1


def _pacer(on):
    from machine import mem32

    mem32[_reg(_PACER, _CSR)] = 1 if on else 0


def _drain(ch):
    # A track stopped mid-buffer leaves a channel busy and chained to the
    # other. Clearing its CTRL makes CHAIN_TO 0 (a channel the display or
    # Wi-Fi owns), and aborting a busy channel is erratum RP2350-E5: after a
    # stop mid-track the next SD read hung the device (USB dead, measured
    # 2026-10-07). So both channels finish their buffers unpaced and
    # unchained, into a scratch word instead of the PWM, and close() then
    # aborts idle channels. Returns the scratch word: keep it until closed.
    from array import array

    scratch = array("I", (0,))
    for dma in ch:
        dma.write = scratch
        dma.ctrl = dma.pack_ctrl(size=2, inc_read=True, inc_write=False, treq_sel=0x3F, chain_to=dma.channel)
    start = _ticks_ms()
    while any(dma.active() for dma in ch) and _ticks_diff(_ticks_ms(), start) < 20:
        pass
    return scratch


# ── playing ─────────────────────────────────────


_KEYS = (("Space", "pause"), ("←→", "track"), ("+/-", "vol"), ("b", "background"), ("q", "stop"))


def _draw(name, info, total_s, background=True):
    channels, rate, bits = info[:3]
    _screen_header("Music")
    print("\x1b[?25l", end="")  # no blinking cursor while playing
    print(" " + _paint("ƒ " + _clip(name, DISPLAY_WIDTH - 4), BCYAN))
    print(_paint("   {} Hz  {}  {}-bit  {}".format(rate, "stereo" if channels == 2 else "mono", bits, _clock(total_s)), GREY))
    if rate * channels * bits // 8 > MAX_BYTES_PER_S:
        print(_paint("   High rate: it may stutter (22050 Hz is plenty)", BYELLOW))
    print("\x1b[11;1H" + _paint("─" * DISPLAY_WIDTH, GREY))
    keys = _KEYS if background else [pair for pair in _KEYS if pair[0] != "b"]
    print("  ".join(_paint(k, BYELLOW) + " " + v for k, v in keys), end="")


def _status(played_s, total_s, paused, under):
    width = DISPLAY_WIDTH - 14
    fraction = played_s / total_s if total_s else 0
    line = "\x1b[6;1H\x1b[K {:>5} {} {}".format(_clock(played_s), _bar(fraction, width, BCYAN), _clock(total_s))
    line += "\x1b[8;1H\x1b[K Volume {} {}".format(_bar(_volume / VOLUME_MAX, 16, BGREEN), _volume)
    state = _paint("Paused", BYELLOW) if paused else _paint("Playing", BGREEN)
    if under:
        state += _paint("  {} gaps (SD too slow)".format(under), BRED)
    line += "\x1b[9;1H\x1b[K " + state + "\x1b[13;1H"
    print(line, end="")


# ── the engine ──────────────────────────────────
# Two buffers of FRAMES samples, two DMA channels paced by slice 11. A channel
# chains to the other only once that buffer is full (arm()): when the SD falls
# behind, the sound stops for a moment instead of replaying old samples, and
# a track's last chunk always plays (it went unplayed when the SD was late at
# the end). Buffers fill STEP samples at a time from pump(): the player's own
# loop runs it, and in the background every key wait does (pico_utils tick),
# so the SD is never read in the middle of another app's SD access.

FREE, FILLING, FULL, PLAYING = 0, 1, 2, 3


class Player:
    def __init__(self, path, name=None):
        self.path = path
        self.name = name or path.rsplit("/", 1)[-1]
        f = open(path, "rb")
        try:
            self.channels, self.rate, self.bits, self.offset, size = wav_info(f)
        except Exception:
            f.close()
            raise
        self.f = f
        self.frame_bytes = self.channels * self.bits // 8
        self.total = size // self.frame_bytes
        self.left = self.total  # frames not read yet
        self.played = 0
        self.gaps = 0  # dropouts: the SD later than the sound
        self.paused = False
        self.ended = False
        self.ch = []
        self.bufs = self.raw = self.mv = None
        self.count = [0, 0]  # frames in each buffer
        self.state = [FREE, FREE]
        self.filling = None  # the buffer a fill is under way in
        self.last = -1  # the buffer holding the end of the file
        self.done_q = []  # buffers the DMA finished (IRQ), in order
        self.pins = None
        self.sd = _sd_card()
        self.started = self.progress = _ticks_ms()

    # hardware

    def _ctrl(self, k, chain):
        # chain: the channel to start after this one, None for none
        dma = self.ch[k]
        to = self.ch[chain].channel if chain is not None else dma.channel
        return dma.pack_ctrl(
            size=2, inc_read=True, inc_write=False, treq_sel=_DREQ_PWM_WRAP0 + _PACER, chain_to=to, irq_quiet=False
        )

    def _irq(self, dma):
        self.done_q.append(0 if dma is self.ch[0] else 1)

    def start(self):
        """Allocate, fill both buffers, start the sound. Cleans up if it fails."""
        import rp2
        from array import array

        try:
            gc.collect()
            self.raw = bytearray(STEP * self.frame_bytes)
            self.mv = memoryview(self.raw)
            self.bufs = (array("I", bytearray(4 * FRAMES)), array("I", bytearray(4 * FRAMES)))
            if self.sd is not None:
                self.sd.init_spi(SD_BAUDRATE)
            self.f.seek(self.offset)
            for _ in (0, 1):  # one at a time: stop() closes what was claimed
                self.ch.append(rp2.DMA())
            write = _reg(_AUDIO, _CC)
            for k in (0, 1):
                self.ch[k].config(read=self.bufs[k], write=write, count=1, ctrl=self._ctrl(k, None))
                self.ch[k].irq(handler=self._irq, hard=False)
            for k in (0, 1):  # both buffers before the first sample
                self.filling = k
                self.state[k] = FILLING
                while self.filling == k:
                    self._step()
            _pacer_setup(self.rate)
            self.pins = _audio_on()
            self._go(0)
            if self.state[1] == FULL:
                self._arm(1)
            _pacer(True)
        except BaseException:
            self.stop()
            raise
        return self

    def _go(self, k):
        # start buffer k now (nothing playing, or the chain came too late)
        self.state[k] = PLAYING
        self.ch[k].active(1)

    def _arm(self, k):
        # buffer k is full: play it after the other one
        p = 1 - k
        if self.state[p] != PLAYING:
            self._go(k)
            return
        self.ch[p].ctrl = self._ctrl(p, k)
        if self.ch[k].active() or k in self.done_q:
            self.state[k] = PLAYING  # the chain took it
        elif not self.ch[p].active():
            self._go(k)  # p ended before the chain was armed: a dropout
        else:
            self.state[k] = PLAYING  # p still playing: the chain starts k

    def _step(self):
        # one step of the fill under way: STEP frames read and converted
        k = self.filling
        want = min(STEP, FRAMES - self.count[k], self.left)
        got = 0
        if want > 0:
            if self.sd is not None:
                self.sd.init_spi(SD_BAUDRATE)  # another app may have set it back
            got = (self.f.readinto(self.mv[: want * self.frame_bytes]) or 0) // self.frame_bytes
        if got:
            convert(self.raw, memoryview(self.bufs[k])[self.count[k] :], got, self.channels, self.bits, _volume)
            self.count[k] += got
            self.left -= got
        if got and self.count[k] < FRAMES and self.left > 0:
            return
        self.filling = None  # this buffer is done
        if self.count[k] == 0:  # nothing more: the other buffer has the end
            self.state[k] = FREE
            self.last = 1 - k
            if self.state[1 - k] == FREE:
                self.ended = True
            return
        if self.left <= 0 or not got:
            self.last = k
        self.ch[k].count = self.count[k]
        self.state[k] = FULL

    # the work, a step at a time

    def pump(self, steps=1):
        """Handle the buffers the DMA finished and fill the next one, up to
        `steps` steps. Call it often: a buffer lasts FRAMES / rate seconds."""
        while self.done_q:
            k = self.done_q.pop(0)
            if self.state[k] != PLAYING:
                continue
            self.ch[k].read = self.bufs[k]  # READ_ADDR isn't reloaded on a chain
            self.ch[k].ctrl = self._ctrl(k, None)
            self.played += self.count[k]
            self.count[k] = 0
            self.state[k] = FREE
            self.progress = _ticks_ms()
            if k == self.last:
                self.ended = True
                return
            other = 1 - k
            if self.state[other] == FULL:  # full but not chained in time
                self._go(other)
            elif self.state[other] != PLAYING:
                self.gaps += 1
        while steps > 0 and self.last == -1 and not self.ended:
            if self.filling is None:
                k = 0 if self.state[0] == FREE else (1 if self.state[1] == FREE else None)
                if k is None:
                    return
                self.filling = k
                self.state[k] = FILLING
            self._step()
            steps -= 1
            if self.filling is None and self.state[0] != FILLING and self.state[1] != FILLING:
                for k in (0, 1):
                    if self.state[k] == FULL:
                        self._arm(k)

    def stalled(self):
        # a buffer playing that never finishes: the DMA stopped
        playing = PLAYING in self.state
        return playing and not self.paused and _ticks_diff(_ticks_ms(), self.progress) > STALL_MS

    def pause(self, on):
        self.paused = bool(on)
        _pacer(not self.paused)
        self.progress = _ticks_ms()

    def stop(self):
        """Silence, release the DMA channels, the SD clock and the file."""
        from machine import mem32

        _pacer(False)  # no more requests: a busy channel waits mid-buffer
        for dma in self.ch:
            dma.irq(handler=None)
        scratch = _drain(self.ch)
        for dma in self.ch:
            # close() aborts: only idle channels by now (_drain); active(0)
            # aborts an enabled one, which can spin forever (erratum E5)
            dma.close()
        scratch = None
        self.ch = []
        if self.pins is not None:
            _ramp(mem32[_reg(_AUDIO, _CC)], 0)
            self.pins = None
        if self.sd is not None:
            self.sd.init_spi(SD_DEFAULT_BAUDRATE)
        self.f.close()
        last_stats.update(
            frames=self.played, total=self.total, gaps=self.gaps, ms=_ticks_diff(_ticks_ms(), self.started), rate=self.rate
        )
        self.bufs = self.raw = self.mv = None
        gc.collect()


# ── playing ─────────────────────────────────────


def _play_ui(p, background=True):
    """The now-playing screen over a started Player. Returns "end", "next",
    "prev", "quit" or "back" (b, with `background`: it goes on playing in the
    background)."""
    global _volume
    _draw(p.name, (p.channels, p.rate, p.bits), p.total / p.rate, background)
    shown = None
    polled = _ticks_ms()
    while True:
        p.pump(2)
        if p.ended:
            return "end"
        now = _ticks_ms()
        key = None
        if _ticks_diff(now, polled) >= KEY_MS:
            polled = now
            key = _poll_key()
        if key in _QUIT:
            return "quit"
        if key in ("right", "n"):
            return "next"
        if key in ("left", "p"):
            return "prev"
        if key == "b" and background:
            return "back"
        if key == " ":
            p.pause(not p.paused)
            shown = None
        elif key in ("+", "=", "up") and _volume < VOLUME_MAX:
            _volume += 1
            shown = None
        elif key in ("-", "_", "down") and _volume > 0:
            _volume -= 1
            shown = None
        if p.stalled():
            raise OSError("audio stalled")  # a buffer that never ended
        if shown is None or _ticks_diff(now, shown) >= 500:
            _status(p.played / p.rate, p.total / p.rate, p.paused, p.gaps)
            shown = now
        if p.filling is None and not p.done_q:
            _sleep_ms(5)


def play_file(path, name=None):
    """Play one WAV file in the foreground. Returns "end", "next", "prev" or
    "quit". Space pauses, +/- volume, left/right track, q stops. No b: from
    the REPL nothing would pump it in the background (b left the player
    running, its DMA channels, file and SD clock taken)."""
    p = Player(path, name).start()
    try:
        return _play_ui(p, background=False)
    finally:
        print("\x1b[?25h", end="")
        p.stop()


# ── in the background ───────────────────────────
# b on the now-playing screen leaves the player: the music goes on through
# the playlist while other apps run, pumped from every key wait (an app that
# blocks, a network request, pauses it for that long). Opening Music again
# shows it; q there stops it, and so does leaving the launcher.

_BG = [None]  # [player, tracks, position] while playing in the background


def playing():
    """The name of the track playing in the background, or None."""
    bg = _BG[0]
    return bg[0].name if bg is not None else None


def _bg_tick():
    bg = _BG[0]
    if bg is None:
        return
    p = bg[0]
    try:
        p.pump(2)
        if p.stalled():
            raise OSError("audio stalled")
        if not p.ended:
            return
        p.stop()
        pos = bg[2] + 1
        if pos < len(bg[1]):
            bg[0] = Player(bg[1][pos][1], bg[1][pos][0]).start()
            bg[2] = pos
            return
    except Exception:
        try:
            bg[0].stop()
        except Exception:
            pass
    _bg_clear()


def _bg_clear():
    _BG[0] = None
    _set_tick(None)
    _set_status_extra("")


def _bg_start(p, tracks, pos):
    _BG[0] = [p, tracks, pos]
    _set_tick(_bg_tick)
    _set_status_extra("►")  # CP437 has ♪ at 0x0D: drawn as a space


def background_stop():
    """Stop the music playing in the background (the launcher does it when
    it leaves to the REPL)."""
    bg = _BG[0]
    if bg is None:
        return False
    _bg_clear()
    try:
        bg[0].stop()
    except Exception:
        pass
    return True


def player(start=0):
    """Track list: Enter plays from there to the end of the list; b on the
    now-playing screen keeps it playing in the background."""
    bg = _BG[0]
    if bg is not None:  # back to the music already playing
        _bg_clear()
        p, tracks, pos = bg
    else:
        p = None
        tracks = songs()
        if not tracks:
            _screen_header("Music")
            print("No .wav files in /sd/music or /sd.")
            print("")
            print("MP3 can't be decoded on the PicoCalc: convert")
            print("on the computer with tools/to_wav.sh (16-bit,")
            print("22050 Hz, stereo) and copy to /sd/music.")
            _wait_key()
            return None
        pos = min(start, len(tracks) - 1)
    labels = [t[0] for t in tracks]
    hints = [_format_bytes(t[2]) for t in tracks]
    while True:
        if p is None:
            key, pos = _pick("Music", labels, hints, pos=pos)
            if key in _QUIT or key == "left":
                _clear_screen()
                return None
            if key not in ("enter", "right"):
                continue
        while 0 <= pos < len(tracks):
            result = "quit"
            failed = None
            try:
                if p is None:
                    p = Player(tracks[pos][1], tracks[pos][0]).start()
                result = _play_ui(p)
            except (OSError, ValueError, MemoryError) as error:
                failed = error
            finally:
                # Stopped whichever way the screen ends (q, an error, Ctrl+C,
                # a USB host taking over), unless b keeps it playing. Left to
                # the garbage collector, its DMA channels would be closed, so
                # aborted, while still busy (erratum E5, see _drain).
                print("\x1b[?25h", end="")
                if result != "back" and p is not None:
                    p.stop()
                    p = None
            if failed is not None:
                print("\x1b[13;1H" + _paint("Can't play: {}".format(failed), BRED))
                _wait_key()
            if result == "back":
                _bg_start(p, tracks, pos)
                _clear_screen()
                return None
            if result == "quit":
                break
            pos += -1 if result == "prev" else 1
        pos = max(0, min(pos, len(tracks) - 1))


def play(path):
    """Play one file from the REPL: music.play('/sd/music/song.wav')."""
    return play_file(path, path.rsplit("/", 1)[-1])


def volume(level=None):
    """Volume 0-16 (shown and used from the next buffer on)."""
    global _volume
    if level is not None:
        _volume = max(0, min(VOLUME_MAX, int(level)))
    return _volume


def ls():
    for name, path, size in songs():
        print(name, _format_bytes(size), _clean(path))


# ── standard ────────────────────────────────────


def ver():
    print("music:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Music (WAV from /sd/music or /sd) --")
    print("player()  m()   Track list, Enter plays")
    print("play(path) p()  One file")
    print("volume(n)       0-16")
    print("ls()            List tracks")
    print("Keys: Space pause, <- -> track,")
    print("+/- volume, q stop")
    print("MP3: convert with tools/to_wav.sh")


def h():
    return help()


m = player
p = play
