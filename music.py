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
from pico_utils import bar as _bar, clear_screen as _clear_screen, clip as _clip
from pico_utils import format_bytes as _format_bytes, key_bar as _key_bar
from pico_utils import paint as _paint, pick as _pick, poll_key as _poll_key
from pico_utils import screen_header as _screen_header, sleep_ms as _sleep_ms
from pico_utils import ticks_diff as _ticks_diff, ticks_ms as _ticks_ms
from pico_utils import title_bar as _title_bar, wait_key as _wait_key
from pico_utils import BCYAN, BGREEN, BRED, BYELLOW, GREY


MODULE_VERSION = "2026-10-07.2"
MUSIC_DIRS = ("/sd/music", "/sd")
FRAMES = 2048  # samples per DMA buffer: 93 ms at 22,050 Hz
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
            body = f.read(size)
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
            found.append((name[:-4], folder.rstrip("/") + "/" + name, size))
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


def _draw(name, info, total_s):
    channels, rate, bits = info[:3]
    _screen_header("Music")
    print("\x1b[?25l", end="")  # no blinking cursor while playing
    print(" " + _paint("ƒ " + _clip(name, DISPLAY_WIDTH - 4), BCYAN))
    print(_paint("   {} Hz  {}  {}-bit  {}".format(rate, "stereo" if channels == 2 else "mono", bits, _clock(total_s)), GREY))
    if rate * channels * bits // 8 > MAX_BYTES_PER_S:
        print(_paint("   High rate: it may stutter (22050 Hz is plenty)", BYELLOW))
    print("\x1b[11;1H" + _paint("─" * DISPLAY_WIDTH, GREY))
    print(
        "  ".join(
            _paint(k, BYELLOW) + " " + v
            for k, v in (("Space", "pause"), ("←→", "track"), ("+/-", "volume"), ("q", "stop"))
        ),
        end="",
    )


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


def play_file(path, name=None):
    """Play one WAV file. Returns "end", "next", "prev" or "quit".
    Space pauses, +/- volume, left/right track, q stops."""
    global _volume
    import rp2
    from array import array
    from machine import mem32

    f = open(path, "rb")
    try:
        channels, rate, bits, offset, size = wav_info(f)
    except Exception:
        f.close()
        raise
    frame_bytes = channels * bits // 8
    total = size // frame_bytes
    total_s = total / rate
    raw = bufs = mv = None  # allocated in the try: a MemoryError closes the file
    sd = _sd_card()
    ch = []
    free = []
    ready = [1, 1]  # buffer k holds samples from the file not played yet
    stats = [0, 0, -1]  # frames played, gaps, last channel (-1: not yet known)
    counts = [0, 0]
    result = "end"
    pins = None
    started = _ticks_ms()

    def fill(k, left):
        want = min(FRAMES, left)
        got = f.readinto(mv[: want * frame_bytes]) or 0
        n = got // frame_bytes
        if n:
            convert(raw, bufs[k], n, channels, bits, _volume)
        counts[k] = n
        ready[k] = 1
        return n

    def done(dma):
        # Buffer k finished and the DMA chained into the other. Only frames
        # read from the file count as played: when the SD falls behind, the
        # DMA replays a buffer not refilled yet, which moves nothing on.
        k = 0 if dma is ch[0] else 1
        dma.read = bufs[k]  # READ_ADDR isn't reloaded on a chain trigger
        fresh = ready[k]
        ready[k] = 0
        stats[0] += counts[k] * fresh
        if k == stats[2]:
            free.append(-1)  # the last buffer finished
            return
        if not ready[1 - k]:  # chained into a buffer not refilled yet: a gap
            stats[1] += 1
        if fresh:  # a replayed buffer's refill is queued already
            free.append(k)

    try:
        gc.collect()
        raw = bytearray(FRAMES * frame_bytes)
        bufs = (array("I", bytearray(4 * FRAMES)), array("I", bytearray(4 * FRAMES)))
        mv = memoryview(raw)
        if sd is not None:
            sd.init_spi(SD_BAUDRATE)
        f.seek(offset)
        left = total
        left -= fill(0, left)
        left -= fill(1, left)
        ch = [rp2.DMA(), rp2.DMA()]
        dreq = _DREQ_PWM_WRAP0 + _PACER
        write = _reg(_AUDIO, _CC)

        def ctrl(k, chain):
            other = ch[1 - k].channel if chain else ch[k].channel
            return ch[k].pack_ctrl(
                size=2, inc_read=True, inc_write=False, treq_sel=dreq, chain_to=other, irq_quiet=False
            )

        last = 0 if counts[1] == 0 else (1 if left == 0 else -1)
        stats[2] = last
        ch[1].config(read=bufs[1], write=write, count=max(1, counts[1]), ctrl=ctrl(1, last != 1))
        ch[0].irq(handler=done, hard=False)
        ch[1].irq(handler=done, hard=False)
        _pacer_setup(rate)
        pins = _audio_on()
        _draw(name or path, (channels, rate, bits), total_s)
        ch[0].config(read=bufs[0], write=write, count=counts[0], ctrl=ctrl(0, last != 0), trigger=True)
        _pacer(True)
        paused = False
        shown = None
        progress = polled = _ticks_ms()
        while True:
            if free:
                progress = _ticks_ms()
                k = free.pop(0)
                if k == -1:
                    break  # end of the file
                if stats[2] == -1:
                    n = fill(k, left)
                    left -= n
                    if n == 0:  # nothing left: the playing channel is the last
                        stats[2] = 1 - k
                        ch[1 - k].ctrl = ctrl(1 - k, False)
                    else:
                        ch[k].count = n
                        if left <= 0:
                            stats[2] = k
                            ch[k].ctrl = ctrl(k, False)
            now = _ticks_ms()
            key = None
            if _ticks_diff(now, polled) >= KEY_MS:
                polled = now
                key = _poll_key()
            if key in _QUIT:
                result = "quit"
                break
            if key in ("right", "n"):
                result = "next"
                break
            if key in ("left", "p"):
                result = "prev"
                break
            if key == " ":
                paused = not paused
                _pacer(not paused)
                shown = None
                progress = now
            elif key in ("+", "=", "up") and _volume < VOLUME_MAX:
                _volume += 1
                shown = None
            elif key in ("-", "_", "down") and _volume > 0:
                _volume -= 1
                shown = None
            if not paused and _ticks_diff(now, progress) > STALL_MS:
                raise OSError("audio stalled")  # no buffer finished: never loop on stale samples
            if shown is None or _ticks_diff(now, shown) >= 500:
                _status(stats[0] / rate, total_s, paused, stats[1])
                shown = now
            if not free:
                _sleep_ms(5)
    finally:
        _pacer(False)  # no more requests: a busy channel waits mid-buffer
        for dma in ch:
            dma.irq(handler=None)
        scratch = _drain(ch)
        for dma in ch:
            # close() aborts: only idle channels by now (_drain); active(0)
            # aborts an enabled one, which can spin forever (erratum E5)
            dma.close()
        scratch = None
        if pins is not None:
            _ramp(mem32[_reg(_AUDIO, _CC)], 0)
        if sd is not None:
            sd.init_spi(SD_DEFAULT_BAUDRATE)
        f.close()
        print("\x1b[?25h", end="")
        last_stats.update(frames=stats[0], total=total, gaps=stats[1], ms=_ticks_diff(_ticks_ms(), started), rate=rate)
        bufs = raw = mv = None
        gc.collect()
    return result


def player(start=0):
    """Track list: Enter plays from there to the end of the list."""
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
    labels = [t[0] for t in tracks]
    hints = [_format_bytes(t[2]) for t in tracks]
    pos = min(start, len(tracks) - 1)
    while True:
        key, pos = _pick("Music", labels, hints, pos=pos)
        if key in _QUIT or key == "left":
            _clear_screen()
            return None
        if key not in ("enter", "right"):
            continue
        while 0 <= pos < len(tracks):
            try:
                result = play_file(tracks[pos][1], tracks[pos][0])
            except (OSError, ValueError, MemoryError) as error:
                print("\x1b[13;1H" + _paint("Can't play: {}".format(error), BRED))
                _wait_key()
                result = "quit"
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
        print(name, _format_bytes(size), path)


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
