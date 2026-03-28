"""Audio synthesizer for PicoCalc."""

import gc
import math
import time
from array import array

from pico_utils import (
    clip as _clip,
    clear_screen as _clear_screen,
    screen_header as _screen_header,
    safe_input as _safe_input,
    sleep_ms as _sleep_ms,
    DISPLAY_WIDTH,
)


MODULE_VERSION = "2026-03-28.3"

# ── audio ───────────────────────────────────────
SAMPLE_RATE = 22050
CHUNK = 512
BUF_SZ = CHUNK * 2
TBL_LEN = 256

# ── state ───────────────────────────────────────
_pin = 28        # I2S SD (data) pin
_pwm_pin = 22    # PWM buzzer pin (PicoCalc built-in speaker)
_use_pwm = False # True = use PWM output instead of I2S
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
    tbl = _tbl
    is_sine = _wave == "sine"
    is_sq = _wave == "square"
    is_saw = _wave == "saw"
    written = 0

    try:
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
                phase = (phase + phase_inc) & 0xFFFFFFFF

            audio.write(buf[:n << 1])
            written += n
    except KeyboardInterrupt:
        pass

    return True


def _play_freq_pwm(freq, dur_ms):
    """Play a square-wave tone via PWM (PicoCalc built-in speaker)."""
    try:
        from machine import PWM, Pin
        pwm = PWM(Pin(_pwm_pin))
        hz = max(20, min(20000, int(freq)))
        pwm.freq(hz)
        duty = max(0, min(65535, int(32768 * _vol // 100)))
        pwm.duty_u16(duty)
        try:
            _sleep_ms(int(dur_ms))
        except KeyboardInterrupt:
            pass
        finally:
            pwm.duty_u16(0)
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


def seq(pattern, ms=None):
    """Play note sequence (space-separated).
    seq('C D E F G A B C5')
    Use - or r for rests."""
    if ms is None:
        ms = _dur
    tokens = str(pattern).strip().split()
    if not tokens:
        print("Empty pattern.")
        return False
    print("Seq: {} notes".format(len(tokens)))
    try:
        for tk in tokens:
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


def piano():
    """Interactive piano keyboard."""
    def _show_piano():
        _screen_header("~~ Synthesizer ~~")
        out = "PWM" if _use_pwm else "I2S"
        print("  C# D#    F# G# A#")
        print("  s  d     g  h  j")
        print("[z][x][c][v][b][n][m]")
        print(" C  D  E  F  G  A  B")
        print("=" * DISPLAY_WIDTH)
        print("Oct:{} {} Vol:{}".format(_oct, _wave[:3], _vol))
        print("Out:{} +/-oct 1-4wave".format(out))
        print("q=quit r=redraw")

    _show_piano()
    try:
        while True:
            try:
                raw = _safe_input("> ").strip().lower()
            except Exception:
                raw = ""

            if raw == "" or raw == "q":
                break

            if raw == "+":
                octave(min(_oct + 1, 8))
                continue
            if raw == "-":
                octave(max(_oct - 1, 0))
                continue
            if raw in ("1", "2", "3", "4"):
                wave(WAVES[int(raw) - 1])
                continue
            if raw == "r":
                _show_piano()
                continue

            played = []
            for ch in raw:
                if ch in _KEYS:
                    semi = _KEYS[ch]
                    midi = (_oct + 1) * 12 + semi
                    _play_freq(_midi_freq(midi), _dur)
                    played.append(NOTE_NAMES[semi])
            if played:
                print(" ".join(played))
    except KeyboardInterrupt:
        pass
    finally:
        _deinit_audio()
        gc.collect()
        _clear_screen()


def demo(name=None):
    """Play a demo melody."""
    songs = {
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
    }
    if name is None:
        print("Demos:", ", ".join(songs.keys()))
        return
    name = str(name).strip().lower()
    if name not in songs:
        print("Unknown:", name)
        print("Options:", ", ".join(songs.keys()))
        return
    print("Demo:", name)
    ms = 60000 // max(_bpm, 30)
    seq(songs[name], ms)


def set_pin(pin):
    """Set I2S audio SD (data) output pin."""
    global _pin
    _pin = int(pin)
    _deinit_audio()
    print("I2S pin:", _pin)
    return _pin


def set_pwm_pin(pin):
    """Set PWM buzzer output pin (PicoCalc built-in speaker)."""
    global _pwm_pin
    _pwm_pin = int(pin)
    print("PWM pin:", _pwm_pin)
    return _pwm_pin


def use_pwm(enabled=True):
    """Switch between PWM (built-in speaker) and I2S (external DAC) output.
    use_pwm(True)  -> PWM mode (PicoCalc built-in speaker, pin 22)
    use_pwm(False) -> I2S mode (external DAC, default)"""
    global _use_pwm
    _use_pwm = bool(enabled)
    _deinit_audio()
    mode = "PWM pin:{}".format(_pwm_pin) if _use_pwm else "I2S pin:{}".format(_pin)
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
    print("  scale, twinkle, ode")
    print("wave(type)    Set waveform")
    print("  sine square saw triangle")
    print("octave(0-8)   Set octave")
    print("volume(0-100) Set volume")
    print("bpm(30-300)   Set tempo")
    print("duration(ms)  Note length")
    print("use_pwm(True) PWM speaker out")
    print("set_pwm_pin(n) PWM pin (def 22)")
    print("set_pin(n)    I2S SD pin (def 28)")
    print("close()       Release audio")
    print("tip: import synthesizer as sy")
    print("tip: sy.use_pwm(True) for")
    print("     PicoCalc built-in speaker")


def h():
    return help()
