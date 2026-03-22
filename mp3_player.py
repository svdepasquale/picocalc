import gc
import os
import time

from pico_utils import clip, paged_print, normalize_nav_cmd, safe_input


MODULE_VERSION = "2026-03-22.1"
DISPLAY_WIDTH = 32
PAGE_LINES = 8
SUPPORTED_EXT = (".mp3", ".wav")
DEFAULT_AUDIO_PIN = 28
DEFAULT_VOLUME = 70
CHUNK_SIZE = 1024

_PLAYLIST = []
_CURRENT_INDEX = 0
_PLAYING = False
_VOLUME = DEFAULT_VOLUME
_AUDIO_PIN = DEFAULT_AUDIO_PIN
_I2S = None
_SCAN_PATH = "/"


def _find_audio_files(path="/", recursive=True):
    files = []
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
                    files.extend(_find_audio_files(full, recursive))
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
        name = f.split("/")[-1]
        print("{}: {}".format(i, clip(name, DISPLAY_WIDTH - 4)))
    return files


def load(path=None):
    global _PLAYLIST, _CURRENT_INDEX
    if path is not None:
        _SCAN_PATH_LOCAL = str(path)
    else:
        _SCAN_PATH_LOCAL = _SCAN_PATH
    files = _find_audio_files(_SCAN_PATH_LOCAL)
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
    name = filepath.split("/")[-1]
    print("Added:", clip(name, 26))
    print("Tracks:", len(_PLAYLIST))
    return True


def playlist():
    if not _PLAYLIST:
        print("Empty playlist. Use load().")
        return []
    print("Playlist ({} tracks):".format(len(_PLAYLIST)))
    for i, f in enumerate(_PLAYLIST, 1):
        name = f.split("/")[-1]
        marker = ">" if i - 1 == _CURRENT_INDEX else " "
        print("{}{}: {}".format(marker, i, clip(name, DISPLAY_WIDTH - 5)))
    return _PLAYLIST


def clear():
    global _PLAYLIST, _CURRENT_INDEX, _PLAYING
    _PLAYING = False
    _PLAYLIST = []
    _CURRENT_INDEX = 0
    gc.collect()
    print("Playlist cleared.")
    return True


def _init_audio():
    global _I2S
    if _I2S is not None:
        return _I2S
    try:
        from machine import I2S, Pin

        _I2S = I2S(
            0,
            sck=Pin(16),
            ws=Pin(17),
            sd=Pin(_AUDIO_PIN),
            mode=I2S.TX,
            bits=16,
            format=I2S.MONO,
            rate=44100,
            ibuf=4096,
        )
        return _I2S
    except ImportError:
        pass
    try:
        from machine import Pin, PWM

        pwm = PWM(Pin(_AUDIO_PIN))
        pwm.freq(44100)
        pwm.duty_u16(0)
        _I2S = pwm
        return _I2S
    except Exception as e:
        print("Audio init err:", clip(str(e), 20))
        return None


def _deinit_audio():
    global _I2S
    if _I2S is not None:
        try:
            _I2S.deinit()
        except Exception:
            pass
        _I2S = None


def _parse_wav_header(f):
    header = f.read(44)
    if header is None or len(header) < 44:
        return None
    if header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
        return None
    channels = header[22] | (header[23] << 8)
    sample_rate = (
        header[24] | (header[25] << 8) | (header[26] << 16) | (header[27] << 24)
    )
    bits = header[34] | (header[35] << 8)
    data_size = (
        header[40] | (header[41] << 8) | (header[42] << 16) | (header[43] << 24)
    )
    return {
        "channels": channels,
        "rate": sample_rate,
        "bits": bits,
        "data_size": data_size,
    }


def _play_wav(filepath):
    global _PLAYING
    audio = _init_audio()
    if audio is None:
        print("No audio output.")
        return False
    try:
        f = open(filepath, "rb")
    except Exception as e:
        print("Open err:", clip(str(e), 22))
        return False
    try:
        info = _parse_wav_header(f)
        if info is None:
            print("Invalid WAV file.")
            f.close()
            return False
        name = filepath.split("/")[-1]
        print("Playing:", clip(name, 24))
        print("{}Hz {}bit {}ch".format(info["rate"], info["bits"], info["channels"]))
        _PLAYING = True
        buf = bytearray(CHUNK_SIZE)
        while _PLAYING:
            n = f.readinto(buf)
            if n is None or n == 0:
                break
            try:
                audio.write(buf[:n])
            except Exception:
                break
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
        name = filepath.split("/")[-1]
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
    name = _PLAYLIST[_CURRENT_INDEX].split("/")[-1]
    print("[{}/{}] {}".format(_CURRENT_INDEX + 1, len(_PLAYLIST), clip(name, 20)))
    return play()


def prev_track():
    global _CURRENT_INDEX
    if not _PLAYLIST:
        print("Empty playlist.")
        return False
    _CURRENT_INDEX = (_CURRENT_INDEX - 1) % len(_PLAYLIST)
    name = _PLAYLIST[_CURRENT_INDEX].split("/")[-1]
    print("[{}/{}] {}".format(_CURRENT_INDEX + 1, len(_PLAYLIST), clip(name, 20)))
    return play()


def now_playing():
    if not _PLAYLIST:
        print("Empty playlist.")
        return None
    name = _PLAYLIST[_CURRENT_INDEX].split("/")[-1]
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
    if not _PLAYLIST:
        print("Empty playlist.")
        return None
    if index is None:
        pos = _CURRENT_INDEX
    else:
        try:
            pos = int(index) - 1
        except Exception:
            print("Invalid index.")
            return None
    if pos < 0 or pos >= len(_PLAYLIST):
        print("Out of range.")
        return None
    filepath = _PLAYLIST[pos]
    name = filepath.split("/")[-1]
    print("[{}/{}]".format(pos + 1, len(_PLAYLIST)))
    print("File:", clip(name, 26))
    print("Path:", clip(filepath, 26))
    try:
        st = os.stat(filepath)
        size = st[6]
        if size >= 1024:
            print("Size: {}KB".format(size // 1024))
        else:
            print("Size: {}B".format(size))
    except Exception:
        print("Size: ?")
    return filepath


def browse():
    if not _PLAYLIST:
        print("Empty playlist. Use load().")
        return None
    total = len(_PLAYLIST)
    pos = _CURRENT_INDEX
    while True:
        name = _PLAYLIST[pos].split("/")[-1]
        state = " *" if _PLAYING and pos == _CURRENT_INDEX else ""
        print("---")
        print("[{}/{}]{}".format(pos + 1, total, state))
        print(clip(name, DISPLAY_WIDTH))
        try:
            st = os.stat(_PLAYLIST[pos])
            size = st[6]
            if size >= 1024:
                print("{}KB".format(size // 1024))
            else:
                print("{}B".format(size))
        except Exception:
            pass
        try:
            cmd = normalize_nav_cmd(safe_input("n/p/P/q/#/arrows: "))
        except Exception:
            cmd = "q"
        if cmd == "q":
            return _PLAYLIST[pos]
        if cmd == "n":
            pos = (pos + 1) % total
            continue
        if cmd == "p":
            pos = (pos - 1) % total
            continue
        if cmd == "d":
            play(pos + 1)
            continue
        try:
            jump = int(cmd) - 1
            if 0 <= jump < total:
                pos = jump
            else:
                print("Out of range.")
        except Exception:
            print("Use n/p/d/q/# or arrows")


def ver():
    print("mp3_player:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("cmd: scan load add playlist clear")
    print("cmd: play stop next_track prev_track")
    print("cmd: now_playing browse info")
    print("cmd: volume set_pin")
    print("cmd: ver help h")
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
