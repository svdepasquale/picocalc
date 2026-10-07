import gc
import os
import sys
import time

try:
    import ujson as json
except ImportError:
    import json


HTTP_TIMEOUT = 15
USER_AGENT = "PicoCalc"
CLOCK_CONFIG_FILE = "clock_config.json"
MIN_SYNCED_YEAR = 2024
MODULE_VERSION = "2026-10-07.2"

_QUIT_KEYS = ("q", "Q", "esc", "eof")
_ESC_WAIT_MS = 30


# ── screen ──────────────────────────────────────


def _terminal():
    # The PicoCalc firmware's screen terminal, looked up at each use: every
    # run of boot.py (soft reset) builds a new one.
    mod = sys.modules.get("picocalc")
    if mod is None:
        return None
    return getattr(mod, "terminal", None)


# gfx's screen console, which draws what apps print while the launcher runs:
# a 12-pixel title row, then rows of 10 pixels, 53 columns.
CON_COLS = 53
CON_TITLE_H = 12
CON_ROW_H = 10
CON_ROW_TOP = CON_TITLE_H + 2  # y of row 2
CON_ROWS = 1 + (320 - CON_ROW_TOP) // CON_ROW_H  # 31

DISPLAY_WIDTH = 32
PAGE_LINES = 8
SCREEN_ROWS = 14
_TERM_ROWS = 14
try:
    _rows, _cols = _terminal().get_screen_size()
    _TERM_ROWS = _rows  # the firmware terminal's 40, for its scroll margins
    # Apps lay out for the console's rows, the fewer: the same screens fit the
    # terminal when they run from the REPL.
    SCREEN_ROWS = min(_rows, CON_ROWS)
    # one column short of the edge: the terminal wraps on the last column
    DISPLAY_WIDTH = _cols - 1
    # room for a header and the pager prompt
    PAGE_LINES = SCREEN_ROWS - 6
except Exception:
    pass

_CONSOLE = [None]  # gfx's console while gfx owns the screen (gfx.begin/end)

# Unicode characters the CP437 font has, and the codes of their glyphs
# (0x10-0x1F glyphs too: the terminal draws them, except 0x1B = ESC).
_CP437_SRC = (
    "►◄↕‼\xb6\xa7▬↨↑↓→∟"
    "↔▲▼\xc7\xfc\xe9\xe2\xe4\xe0\xe5\xe7\xea"
    "\xeb\xe8\xef\xee\xec\xc4\xc5\xc9\xe6\xc6\xf4\xf6"
    "\xf2\xfb\xf9\xff\xd6\xdc\xa2\xa3\xa5₧ƒ\xe1"
    "\xed\xf3\xfa\xf1\xd1\xaa\xba\xbf⌐\xac\xbd\xbc"
    "\xa1\xab\xbb░▒▓│┤╡╢╖╕"
    "╣║╗╝╜╛┐└┴┬├─"
    "┼╞╟╚╔╩╦╠═╬╧╨"
    "╤╥╙╘╒╓╫╪┘┌█▄"
    "▌▐▀α\xdfΓπΣσ\xb5τΦ"
    "ΘΩδ∞φε∩≡\xb1≥≤⌠"
    "⌡\xf7≈\xb0∙\xb7√ⁿ\xb2■"
)
_CP437_DST = (
    "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1c"
    "\x1d\x1e\x1f\x80\x81\x82\x83\x84\x85\x86\x87\x88"
    "\x89\x8a\x8b\x8c\x8d\x8e\x8f\x90\x91\x92\x93\x94"
    "\x95\x96\x97\x98\x99\x9a\x9b\x9c\x9d\x9e\x9f\xa0"
    "\xa1\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xab\xac"
    "\xad\xae\xaf\xb0\xb1\xb2\xb3\xb4\xb5\xb6\xb7\xb8"
    "\xb9\xba\xbb\xbc\xbd\xbe\xbf\xc0\xc1\xc2\xc3\xc4"
    "\xc5\xc6\xc7\xc8\xc9\xca\xcb\xcc\xcd\xce\xcf\xd0"
    "\xd1\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xdb\xdc"
    "\xdd\xde\xdf\xe0\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8"
    "\xe9\xea\xeb\xec\xed\xee\xef\xf0\xf1\xf2\xf3\xf4"
    "\xf5\xf6\xf7\xf8\xf9\xfa\xfb\xfc\xfd\xfe"
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
    "←": "<",  # CP437 has it at 0x1B, which is ESC
    "\xa0": " ",
    "€": "EUR",
}


def to_cp437(text):
    # Map text to the codes the PicoCalc font draws (CP437); the rest -> ASCII.
    # One replace() per distinct character: a loop over every character cost
    # ~75 us each on the device, nearly what the terminal takes to draw it
    # (~0.1 ms, measured 2026-10-06).
    if len(text) == len(text.encode()):
        return text
    table = {}
    for ch in set(text):
        if ord(ch) >= 128:
            idx = _CP437_SRC.find(ch)
            table[ch] = _CP437_DST[idx] if idx >= 0 else _FOLDS.get(ch, "?")
    for code in table.values():
        if code in table:  # an output is also an input: map one by one
            return "".join(table.get(ch, ch) for ch in text)
    for ch, code in table.items():
        text = text.replace(ch, code)
    return text


try:
    import io

    _IOBase = io.IOBase
except (ImportError, AttributeError):
    _IOBase = object


_INTERRUPTED = [False]  # a Ctrl+C caught in the screen writer, raised at the next key read


def _screen_text(buf):
    if isinstance(buf, str):
        return buf
    try:
        return str(buf, "utf-8")
    except Exception:
        return "".join(chr(b) if b < 128 else "?" for b in buf)


class _ScreenTerm(_IOBase):
    # The firmware's vt.write() returns characters, not bytes: on non-ASCII
    # output MicroPython re-sends the tail of the UTF-8 sequence, decode()
    # raises and dupterm detaches screen and keyboard until reset. This
    # stream reports bytes, never raises (a Ctrl+C from USB included, which
    # lands wherever Python is: it is kept for the next key read), and maps
    # to CP437. While gfx owns the screen its console draws instead.
    _cp437_screen = True

    def __init__(self, term):
        self._term = term

    def write(self, buf):
        con = _CONSOLE[0]
        if con is not None and con.draw(buf):
            return len(buf)
        try:
            self._term.wr(to_cp437(_screen_text(buf)))
        except KeyboardInterrupt:
            _INTERRUPTED[0] = True
        except Exception:
            pass
        return len(buf)

    def readinto(self, buf):
        # The keyboard MCU answers EIO while the PicoCalc is off (Pico on USB
        # power only), and a firmware bug can raise on Ctrl+U: raising here
        # would make dupterm detach the screen. A kept Ctrl+C goes back as
        # the byte, which dupterm turns into the KeyboardInterrupt.
        try:
            if _INTERRUPTED[0]:
                _INTERRUPTED[0] = False
                buf[0] = 3
                return 1
            con = _CONSOLE[0]
            if con is not None and con.beam is None:
                _show_cursor()  # input() reads: the console's cursor
            return self._term.readinto(buf)
        except KeyboardInterrupt:
            buf[0] = 3
            return 1
        except Exception:
            return None

    def ioctl(self, req, arg):
        # No poll support: without this method each select on stdin raised
        # (and allocated) inside dupterm, which then ignored it.
        return 0


def _set_margins(term, keep_cursor=False):
    # Firmware bug: scroll() moves the cursor one row past the bottom margin
    # while it redraws the screen, and the cursor-blink interrupt can draw it
    # there, past the end of the framebuffer: it zeroed bytes of the SD
    # driver's buffer (the next heap block) and SD reads failed. A scroll
    # region one row short keeps that row on screen. Never move the cursor
    # to the last row: a line feed from there is the same overflow.
    seq = "\x1b[1;{}r".format(_TERM_ROWS - 1)  # DECSTBM homes the cursor
    try:
        term.wr("\x1b7" + seq + "\x1b8" if keep_cursor else seq)
    except Exception:
        pass


def _install_screen():
    term = _terminal()
    if term is None or not hasattr(os, "dupterm") or not hasattr(term, "wr"):
        return False
    prev = os.dupterm(_ScreenTerm(term))
    if prev is term or prev is None:
        # None: dupterm already detached the driver's terminal (a raw-paste
        # handshake from mpremote/Thonny writes bytes it can't decode)
        _set_margins(term, True)
        return True
    if getattr(prev, "_cp437_screen", False):
        if prev._term is term:
            os.dupterm(prev)  # already wrapped
        _set_margins(term, True)
        return True  # else: the old wrapper held a terminal boot.py replaced
    os.dupterm(prev)  # not the PicoCalc terminal: leave it alone
    return False


def _show_cursor():
    # gfx's console draws a cursor only while something waits for text.
    con = _CONSOLE[0]
    if con is not None:
        try:
            con.waiting()
        except Exception:
            pass


def block_rows(rows, row, col, fg):
    """Rows of text with █ blocks at screen row `row`, column `col`: the clock's
    big digits. gfx's console draws the blocks solid (its rows have gaps
    between glyphs); the terminal prints them."""
    con = _CONSOLE[0]
    if con is not None:
        try:
            con.blocks(rows, row, col, fg)
            return
        except Exception:
            pass
    for i, line in enumerate(rows):
        print("\x1b[{};1H\x1b[K{}{}".format(row + i, " " * (col - 1), paint(line, fg)), end="")


def console_suspend():
    """Lend the screen to the firmware terminal, for a program that writes to
    it directly (the pye editor). Returns what console_resume() takes."""
    con = _CONSOLE[0]
    if con is None:
        return None
    import gfx

    return gfx.suspend()


def console_resume(token):
    if token:
        import gfx

        gfx.resume()


def ensure_screen():
    """Put the CP437 writer back if the screen terminal was detached or
    rebuilt since import (host tools, boot.py runs)."""
    global SCREEN_FIX
    SCREEN_FIX = _install_screen()
    return SCREEN_FIX


SCREEN_FIX = _install_screen()


def _fix_screenshot():
    # Ctrl+U saves the screen to /sd/screen_<ms>.bmp in the firmware, but its
    # code reads display.buffer (never set) and memoryview.cast (MicroPython
    # has none), so the key raised instead. Give it both.
    try:
        import array
        import picocalc
        import picocalcdisplay

        display = picocalc.display
        if not hasattr(display, "buffer"):
            display.buffer = memoryview(display)
        type(display).getLUT = lambda self: array.array("H", bytes(picocalcdisplay.getLUTview()))
    except Exception:
        pass


_fix_screenshot()


# ── look ────────────────────────────────────────
# 16-colour palette of the PicoCalc terminal (vt100 LUT) through SGR 38;5;n.
BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = 0, 1, 2, 3, 4, 5, 6, 7
GREY, BRED, BGREEN, BYELLOW, BBLUE, BMAGENTA, BCYAN, BWHITE = 8, 9, 10, 11, 12, 13, 14, 15
RESET = "\x1b[0m"


def paint(text, fg=None, bg=None):
    # Coloured text that always resets: a crash never leaves colour behind.
    codes = ""
    if fg is not None:
        codes += "\x1b[38;5;{}m".format(fg)
    if bg is not None:
        codes += "\x1b[48;5;{}m".format(bg)
    return codes + str(text) + RESET


def bar(fraction, width=10, fg=GREEN):
    try:
        full = int(fraction * width + 0.5)
    except Exception:
        full = 0
    full = max(0, min(width, full))
    return paint("█" * full, fg) + paint("░" * (width - full), GREY)


def key_bar(pairs):
    # One hint line, keys highlighted: key_bar((("n", "next"), ("q", "quit")))
    print("  ".join(paint(key, BYELLOW) + " " + label for key, label in pairs))


STATUS_TTL_MS = 20000
_STATUS = [None, 0, False, None]  # read at, UTC offset, Wi-Fi up, battery


def refresh_status():
    # Next title bar re-reads Wi-Fi/battery/offset (after a connect, a zone change).
    _STATUS[0] = None


def status_text():
    # "12:34  WiFi  Bat 87%": time once the clock is set, Wi-Fi, battery ("+"
    # while charging). Offset,
    # Wi-Fi and battery (16 ms + I2C) are re-read every STATUS_TTL_MS only:
    # viewers draw the title bar on every key.
    now = ticks_ms()
    if _STATUS[0] is None or ticks_diff(now, _STATUS[0]) > STATUS_TTL_MS:
        _STATUS[0] = now
        _STATUS[1] = utc_offset_hours()
        _STATUS[2] = wifi_connected()
        _STATUS[3] = battery()
    parts = []
    if clock_synced():
        t = local_time(_STATUS[1])
        parts.append("{:02d}:{:02d}".format(t[3], t[4]))
    if _STATUS[2]:
        parts.append("WiFi")
    if _STATUS[3] is not None:
        parts.append("Bat {}%{}".format(_STATUS[3][0], "+" if _STATUS[3][1] else ""))
    return "  ".join(parts)


def clip(text, limit):
    value = str(text)
    if len(value) <= limit:
        return value
    return value[:limit]


def wrap_text(text, width=DISPLAY_WIDTH):
    # … and € draw as three characters (CP437 lacks them): counted as such
    source = str(text).replace("\r\n", "\n").replace("\r", "\n").replace("…", "...").replace("€", "EUR")
    wrapped = []
    for paragraph in source.split("\n"):
        # leading spaces (code, an indented list) stay on the first line
        indent = min(len(paragraph) - len(paragraph.lstrip(" ")), width // 2)
        paragraph = paragraph.strip()
        if paragraph == "":
            wrapped.append("")
            continue

        parts = []
        parts_len = 0
        words = paragraph.split(" ")
        if indent:
            words[0] = " " * indent + words[0]
        for word in words:
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
    print(RESET + "\x1b[2J\x1b[H", end="")
    term = _terminal()
    if term is not None and hasattr(term, "wr"):
        _set_margins(term)  # straight to the PicoCalc, not to USB terminals


def title_bar(title, status=True):
    # Full-width coloured bar: title left, time/Wi-Fi/battery right.
    right = status_text() if status else ""
    left = " " + str(title)
    gap = DISPLAY_WIDTH - len(left) - len(right) - 1
    if gap < 1:
        right = ""
        gap = DISPLAY_WIDTH - len(left)
    line = (left + " " * gap + right + " ")[:DISPLAY_WIDTH]
    return paint(line, BWHITE, BLUE)


def screen_header(title, status=True):
    clear_screen()
    print(title_bar(title, status))
    print("")


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


def preview_print(text, width=DISPLAY_WIDTH, max_lines=PAGE_LINES, fg=None):
    lines = preview_lines(text, width=width, max_lines=max_lines)
    if not lines:
        print("(empty)")
        return 0
    for line in lines:
        print(paint(line, fg) if fg is not None else line)
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
            if wait_key(paint("-- more --", GREY) + "  " + paint("q", BYELLOW) + " stop") in _QUIT_KEYS:
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
# directly. USB serial is the keyboard only where there is no PicoCalc
# terminal (host tests, a bare Pico); with one, input on USB means a host
# tool wants the REPL.

_KEY_BUF = bytearray(1)
_STDIN_POLL = None
_CSI_KEYS = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
_TILDE_KEYS = {"1": "home", "2": "insert", "3": "del", "4": "end", "5": "pgup", "6": "pgdn", "7": "home", "8": "end"}


class HostTakeover(BaseException):
    # mpremote/Thonny wrote to the USB REPL while the PicoCalc screen is the
    # console. Not an Exception or KeyboardInterrupt, so the toolkit's own
    # except clauses let it through to the launcher, which hands over.
    pass


def _stdin_poll():
    global _STDIN_POLL
    if _STDIN_POLL is None:
        try:
            import select
            import sys

            _STDIN_POLL = select.poll()
            _STDIN_POLL.register(sys.stdin, select.POLLIN)
        except Exception:
            _STDIN_POLL = False
    return _STDIN_POLL


def _poll_byte():
    # One pending key byte, or None. Raises HostTakeover when USB serial has
    # input while the PicoCalc keyboard is the key source: reading it as keys
    # would eat the bytes mpremote/Thonny send to reach the REPL.
    if _INTERRUPTED[0]:  # a Ctrl+C the screen writer kept
        _INTERRUPTED[0] = False
        raise KeyboardInterrupt
    term = _terminal()
    poll = _stdin_poll()
    if term is not None:
        try:
            if term.readinto(_KEY_BUF):
                return _KEY_BUF[0]
        except Exception:  # keyboard MCU unpowered or busy, firmware bugs
            pass
        if poll and poll.poll(0):
            raise HostTakeover
        return None
    if poll and poll.poll(0):
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


def wait_key(prompt=None):
    # One-line prompt; erases itself after the key.
    if prompt is None:
        prompt = paint("-- any key --", GREY)
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
        _show_cursor()
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


_PICK_TOP = 3  # screen row of the first list row (title bar, blank line)


def _pick_row(labels, hints, i, selected):
    num = str(i + 1) if i < 9 else " "
    text = " {} {:<{}}{}".format(
        num, clip(labels[i], DISPLAY_WIDTH - 16), DISPLAY_WIDTH - 15, clip(hints[i], 12)
    )
    if selected:
        return paint("{:<{}}".format(text, DISPLAY_WIDTH)[:DISPLAY_WIDTH], BLACK, BCYAN)
    return " " + paint(num, BYELLOW) + text[2:]


def _pick_title(title, pos, count):
    return "{}  {}/{}".format(title, pos + 1, count) if count else title


def pick(title, labels, hints=None, footer=None, pos=0):
    """List screen: arrows (or j/k) move the highlight, 1-9 picks that row.
    Returns (key, index) for any other key ("enter" for a digit), index None
    when the list is empty. The caller acts on the key. Long lists scroll.
    footer: key_bar pairs, or a tuple of such rows."""
    count = len(labels)
    hints = hints or [""] * count
    footer = footer or (("\u2191\u2193", "choose"), ("Enter", "open"), ("q", "back"))
    if not isinstance(footer[0][0], tuple):
        footer = (footer,)
    rows = max(3, PAGE_LINES - 4)
    pos = max(0, min(pos, count - 1))
    top = None
    end_row = 1
    while True:
        if top is None or not top <= pos < top + rows:
            top = max(0, min(pos - rows // 2, count - rows))
            screen_header(_pick_title(title, pos, count))
            if not count:
                print(paint("  (empty)", GREY))
            for i in range(top, min(top + rows, count)):
                print(_pick_row(labels, hints, i, i == pos))
            print(paint("\u2500" * DISPLAY_WIDTH, GREY))
            for row in footer:
                key_bar(row)
            end_row = _PICK_TOP + max(1, min(rows, count - top)) + 1 + len(footer)
        try:
            key = read_key()
        except KeyboardInterrupt:
            return "esc", (pos if count else None)
        old = pos
        if key in ("down", "j"):
            pos = min(pos + 1, count - 1) if count else 0
        elif key in ("up", "k"):
            pos = max(pos - 1, 0)
        elif key and len(key) == 1 and "1" <= key <= "9":
            if int(key) <= count:
                return "enter", int(key) - 1
            continue
        else:
            return key, (pos if count else None)
        if pos != old and top <= pos < top + rows:
            # Same window: repaint the two rows and the counter in place,
            # then park the cursor under the footer for the caller's prompts.
            print("\x1b[1;1H" + title_bar(_pick_title(title, pos, count)), end="")
            for i in (old, pos):
                print(
                    "\x1b[{};1H\x1b[K{}".format(_PICK_TOP + i - top, _pick_row(labels, hints, i, i == pos)),
                    end="",
                )
            print("\x1b[{};1H".format(end_row), end="")


def select_list(title, labels, hints=None, footer=None):
    """Pick one of `labels` with arrows + Enter or its number (1-9); returns
    the index, or None on q/Esc. Long lists scroll."""
    pos = 0
    while labels:
        key, pos = pick(title, labels, hints, footer, pos)
        if key in ("enter", "right"):
            return pos
        if key in _QUIT_KEYS or key == "left":
            return None
    return None


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
        screen_header("{}  {}/{}".format(title, pos + 1, total))

        if render_summary is not None:
            render_summary(item, pos, total)

        print(paint("\u2500" * DISPLAY_WIDTH, GREY))
        key_bar((("n/\u2192", "next"), ("p/\u2190", "prev"), ("d/Enter", "detail")))
        key_bar((("q/Esc", "back"), ("0-9", "jump")))

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


# OSError args[0] from the device's network stack: lwIP errnos with Linux
# numbers (a host's errno module has others), getaddrinfo's -2, and ENOMEM
# when mbedtls can't get its ~20 KB of TLS buffers.
_NET_ERRORS = {
    -2: "DNS failed",
    12: "out of memory",
    103: "connection reset",
    104: "connection reset",
    110: "timed out",
    113: "unreachable",
}


def net_error(error):
    """A failed request in a few words, to print after a label of up to 5
    characters ("Err:"): a known errno as text, else the message (mbedtls
    errors carry one), clipped to fit."""
    if isinstance(error, MemoryError):
        return "out of memory"  # str() of it is empty
    text = error
    args = getattr(error, "args", ())
    if isinstance(error, OSError) and args:
        if isinstance(args[0], int) and args[0] in _NET_ERRORS:
            return _NET_ERRORS[args[0]]
        if len(args) > 1:
            text = args[1]  # mbedtls: (code, text)
    return clip(text, DISPLAY_WIDTH - 6) or type(error).__name__


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
