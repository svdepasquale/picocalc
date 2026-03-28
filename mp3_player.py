import gc
import os
import time

from pico_utils import clip, paged_print, paged_lines, preview_print, browse_items, format_bytes
from pico_utils import screen_header


MODULE_VERSION = "2026-03-28.2"
DISPLAY_WIDTH = 32
PAGE_LINES = 8
SUPPORTED_EXT = (".mp3", ".wav")
DEFAULT_AUDIO_PIN = 28
DEFAULT_VOLUME = 70
CHUNK_SIZE = 4096

_PLAYLIST = []
_CURRENT_INDEX = 0
_PLAYING = False
_VOLUME = DEFAULT_VOLUME
_AUDIO_PIN = DEFAULT_AUDIO_PIN
_I2S = None
_I2S_CONFIG = None
_SCAN_PATH = "/"


def _track_name(filepath):
    return str(filepath).split("/")[-1]


def _track_size_text(filepath):
    try:
        st = os.stat(filepath)
        size = st[6]
    except Exception:
        return "?"
    return format_bytes(size)


def _resolve_track(index=None):
    if not _PLAYLIST:
        print("Empty playlist.")
        return None, None
    if index is None:
        return _PLAYLIST[_CURRENT_INDEX], _CURRENT_INDEX
    try:
        pos = int(index) - 1
    except Exception:
        print("Invalid index.")
        return None, None
    if pos < 0 or pos >= len(_PLAYLIST):
        print("Out of range.")
        return None, None
    return _PLAYLIST[pos], pos


def _render_track_summary(filepath, pos, total):
    if _PLAYING and pos == _CURRENT_INDEX:
        state = "Playing"
    elif pos == _CURRENT_INDEX:
        state = "Selected"
    else:
        state = "Track"
    print("[{}/{}] {}".format(pos + 1, total, state))
    print("File:")
    preview_print(_track_name(filepath), max_lines=2)
    print("---")
    print("Path:")
    preview_print(filepath, max_lines=2)
    print("Size:", _track_size_text(filepath))
    print("Vol:", _VOLUME)


def _render_track_detail(filepath, pos, total):
    screen_header("Track Detail")
    print("[{}/{}]".format(pos + 1, total))
    print("File:")
    paged_print(_track_name(filepath))
    print("Path:")
    paged_print(filepath)
    print("Size:", _track_size_text(filepath))
    print("Vol:", _VOLUME)
    print("Pin:", _AUDIO_PIN)


def _find_audio_files(path="/", recursive=True, _depth=0):
    files = []
    if _depth > 8:
        return files
    try:
        items = os.listdir(path)
    except Exception:
        return files
    for name in items:
        full = path.rstrip("/") + "/" + name
        lower_name = name.lower()
        try:
            st = os.stat(full)
            if st[0] & 0x4000:
                if recursive:
                    files.extend(_find_audio_files(full, recursive, _depth + 1))
            else:
                for ext in SUPPORTED_EXT:
                    if lower_name.endswith(ext):
                        files.append(full)
                        break
        except Exception:
            pass
    files.sort()
    return files


def scan(path=None):
    global _SCAN_PATH
    if path is not None:
        _SCAN_PATH = str(path)
    print("Scanning:", _SCAN_PATH)
    files = _find_audio_files(_SCAN_PATH)
    if not files:
        print("No audio files found.")
        return []
    print("Found {} file(s):".format(len(files)))
    for i, f in enumerate(files, 1):
        name = _track_name(f)
        print("{}: {}".format(i, clip(name, DISPLAY_WIDTH - 4)))
    return files


def load(path=None):
    global _PLAYLIST, _CURRENT_INDEX
    if path is not None:
        scan_dir = str(path)
    else:
        scan_dir = _SCAN_PATH
    files = _find_audio_files(scan_dir)
    if not files:
        print("No audio files found.")
        return False
    _PLAYLIST = files
    _CURRENT_INDEX = 0
    print("Loaded {} track(s).".format(len(files)))
    return True


def add(filepath):
    filepath = str(filepath).strip()
    if filepath == "":
        print("Empty path.")
        return False
    try:
        os.stat(filepath)
    except Exception:
        print("File not found:", clip(filepath, 22))
        return False
    lower = filepath.lower()
    valid = False
    for ext in SUPPORTED_EXT:
        if lower.endswith(ext):
            valid = True
            break
    if not valid:
        print("Unsupported format.")
        print("Supported:", ", ".join(SUPPORTED_EXT))
        return False
    _PLAYLIST.append(filepath)
    name = _track_name(filepath)
    print("Added:", clip(name, 26))
    print("Tracks:", len(_PLAYLIST))
    return True


def playlist():
    if not _PLAYLIST:
        print("Empty playlist. Use load().")
        return []
    print("Playlist ({} tracks):".format(len(_PLAYLIST)))
    lines = []
    for i, f in enumerate(_PLAYLIST, 1):
        name = _track_name(f)
        marker = ">" if i - 1 == _CURRENT_INDEX else " "
        lines.append("{}{}: {}".format(marker, i, clip(name, DISPLAY_WIDTH - 5)))
    paged_lines(lines)
    return _PLAYLIST


def clear():
    global _PLAYLIST, _CURRENT_INDEX, _PLAYING
    _PLAYING = False
    _PLAYLIST = []
    _CURRENT_INDEX = 0
    gc.collect()
    print("Playlist cleared.")
    return True


def _init_audio(rate=44100, bits=16, channels=1):
    global _I2S, _I2S_CONFIG
    config = (rate, bits, channels)
    if _I2S is not None and _I2S_CONFIG == config:
        return _I2S
    _deinit_audio()
    try:
        from machine import I2S, Pin

        fmt = I2S.MONO if channels <= 1 else I2S.STEREO
        _I2S = I2S(
            0,
            sck=Pin(16),
            ws=Pin(17),
            sd=Pin(_AUDIO_PIN),
            mode=I2S.TX,
            bits=bits,
            format=fmt,
            rate=rate,
            ibuf=4096,
        )
        _I2S_CONFIG = config
        return _I2S
    except ImportError:
        print("No I2S module.")
        return None
    except Exception as e:
        print("Audio init err:", clip(str(e), 20))
        return None


def _deinit_audio():
    global _I2S, _I2S_CONFIG
    if _I2S is not None:
        try:
            _I2S.deinit()
        except Exception:
            pass
        _I2S = None
    _I2S_CONFIG = None


def _scale_volume(buf, n):
    scale = _VOLUME / 100.0
    for i in range(0, n - 1, 2):
        sample = buf[i] | (buf[i + 1] << 8)
        if sample >= 0x8000:
            sample -= 0x10000
        sample = int(sample * scale)
        if sample > 32767:
            sample = 32767
        elif sample < -32768:
            sample = -32768
        if sample < 0:
            sample += 0x10000
        buf[i] = sample & 0xFF
        buf[i + 1] = (sample >> 8) & 0xFF


def _parse_wav_header(f):
    riff = f.read(12)
    if riff is None or len(riff) < 12:
        return None
    if riff[0:4] != b"RIFF" or riff[8:12] != b"WAVE":
        return None
    channels = 0
    sample_rate = 0
    bits = 0
    data_size = 0
    for _ in range(20):
        chunk_hdr = f.read(8)
        if chunk_hdr is None or len(chunk_hdr) < 8:
            break
        cid = chunk_hdr[0:4]
        csz = (
            chunk_hdr[4] | (chunk_hdr[5] << 8)
            | (chunk_hdr[6] << 16) | (chunk_hdr[7] << 24)
        )
        if cid == b"fmt ":
            fmt = f.read(csz)
            if fmt is None or len(fmt) < 16:
                return None
            channels = fmt[2] | (fmt[3] << 8)
            sample_rate = (
                fmt[4] | (fmt[5] << 8) | (fmt[6] << 16) | (fmt[7] << 24)
            )
            bits = fmt[14] | (fmt[15] << 8)
        elif cid == b"data":
            data_size = csz
            break
        else:
            f.read(csz)
            if csz % 2:
                f.read(1)
    if sample_rate == 0:
        return None
    return {
        "channels": channels,
        "rate": sample_rate,
        "bits": bits,
        "data_size": data_size,
    }


def _play_wav(filepath):
    global _PLAYING
    try:
        f = open(filepath, "rb")
    except Exception as e:
        print("Open err:", clip(str(e), 22))
        return False
    try:
        info = _parse_wav_header(f)
        if info is None:
            print("Invalid WAV file.")
            return False
        audio = _init_audio(
            rate=info["rate"],
            bits=info["bits"],
            channels=info["channels"],
        )
        if audio is None:
            print("No audio output.")
            return False
        name = _track_name(filepath)
        print("Playing:", clip(name, 24))
        print("{}Hz {}bit {}ch".format(info["rate"], info["bits"], info["channels"]))
        _PLAYING = True
        buf = bytearray(CHUNK_SIZE)
        view = memoryview(buf)
        remaining = int(info.get("data_size", 0) or 0)
        limit_to_data = remaining > 0
        while _PLAYING:
            if limit_to_data:
                read_size = remaining if remaining < CHUNK_SIZE else CHUNK_SIZE
                if read_size <= 0:
                    break
                n = f.readinto(view[:read_size])
            else:
                n = f.readinto(buf)
            if n is None or n == 0:
                break
            if _VOLUME < 100 and info["bits"] == 16:
                _scale_volume(buf, n)
            try:
                audio.write(buf[:n])
            except Exception:
                break
            if limit_to_data:
                remaining -= n
    except KeyboardInterrupt:
        print("Stopped.")
    except Exception as e:
        print("Play err:", clip(str(e), 22))
    finally:
        _PLAYING = False
        f.close()
    gc.collect()
    return True


def play(index=None):
    global _CURRENT_INDEX
    if not _PLAYLIST:
        print("Empty playlist. Use load().")
        return False
    if index is not None:
        try:
            pos = int(index) - 1
        except Exception:
            print("Invalid index.")
            return False
        if pos < 0 or pos >= len(_PLAYLIST):
            print("Out of range.")
            return False
        _CURRENT_INDEX = pos
    filepath = _PLAYLIST[_CURRENT_INDEX]
    lower = filepath.lower()
    if lower.endswith(".wav"):
        return _play_wav(filepath)
    elif lower.endswith(".mp3"):
        print("MP3 requires decoder hw.")
        print("Convert to WAV for sw playback.")
        name = _track_name(filepath)
        print("File:", clip(name, 26))
        return False
    print("Unsupported format.")
    return False


def stop():
    global _PLAYING
    _PLAYING = False
    print("Stopped.")
    return True


def next_track():
    global _CURRENT_INDEX
    if not _PLAYLIST:
        print("Empty playlist.")
        return False
    _CURRENT_INDEX = (_CURRENT_INDEX + 1) % len(_PLAYLIST)
    name = _track_name(_PLAYLIST[_CURRENT_INDEX])
    print("[{}/{}] {}".format(_CURRENT_INDEX + 1, len(_PLAYLIST), clip(name, 20)))
    return play()


def prev_track():
    global _CURRENT_INDEX
    if not _PLAYLIST:
        print("Empty playlist.")
        return False
    _CURRENT_INDEX = (_CURRENT_INDEX - 1) % len(_PLAYLIST)
    name = _track_name(_PLAYLIST[_CURRENT_INDEX])
    print("[{}/{}] {}".format(_CURRENT_INDEX + 1, len(_PLAYLIST), clip(name, 20)))
    return play()


def now_playing():
    if not _PLAYLIST:
        print("Empty playlist.")
        return None
    name = _track_name(_PLAYLIST[_CURRENT_INDEX])
    state = "Playing" if _PLAYING else "Stopped"
    print("[{}/{}] {}".format(_CURRENT_INDEX + 1, len(_PLAYLIST), state))
    print(clip(name, DISPLAY_WIDTH))
    print("Vol:", _VOLUME)
    return name


def volume(level=None):
    global _VOLUME
    if level is None:
        print("Vol:", _VOLUME)
        return _VOLUME
    try:
        val = int(level)
    except Exception:
        print("Invalid. Range: 0..100")
        return _VOLUME
    if val < 0 or val > 100:
        print("Range: 0..100")
        return _VOLUME
    _VOLUME = val
    print("Vol:", _VOLUME)
    return _VOLUME


def set_pin(pin):
    global _AUDIO_PIN
    try:
        val = int(pin)
    except Exception:
        print("Invalid pin.")
        return False
    _AUDIO_PIN = val
    _deinit_audio()
    print("Audio pin:", _AUDIO_PIN)
    return True


def info(index=None):
    filepath, pos = _resolve_track(index)
    if filepath is None:
        return None
    _render_track_detail(filepath, pos, len(_PLAYLIST))
    return filepath


def browse():
    if not _PLAYLIST:
        print("Empty playlist. Use load().")
        return None
    return browse_items(
        "MP3 Player",
        _PLAYLIST,
        _CURRENT_INDEX + 1,
        _render_track_summary,
        _render_track_detail,
    )


def ver():
    print("mp3_player:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- MP3 Player --")
    print("scan(path)    Find audio files")
    print("load(path)    Load playlist")
    print("add(file)     Add single track")
    print("playlist()    Show playlist")
    print("clear()       Clear playlist")
    print("play(#)/p(#)  Play track")
    print("stop()/s()    Stop playback")
    print("next_track()  Next / n()")
    print("prev_track()  Previous / pr()")
    print("now_playing()  Current / np()")
    print("browse()/b()  Browse tracks")
    print("info(#)       Track details")
    print("volume(0-100) Set volume / v()")
    print("set_pin(#)    Audio output pin")
    print("tip: import mp3_player as mp")


def h():
    return help()


def p(index=None):
    return play(index)


def s():
    return stop()


def n():
    return next_track()


def pr():
    return prev_track()


def np():
    return now_playing()


def b():
    return browse()


def v(level=None):
    return volume(level)


def ls(path=None):
    return scan(path)


def pl():
    return playlist()
