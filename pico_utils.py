import gc
import os
import time

try:
    import ujson as json
except ImportError:
    import json


HTTP_TIMEOUT = 15
USER_AGENT = "PicoCalc"
CLOCK_CONFIG_FILE = "clock_config.json"
MIN_SYNCED_YEAR = 2024
MODULE_VERSION = "2026-10-04.2"

_QUIT_KEYS = ("q", "Q", "esc", "eof")
_ESC_WAIT_MS = 30


# ── screen ──────────────────────────────────────


def _find_terminal():
    # The official PicoCalc firmware's screen terminal (set up by boot.py).
    try:
        import picocalc

        return getattr(picocalc, "terminal", None)
    except ImportError:
        return None


_TERM = _find_terminal()
DISPLAY_WIDTH = 32
PAGE_LINES = 8
if _TERM is not None:
    try:
        _rows, _cols = _TERM.get_screen_size()
        # one column short of the edge, so a full line never wraps by itself
        DISPLAY_WIDTH = _cols - 1
        # room for a header and the pager prompt
        PAGE_LINES = _rows - 6
    except Exception:
        pass

# Unicode characters the CP437 font has, and the codes of their glyphs.
_CP437_SRC = (
    "\xc7\xfc\xe9\xe2\xe4\xe0\xe5\xe7\xea\xeb\xe8\xef"
    "\xee\xec\xc4\xc5\xc9\xe6\xc6\xf4\xf6\xf2\xfb\xf9"
    "\xff\xd6\xdc\xa2\xa3\xa5₧ƒ\xe1\xed\xf3\xfa"
    "\xf1\xd1\xaa\xba\xbf⌐\xac\xbd\xbc\xa1\xab\xbb"
    "\xdf\xb5\xb1\xf7\xb0\xb7\xb2"
)
_CP437_DST = (
    "\x80\x81\x82\x83\x84\x85\x86\x87\x88\x89\x8a\x8b"
    "\x8c\x8d\x8e\x8f\x90\x91\x92\x93\x94\x95\x96\x97"
    "\x98\x99\x9a\x9b\x9c\x9d\x9e\x9f\xa0\xa1\xa2\xa3"
    "\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xab\xac\xad\xae\xaf"
    "\xe1\xe6\xf1\xf6\xf8\xfa\xfd"
)
# What CP437 lacks, folded to ASCII.
_FOLDS = {
    "\xc0": "A",
    "\xc1": "A",
    "\xc8": "E",
    "\xcc": "I",
    "\xcd": "I",
    "\xd2": "O",
    "\xd3": "O",
    "\xd9": "U",
    "\xda": "U",
    "‘": "'",
    "’": "'",
    "‚": ",",
    "“": '"',
    "”": '"',
    "„": '"',
    "–": "-",
    "—": "-",
    "−": "-",
    "…": "...",
    "•": "*",
    "\xa0": " ",
    "€": "EUR",
}


def to_cp437(text):
    # Map text to the codes the PicoCalc font draws (CP437); the rest -> ASCII.
    if len(text) == len(text.encode()):
        return text
    out = []
    for ch in text:
        if ord(ch) < 128:
            out.append(ch)
            continue
        idx = _CP437_SRC.find(ch)
        if idx >= 0:
            out.append(_CP437_DST[idx])
        else:
            out.append(_FOLDS.get(ch, "?"))
    return "".join(out)


try:
    import io

    _IOBase = io.IOBase
except (ImportError, AttributeError):
    _IOBase = object


class _ScreenTerm(_IOBase):
    # The firmware's vt.write() returns characters, not bytes: on non-ASCII
    # output MicroPython re-sends the tail of the UTF-8 sequence, decode()
    # raises and dupterm detaches screen and keyboard until reset. This
    # stream reports bytes, never raises on bad UTF-8, and maps to CP437.
    _cp437_screen = True

    def __init__(self, term):
        self._term = term

    def write(self, buf):
        if isinstance(buf, str):
            text = buf
        else:
            try:
                text = str(buf, "utf-8")
            except Exception:
                text = "".join(chr(b) if b < 128 else "?" for b in buf)
        self._term.wr(to_cp437(text))
        return len(buf)

    def readinto(self, buf):
        return self._term.readinto(buf)


def _install_screen():
    if _TERM is None or not hasattr(os, "dupterm") or not hasattr(_TERM, "wr"):
        return False
    prev = os.dupterm(_ScreenTerm(_TERM))
    if prev is _TERM:
        return True
    os.dupterm(prev)  # already wrapped, or not the PicoCalc terminal
    return getattr(prev, "_cp437_screen", False)


SCREEN_FIX = _install_screen()


def clip(text, limit):
    value = str(text)
    if len(value) <= limit:
        return value
    return value[:limit]


def wrap_text(text, width=DISPLAY_WIDTH):
    source = str(text).replace("\r\n", "\n").replace("\r", "\n")
    wrapped = []
    for paragraph in source.split("\n"):
        paragraph = paragraph.strip()
        if paragraph == "":
            wrapped.append("")
            continue

        parts = []
        parts_len = 0
        for word in paragraph.split(" "):
            if word == "":
                continue
            if not parts:
                if len(word) <= width:
                    parts.append(word)
                    parts_len = len(word)
                else:
                    start = 0
                    while start < len(word):
                        wrapped.append(word[start : start + width])
                        start += width
            elif parts_len + 1 + len(word) <= width:
                parts.append(word)
                parts_len += 1 + len(word)
            else:
                wrapped.append(" ".join(parts))
                parts = []
                parts_len = 0
                if len(word) <= width:
                    parts.append(word)
                    parts_len = len(word)
                else:
                    start = 0
                    while start < len(word):
                        wrapped.append(word[start : start + width])
                        start += width
        if parts:
            wrapped.append(" ".join(parts))
    return wrapped


def clear_screen():
    print("\x1b[2J\x1b[H", end="")


def screen_header(title):
    clear_screen()
    bar = "=" * DISPLAY_WIDTH
    print(bar)
    print(title.center(DISPLAY_WIDTH))
    print(bar)


def paged_print(text, page_lines=PAGE_LINES):
    return paged_lines(wrap_text(text), page_lines)


def preview_lines(text, width=DISPLAY_WIDTH, max_lines=PAGE_LINES):
    lines = wrap_text(text, width)
    if max_lines is None or max_lines < 1 or len(lines) <= max_lines:
        return lines

    shown = lines[:max_lines]
    if shown:
        tail = shown[-1]
        max_tail = width - 3
        if max_tail < 0:
            max_tail = 0
        if len(tail) > max_tail:
            tail = tail[:max_tail]
        shown[-1] = tail + "..."
    return shown


def preview_print(text, width=DISPLAY_WIDTH, max_lines=PAGE_LINES):
    lines = preview_lines(text, width=width, max_lines=max_lines)
    if not lines:
        print("(empty)")
        return 0
    for line in lines:
        print(line)
    return len(lines)


def paged_lines(lines, page_lines=PAGE_LINES):
    if not lines:
        print("(empty)")
        return 0

    count = 0
    total = len(lines)
    for index, line in enumerate(lines):
        print(line)
        count += 1
        if count >= page_lines and index < total - 1:
            if wait_key("-- more: any key, q stop --") in _QUIT_KEYS:
                print("(stopped)")
                break
            count = 0
    return total


def format_bytes(size):
    try:
        value = int(size)
    except Exception:
        return "?"

    if value < 1024:
        return "{}B".format(value)
    if value < 1024 * 1024:
        return "{}KB".format(value // 1024)
    return "{}MB".format(value // (1024 * 1024))


def ticks_ms():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.time() * 1000)


def ticks_diff(current, start):
    if hasattr(time, "ticks_diff"):
        return time.ticks_diff(current, start)
    return int(current) - int(start)


def ticks_add(ticks, delta):
    if hasattr(time, "ticks_add"):
        return time.ticks_add(ticks, delta)
    return int(ticks) + int(delta)


def sleep_ms(ms):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(ms)
        return
    time.sleep(ms / 1000.0)


# ── keys ────────────────────────────────────────
# Single keypresses, no Enter. The PicoCalc terminal's readinto() never
# blocks and has no ioctl (select() can't see it), so it is polled
# directly; USB serial goes through select on stdin.

_KEY_BUF = bytearray(1)
_STDIN_POLL = None
_CSI_KEYS = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
_TILDE_KEYS = {"1": "home", "2": "insert", "3": "del", "4": "end", "5": "pgup", "6": "pgdn", "7": "home", "8": "end"}


def _poll_byte():
    # One pending byte from the PicoCalc keyboard or USB serial, or None.
    global _STDIN_POLL
    if _TERM is not None and _TERM.readinto(_KEY_BUF):
        return _KEY_BUF[0]
    if _STDIN_POLL is None:
        try:
            import select
            import sys

            _STDIN_POLL = select.poll()
            _STDIN_POLL.register(sys.stdin, select.POLLIN)
        except Exception:
            _STDIN_POLL = False
    if _STDIN_POLL and _STDIN_POLL.poll(0):
        import sys

        try:
            ch = sys.stdin.read(1)
        except Exception:
            return None
        if ch:
            return ord(ch[0])
    return None


def _key_byte(timeout_ms=None):
    start = ticks_ms()
    while True:
        byte = _poll_byte()
        if byte is not None:
            return byte
        if timeout_ms is not None and ticks_diff(ticks_ms(), start) >= timeout_ms:
            return None
        sleep_ms(10)


def _decode_key(first, next_byte):
    # Raw bytes -> key name, for the sequences the PicoCalc driver and VT100
    # terminals send. Ctrl+C arrives as data from the device keyboard.
    if first == 0x1B:
        second = next_byte(_ESC_WAIT_MS)
        if second is None or second == 0x1B:
            return "esc"  # the PicoCalc Esc key sends ESC ESC
        if second not in (0x5B, 0x4F):  # not "[" or "O": Alt+key
            return chr(second)
        params = ""
        while True:
            byte = next_byte(_ESC_WAIT_MS)
            if byte is None:
                return "esc"
            if 0x40 <= byte <= 0x7E:
                break
            params += chr(byte)
        final = chr(byte)
        if final == "~":
            return _TILDE_KEYS.get(params.split(";")[0], "")
        return _CSI_KEYS.get(final, "")
    if first == 0x03:
        raise KeyboardInterrupt
    if first in (0x0D, 0x0A):
        return "enter"
    if first in (0x7F, 0x08):
        return "backspace"
    if first == 0x09:
        return "tab"
    if first == 0x04:
        return "eof"
    return chr(first)


def read_key(timeout_ms=None):
    # Next key: a printable character or a name ("enter", "esc", "up"...).
    # None if timeout_ms passes without a key.
    first = _key_byte(timeout_ms)
    if first is None:
        return None
    return _decode_key(first, _key_byte)


def poll_key():
    # A key if one is pending, else None. Long loops call this so q/Esc can
    # stop them: Ctrl+C from the device keyboard only acts while stdin is read.
    return read_key(0)


def wait_key(prompt="-- any key --"):
    # One-line prompt; erases itself after the key.
    print(prompt, end="")
    try:
        key = read_key()
    except KeyboardInterrupt:
        key = "esc"
    print("\r\x1b[K", end="")
    return key


def read_line(prompt="", mask=None, max_len=120):
    # Line entry with Backspace; Enter returns the text, Esc returns None.
    # mask (e.g. "*") is echoed instead of the characters.
    print(prompt, end="")
    chars = []
    while True:
        key = read_key()
        if key == "enter":
            print()
            return "".join(chars)
        if key in ("esc", "eof"):
            print()
            return None
        if key == "backspace":
            if chars:
                chars.pop()
                print("\b \b", end="")
            continue
        if key and len(key) == 1 and " " <= key <= "~" and len(chars) < max_len:
            chars.append(key)
            print(mask or key, end="")


def _read_digits(first):
    print("Go to #:", first, end="")
    digits = first
    while True:
        key = read_key()
        if key == "enter":
            print()
            break
        if key in _QUIT_KEYS:
            print()
            return None
        if key == "backspace":
            if digits:
                digits = digits[:-1]
                print("\b \b", end="")
            continue
        if key and len(key) == 1 and key.isdigit() and len(digits) < 4:
            digits += key
            print(key, end="")
    return int(digits) if digits else None


def browse_items(
    title,
    items,
    start_index=1,
    render_summary=None,
    render_detail=None,
    on_key=None,
):
    # One item per screen. on_key(key, item, pos) handles extra keys.
    if not items:
        print("No items.")
        return None

    try:
        pos = int(start_index) - 1
    except Exception:
        print("Invalid index.")
        return None

    if pos < 0 or pos >= len(items):
        print("Out of range.")
        return None

    while True:
        total = len(items)
        if total == 0:
            clear_screen()
            return None
        if pos >= total:
            pos = total - 1
        item = items[pos]
        screen_header(title)

        if render_summary is not None:
            render_summary(item, pos, total)

        print("=" * DISPLAY_WIDTH)
        print("n/p/arrows move  d/Enter detail")
        print("q/Esc quit  0-9 jump")

        try:
            key = read_key()
        except KeyboardInterrupt:
            key = "esc"
        if key in _QUIT_KEYS or key == "down":
            clear_screen()
            return item
        if key in ("n", "right", " "):
            pos = (pos + 1) % total
        elif key in ("p", "left"):
            pos = (pos - 1) % total
        elif key in ("d", "up", "enter"):
            if render_detail is not None:
                clear_screen()
                render_detail(item, pos, total)
                wait_key()
        elif key and len(key) == 1 and key.isdigit():
            num = _read_digits(key)
            if num is not None:
                if 1 <= num <= total:
                    pos = num - 1
                else:
                    wait_key("Out of range. Any key")
        elif key and on_key is not None:
            on_key(key, item, pos)


def load_json(filepath):
    for path in (filepath, filepath + ".tmp", filepath + ".bak"):
        try:
            with open(path, "r") as f:
                data = json.load(f)
                if isinstance(data, (dict, list)):
                    return data
        except Exception:
            pass
    return None


def save_json(filepath, data):
    tmp = filepath + ".tmp"
    bak = filepath + ".bak"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
            try:
                f.flush()
            except Exception:
                pass
    except Exception as e:
        print("Save err:", e)
        return False
    try:
        os.sync()
    except Exception:
        pass
    try:
        os.remove(bak)
    except Exception:
        pass
    try:
        os.rename(filepath, bak)
    except Exception:
        pass
    try:
        os.rename(tmp, filepath)
    except Exception as e:
        print("Rename err:", e)
        try:
            os.rename(bak, filepath)
        except Exception:
            pass
        return False
    try:
        os.remove(bak)
    except Exception:
        pass
    gc.collect()
    return True


def safe_input(prompt):
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""


def http_module():
    try:
        import urequests as requests
    except ImportError:
        print("Missing urequests.")
        print("Install: import mip; mip.install('urequests')")
        return None
    return requests


def http_request(requests, method, url, timeout=HTTP_TIMEOUT, **kwargs):
    # MicroPython's socket module has no setdefaulttimeout(), so the timeout
    # must travel with each request; very old urequests builds lack the kwarg.
    headers = dict(kwargs.get("headers") or {})
    if "User-Agent" not in headers:
        headers["User-Agent"] = USER_AGENT
    kwargs["headers"] = headers
    try:
        return requests.request(method, url, timeout=timeout, **kwargs)
    except TypeError as error:
        if "keyword" not in str(error):
            raise
        return requests.request(method, url, **kwargs)


# ── time ────────────────────────────────────────


def _weekday(year, month, day):
    # Monday=0 (Sakamoto's method)
    offsets = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
    if month < 3:
        year -= 1
    return (year + year // 4 - year // 100 + year // 400 + offsets[month - 1] + day + 6) % 7


def eu_dst_active(utc):
    # EU summer time, from a UTC time tuple: last Sunday of March 01:00 UTC
    # until last Sunday of October 01:00 UTC.
    month = utc[1]
    if month < 3 or month > 10:
        return False
    if 3 < month < 10:
        return True
    last_sunday = 31 - (_weekday(utc[0], month, 31) + 1) % 7
    if month == 3:
        return (utc[2], utc[3]) >= (last_sunday, 1)
    return (utc[2], utc[3]) < (last_sunday, 1)


def utc_offset_hours():
    data = load_json(CLOCK_CONFIG_FILE)
    try:
        offset = int(data.get("utc_offset", 0))
        if data.get("dst") == "eu" and eu_dst_active(time.gmtime()):
            offset += 1
        return offset
    except Exception:
        return 0


def clock_synced():
    # rp2 boots with its clock at 2021-01-01 until NTP (or a host tool) sets it.
    return time.gmtime()[0] >= MIN_SYNCED_YEAR


def local_time(offset=None):
    if offset is None:
        offset = utc_offset_hours()
    return time.gmtime(time.time() + offset * 3600)


# ── device ──────────────────────────────────────


def battery():
    # (percent, charging) from the PicoCalc keyboard MCU, or None elsewhere.
    # Same sequence as ClockworkPi's C example: write reg 0x0B, wait 16 ms.
    try:
        import picocalc

        kbd = picocalc.keyboard
        kbd.i2c.writeto(kbd.address, b"\x0b")
        sleep_ms(16)
        data = kbd.i2c.readfrom(kbd.address, 2)
        return data[1] & 0x7F, bool(data[1] & 0x80)
    except Exception:
        return None


def wifi_connected():
    try:
        import network

        return network.WLAN(network.STA_IF).isconnected()
    except Exception:
        return False


def check_wifi():
    try:
        import network
    except ImportError:
        print("No network module.")
        return False
    try:
        w = network.WLAN(network.STA_IF)
        if not w.isconnected():
            print("No WiFi. Connect first.")
            return False
    except Exception:
        print("WiFi check error.")
        return False
    return True


def ver():
    print("pico_utils:", MODULE_VERSION)
    return MODULE_VERSION
