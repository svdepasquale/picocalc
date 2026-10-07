"""Audio synthesizer for PicoCalc."""

import gc
import math
from array import array

from pico_utils import (
    clip as _clip,
    clear_screen as _clear_screen,
    screen_header as _screen_header,
    sleep_ms as _sleep_ms,
    read_key as _read_key,
    poll_key as _poll_key,
    DISPLAY_WIDTH,
)


MODULE_VERSION = "2026-10-07.1"

# ── audio ───────────────────────────────────────
SAMPLE_RATE = 22050
CHUNK = 512
BUF_SZ = CHUNK * 2
TBL_LEN = 256

# ── state ───────────────────────────────────────
_pin = 28              # I2S SD (data) pin
_pwm_pins = (26, 27)   # PicoCalc speakers L/R (official boot.py)
# PWM by default: I2S needs an external DAC, and its default SCK/WS
# (GP16/17) are the PicoCalc SD card pins
_use_pwm = True
_vol = 70
_wave = "sine"
_oct = 4
_bpm = 120
_dur = 200

_i2s = None
_i2s_cfg = None
_tbl = None

# ── constants ───────────────────────────────────
WAVES = ("sine", "square", "saw", "triangle")
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F",
              "F#", "G", "G#", "A", "A#", "B")

# piano key -> semitone offset from C
_KEYS = {
    "z": 0, "s": 1, "x": 2, "d": 3, "c": 4,
    "v": 5, "g": 6, "b": 7, "h": 8, "n": 9,
    "j": 10, "m": 11,
}


# ── wavetable ───────────────────────────────────

def _build_table():
    global _tbl
    if _tbl is not None:
        return
    _tbl = array("h", (
        int(32767 * math.sin(6.2831853 * i / TBL_LEN))
        for i in range(TBL_LEN)
    ))
    gc.collect()


# ── I2S audio ───────────────────────────────────

def _init_audio():
    global _i2s, _i2s_cfg
    cfg = (SAMPLE_RATE, 16, 1)
    if _i2s is not None and _i2s_cfg == cfg:
        return _i2s
    _deinit_audio()
    try:
        from machine import I2S, Pin
        _i2s = I2S(
            0,
            sck=Pin(16), ws=Pin(17), sd=Pin(_pin),
            mode=I2S.TX, bits=16, format=I2S.MONO,
            rate=SAMPLE_RATE, ibuf=4096,
        )
        _i2s_cfg = cfg
        return _i2s
    except ImportError:
        print("No I2S on this platform.")
        return None
    except Exception as e:
        print("Audio err:", _clip(str(e), 22))
        return None


def _deinit_audio():
    global _i2s, _i2s_cfg
    if _i2s is not None:
        try:
            _i2s.deinit()
        except Exception:
            pass
        _i2s = None
        _i2s_cfg = None


# ── note helpers ────────────────────────────────

def _midi_freq(midi):
    """MIDI note number to frequency. A4=69=440Hz."""
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def _parse_note(text):
    """Parse note name to MIDI number (C4, A#5, Db3, -)."""
    s = text.strip()
    if not s:
        return None
    if s == "-" or s.lower() == "r":
        return 0

    name = s[0].upper()
    if name not in "CDEFGAB":
        return None

    idx = 1
    acc = 0
    if idx < len(s) and s[idx] == "#":
        acc = 1
        idx += 1
    elif idx < len(s) and s[idx].lower() == "b":
        acc = -1
        idx += 1

    if idx < len(s):
        try:
            o = int(s[idx:])
        except ValueError:
            return None
    else:
        o = _oct

    base = {"C": 0, "D": 2, "E": 4, "F": 5,
            "G": 7, "A": 9, "B": 11}
    semi = base.get(name, 0) + acc
    midi = (o + 1) * 12 + semi
    if midi < 0 or midi > 127:
        return None
    return midi


# ── core tone generation ────────────────────────

def _play_freq_i2s(freq, dur_ms):
    """Generate and play a tone via I2S DAC."""
    _build_table()
    audio = _init_audio()
    if audio is None:
        return False

    phase_inc = int(freq * TBL_LEN * 65536 / SAMPLE_RATE)
    phase = 0
    vol_s = int(_vol * 256 / 100)
    total = max(1, int(SAMPLE_RATE * dur_ms / 1000))
    att = max(1, min(110, total // 4))
    rel = max(1, min(110, total // 4))

    buf = bytearray(BUF_SZ)
    mv = memoryview(buf)
    tbl = _tbl
    is_sine = _wave == "sine"
    is_sq = _wave == "square"
    is_saw = _wave == "saw"
    written = 0

    # no except: Ctrl+C reaches seq(), rtttl() and piano(), which stop on it
    while written < total:
        n = min(CHUNK, total - written)
        for i in range(n):
            ix = (phase >> 16) & 0xFF

            if is_sine:
                smp = tbl[ix]
            elif is_sq:
                smp = 32767 if ix < 128 else -32767
            elif is_saw:
                smp = ix * 257 - 32768
            else:
                if ix < 128:
                    smp = (ix << 9) - 32768
                else:
                    smp = 32767 - ((ix - 128) << 9)

            smp = (smp * vol_s) >> 8

            # attack / release envelope
            pos = written + i
            if pos < att:
                smp = smp * pos // att
            elif pos > total - rel:
                smp = smp * (total - pos) // rel

            if smp < 0:
                smp += 65536

            off = i << 1
            buf[off] = smp & 0xFF
            buf[off + 1] = (smp >> 8) & 0xFF
            # only bits 16-23 index the table; 24 bits keep phase a
            # small int (a 32-bit mask allocated a long int per sample)
            phase = (phase + phase_inc) & 0xFFFFFF

        audio.write(mv[:n << 1])
        written += n

    return True


def _play_freq_pwm(freq, dur_ms):
    """Play a square-wave tone via PWM (PicoCalc built-in speakers)."""
    try:
        from machine import PWM, Pin
        pwms = [PWM(Pin(p)) for p in _pwm_pins]
        hz = max(20, min(20000, int(freq)))
        duty = max(0, min(65535, int(32768 * _vol // 100)))
        # GP26/27 share one PWM slice: set the frequency before any duty
        for pwm in pwms:
            pwm.freq(hz)
        for pwm in pwms:
            pwm.duty_u16(duty)
        try:
            _sleep_ms(int(dur_ms))
        finally:  # silent and released, and Ctrl+C goes on to stop the tune
            for pwm in pwms:
                pwm.duty_u16(0)
            for pwm in pwms:
                pwm.deinit()
        return True
    except ImportError:
        print("No machine module (not MicroPython?).")
        return False
    except Exception as e:
        print("PWM err:", _clip(str(e), 22))
        return False


def _play_freq(freq, dur_ms):
    """Route audio to I2S or PWM based on current mode."""
    if freq <= 0 or dur_ms <= 0:
        if dur_ms > 0:
            _sleep_ms(int(dur_ms))
        return True
    if _use_pwm:
        return _play_freq_pwm(freq, dur_ms)
    ok = _play_freq_i2s(freq, dur_ms)
    if not ok:
        print("I2S unavail. Try: use_pwm(True)")
    return ok


# ── public API ──────────────────────────────────

def tone(freq, ms=None):
    """Play a tone at given Hz."""
    if ms is None:
        ms = _dur
    freq = float(freq)
    ms = int(ms)
    if freq < 20 or freq > 20000:
        print("Range: 20-20000 Hz")
        return False
    print("{}Hz {}ms [{}]".format(int(freq), ms, _wave))
    return _play_freq(freq, ms)


def note(name, ms=None):
    """Play note by name (C4, A#5, Db3)."""
    if ms is None:
        ms = _dur
    midi = _parse_note(str(name))
    if midi is None:
        print("Invalid:", _clip(str(name), 22))
        return False
    if midi == 0:
        _sleep_ms(int(ms))
        return True
    freq = _midi_freq(midi)
    nn = NOTE_NAMES[midi % 12]
    no = midi // 12 - 1
    print("{}{} {:.1f}Hz {}ms".format(nn, no, freq, ms))
    return _play_freq(freq, int(ms))


def wave(name=None):
    """Set or show waveform."""
    global _wave
    if name is None:
        print("Wave:", _wave)
        print("Options:", ", ".join(WAVES))
        return _wave
    w = str(name).strip().lower()
    if w not in WAVES:
        print("Unknown:", w)
        print("Options:", ", ".join(WAVES))
        return _wave
    _wave = w
    print("Wave:", _wave)
    return _wave


def octave(n=None):
    """Set or show octave (0-8)."""
    global _oct
    if n is None:
        print("Octave:", _oct)
        return _oct
    n = int(n)
    if n < 0 or n > 8:
        print("Range: 0-8")
        return _oct
    _oct = n
    print("Octave:", _oct)
    return _oct


def volume(n=None):
    """Set or show volume (0-100)."""
    global _vol
    if n is None:
        print("Volume:", _vol)
        return _vol
    n = int(n)
    if n < 0 or n > 100:
        print("Range: 0-100")
        return _vol
    _vol = n
    print("Volume:", _vol)
    return _vol


def bpm(n=None):
    """Set or show BPM (30-300)."""
    global _bpm, _dur
    if n is None:
        print("BPM:", _bpm, "({}ms/beat)".format(_dur))
        return _bpm
    n = int(n)
    if n < 30 or n > 300:
        print("Range: 30-300")
        return _bpm
    _bpm = n
    _dur = 60000 // _bpm
    print("BPM:", _bpm, "({}ms/beat)".format(_dur))
    return _bpm


def duration(ms=None):
    """Set or show note duration (ms)."""
    global _dur
    if ms is None:
        print("Duration:", _dur, "ms")
        return _dur
    ms = int(ms)
    if ms < 10 or ms > 5000:
        print("Range: 10-5000 ms")
        return _dur
    _dur = ms
    print("Duration:", _dur, "ms")
    return _dur


def _stop_requested():
    # Ctrl+C from the device keyboard only acts while stdin is read
    return _poll_key() in ("q", "Q", "esc")


def seq(pattern, ms=None):
    """Play note sequence (space-separated).
    seq('C D E F G A B C5')
    Use - or r for rests. q/Esc stops."""
    if ms is None:
        ms = _dur
    tokens = str(pattern).strip().split()
    if not tokens:
        print("Empty pattern.")
        return False
    print("Seq: {} notes (q/Esc stops)".format(len(tokens)))
    try:
        for tk in tokens:
            if _stop_requested():
                print("Stopped.")
                return False
            midi = _parse_note(tk)
            if midi is None:
                continue
            if midi == 0:
                _sleep_ms(int(ms))
            else:
                _play_freq(_midi_freq(midi), int(ms))
    except KeyboardInterrupt:
        print("Stopped.")
        return False
    print("Done.")
    return True


_RTTTL_NOTES = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11, "h": 11}


def _rtttl_parse(song):
    """'name:d=4,o=5,b=63:8e6,p,4c#.5' -> [(hz, ms), ...]; hz 0 = rest."""
    parts = str(song).split(":")
    if len(parts) != 3:
        return None
    length, octv, tempo = 4, 6, 63  # RTTTL defaults
    for item in parts[1].split(","):
        kv = item.strip().lower().split("=")
        if len(kv) != 2:
            continue
        try:
            val = int(kv[1])
        except ValueError:
            continue
        if kv[0] == "d":
            length = val
        elif kv[0] == "o":
            octv = val
        elif kv[0] == "b":
            tempo = val
    whole = 240000 // max(tempo, 1)  # ms per whole note (4 beats)
    out = []
    for tok in parts[2].split(","):
        tok = tok.strip().lower()
        i = 0
        num = ""
        while i < len(tok) and tok[i].isdigit():
            num += tok[i]
            i += 1
        if i >= len(tok):
            continue
        name = tok[i]
        i += 1
        sharp = 0
        if i < len(tok) and tok[i] == "#":
            sharp = 1
            i += 1
        dotted = False
        if i < len(tok) and tok[i] == ".":
            dotted = True
            i += 1
        octave_digits = ""
        while i < len(tok) and tok[i].isdigit():
            octave_digits += tok[i]
            i += 1
        if i < len(tok) and tok[i] == ".":
            dotted = True
        note_len = int(num) if num else length
        if note_len <= 0:
            continue
        ms = whole // note_len
        if dotted:
            ms += ms // 2
        if name == "p":
            out.append((0, ms))
            continue
        if name not in _RTTTL_NOTES:
            continue
        note_oct = int(octave_digits) if octave_digits else octv
        midi = (note_oct + 1) * 12 + _RTTTL_NOTES[name] + sharp
        out.append((_midi_freq(midi), ms))
    return out


def rtttl(song):
    """Play an RTTTL ringtone ('name:d=4,o=5,b=100:8e6,...'). q/Esc stops."""
    notes = _rtttl_parse(song)
    if not notes:
        print("Bad RTTTL.")
        return False
    print("RTTTL: {} ({} notes, q/Esc stops)".format(_clip(song.split(":")[0], 20), len(notes)))
    try:
        for hz, ms in notes:
            if _stop_requested():
                print("Stopped.")
                return False
            if hz:
                _play_freq(hz, ms * 9 // 10)
                _sleep_ms(ms - ms * 9 // 10)
            else:
                _sleep_ms(ms)
    except KeyboardInterrupt:
        print("Stopped.")
        return False
    return True


def beep(times=1, hz=880, ms=120):
    """Short beeps without console output (timers, alerts)."""
    for i in range(times):
        _play_freq(hz, ms)
        if i < times - 1:
            _sleep_ms(ms)
    return True


def piano():
    """Interactive piano: each key plays at once, no Enter."""
    def _show_piano():
        _screen_header("~~ Synthesizer ~~")
        out = "PWM" if _use_pwm else "I2S"
        print("  C# D#    F# G# A#")
        print("  s  d     g  h  j")
        print("[z][x][c][v][b][n][m]")
        print(" C  D  E  F  G  A  B")
        print("=" * DISPLAY_WIDTH)
        wave_label = "sq" if _use_pwm else _wave[:3]
        print("Oct:{} {} Vol:{}".format(_oct, wave_label, _vol))
        if _use_pwm:
            print("Out:{} +/-oct (waves: I2S only)".format(out))
        else:
            print("Out:{} +/-oct 1-4wave".format(out))
        print("q/Esc=quit r=redraw")

    _show_piano()
    try:
        while True:
            key = _read_key()
            if key in (None, "q", "Q", "esc", "eof"):
                break
            if len(key) == 1:
                key = key.lower()
            if key == "+":
                print()
                octave(min(_oct + 1, 8))
            elif key == "-":
                print()
                octave(max(_oct - 1, 0))
            elif key in ("1", "2", "3", "4"):
                print()
                wave(WAVES[int(key) - 1])
            elif key == "r":
                _show_piano()
            elif key in _KEYS:
                semi = _KEYS[key]
                print(NOTE_NAMES[semi], end=" ")
                _play_freq(_midi_freq((_oct + 1) * 12 + semi), _dur)
    except KeyboardInterrupt:
        pass
    finally:
        _deinit_audio()
        gc.collect()
        _clear_screen()


_DEMOS = {
    "scale": "C D E F G A B C5",
    "twinkle": (
        "C C G G A A G - "
        "F F E E D D C - "
        "G G F F E E D - "
        "G G F F E E D - "
        "C C G G A A G - "
        "F F E E D D C"
    ),
    "ode": (
        "E E F G G F E D "
        "C C D E E D D - "
        "E E F G G F E D "
        "C C D E D C C"
    ),
    # RTTTL: Tarrega's Gran Vals (the Nokia tune) and Beethoven's Fur Elise
    "nokia": "Nokia:d=4,o=5,b=225:8e6,8d6,f#,g#,8c#6,8b,d,e,8b,8a,c#,e,2a",
    "elise": (
        "Elise:d=8,o=5,b=125:32p,e6,d#6,e6,d#6,e6,b,d6,c6,4a.,32p,c,e,a,"
        "4b.,32p,e,g#,b,4c.6,32p,e,e6,d#6,e6,d#6,e6,b,d6,c6,4a.,32p,c,e,a,"
        "4b.,32p,d,c6,b,2a"
    ),
}


def demo(name=None):
    """Play a demo melody."""
    if name is None:
        print("Demos:", ", ".join(_DEMOS.keys()))
        return
    name = str(name).strip().lower()
    if name not in _DEMOS:
        print("Unknown:", name)
        print("Options:", ", ".join(_DEMOS.keys()))
        return
    print("Demo:", name)
    song = _DEMOS[name]
    if ":" in song:
        return rtttl(song)
    return seq(song, 60000 // max(_bpm, 30))


def set_pin(pin):
    """Set I2S audio SD (data) output pin."""
    global _pin
    val = int(pin)
    if val < 0 or val > 28:
        print("Invalid pin (0-28).")
        return _pin
    _pin = val
    _deinit_audio()
    print("I2S pin:", _pin)
    return _pin


def _pins_label():
    return ",".join(str(p) for p in _pwm_pins)


def set_pwm_pin(*pins):
    """Set PWM speaker pin(s): set_pwm_pin(26, 27) or set_pwm_pin(28)."""
    global _pwm_pins
    if not pins:
        print("PWM pins:", _pins_label())
        return _pwm_pins
    vals = tuple(int(p) for p in pins)
    for val in vals:
        if val < 0 or val > 28:
            print("Invalid pin (0-28).")
            return _pwm_pins
    _pwm_pins = vals
    print("PWM pins:", _pins_label())
    return _pwm_pins


def use_pwm(enabled=True):
    """Switch between PWM (built-in speakers) and I2S (external DAC) output.
    use_pwm(True)  -> PWM mode (PicoCalc speakers, GP26/27, default)
    use_pwm(False) -> I2S mode (external DAC; SCK/WS = SD card pins)"""
    global _use_pwm
    _use_pwm = bool(enabled)
    _deinit_audio()
    mode = "PWM pins:{}".format(_pins_label()) if _use_pwm else "I2S pin:{}".format(_pin)
    print("Audio out:", mode)
    return _use_pwm


def close():
    """Release audio hardware."""
    _deinit_audio()
    print("Audio released.")


def ver():
    print("synthesizer:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Synthesizer --")
    print("piano()       Interactive mode")
    print("tone(hz,ms)   Play frequency")
    print("note(n,ms)    Play note (C4,A#5)")
    print("seq(notes)    Play sequence")
    print("demo(name)    Play demo melody")
    print("  scale twinkle ode nokia elise")
    print("rtttl(song)   Play RTTTL ringtone")
    print("beep(n)       Short beeps")
    print("wave(type)    Set waveform")
    print("  sine square saw triangle")
    print("octave(0-8)   Set octave")
    print("volume(0-100) Set volume")
    print("bpm(30-300)   Set tempo")
    print("duration(ms)  Note length")
    print("use_pwm(b)    PWM/I2S (def PWM)")
    print("set_pwm_pin(n..) def 26,27")
    print("set_pin(n)    I2S SD pin (def 28)")
    print("close()       Release audio")
    print("tip: import synthesizer as sy")
    print("tip: I2S needs a DAC and uses")
    print("     GP16/17 = SD card pins")


def h():
    return help()
