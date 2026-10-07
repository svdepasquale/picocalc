"""Draw straight into the PicoCalc framebuffer. While gfx owns the screen
(begin() to end(), the launcher's session), whatever the apps print is drawn
here too, by the screen console below, instead of the firmware terminal: a
full screen of text takes ~18 ms this way against ~220 ms through the
terminal (measured 2026-10-06)."""

import sys

import pico_utils as _pu
from pico_utils import _terminal as _pu_terminal
from pico_utils import clear_screen as _clear_screen, status_text as _status_text
from pico_utils import to_cp437 as _to_cp437
from pico_utils import BLACK, BLUE, BWHITE, BYELLOW, WHITE
from pico_utils import CON_COLS, CON_ROW_H, CON_ROW_TOP, CON_ROWS, CON_TITLE_H

try:
    import framebuf as _framebuf
except ImportError:  # CPython
    _framebuf = None

MODULE_VERSION = "2026-10-07.1"

WIDTH = 320
HEIGHT = 320
CHAR_W = 6  # 6x8 font: text() advances 6 pixels and draws 5 of them
CHAR_H = 8
COLUMNS = WIDTH // CHAR_W
TITLE_H = 12
_ROW_BYTES = WIDTH // 2  # 4 bpp

# Every call costs: ~11 us a Python call, ~15-20 us a builtin, 103 us str()
# of a str (measured 2026-10-06). Hence the display kept from begin() to
# end() and text passed as bytes where it is drawn often (cp() once).
_FB = [None]


# ── font codes ──────────────────────────────────

SPECIAL = 1  # box/block codes, drawn as lines and rectangles
SLOW = 2  # a character that folds to several (…, €): converted in Python
HIGH = 4  # codes from 0x8E: the firmware font is misaligned there

# The firmware's font (picocalcdisplay font6x8e500.h) gives 0x8E, 0x8F and
# 0xF4 seven rows instead of eight, so text() draws every glyph from 0x8E on
# one to three rows up with the next glyph's top below (ò, ù, ·, ≈, ■...).
# The same glyphs, realigned (the short ones padded with an empty last row),
# for 0x8E-0xFF; text() still draws the codes below.
_HIGH_FONT = (
    b"\x50\x00\x88\xf8\x88\x88\x00\x00\x20\x00\x88\xf8\x88\x88\x00\x00"
    b"\x10\x20\xf8\x80\xf8\x80\xf8\x00\x00\x00\xd0\x28\xf8\xa0\x58\x00"
    b"\x38\x60\xa0\xb8\xe0\xa0\xb8\x00\x20\x50\x70\x88\x88\x88\x70\x00"
    b"\x20\x00\x70\x88\x88\x88\x70\x00\x20\x10\x70\x88\x88\x88\x70\x00"
    b"\x20\x50\x88\x88\x88\x88\x78\x00\x40\x20\x88\x88\x88\x88\x78\x00"
    b"\x50\x00\x88\x88\x88\x78\x08\xf0\x50\x70\x88\x88\x88\x88\x70\x00"
    b"\x50\x88\x88\x88\x88\x88\x70\x00\x00\x20\x70\xa0\xa0\x70\x20\x00"
    b"\x18\x20\x20\xf8\x20\x40\xf8\x00\x88\x50\xf8\x20\xf8\x20\x20\x00"
    b"\xc0\xa0\xc0\xa0\x70\x20\x38\x00\x18\x20\xf8\x20\x20\x20\x20\xc0"
    b"\x20\x40\x70\x08\x78\x88\x78\x00\x10\x20\x00\x60\x20\x20\x20\x00"
    b"\x20\x40\x70\x88\x88\x88\x70\x00\x10\x20\x88\x88\x88\x88\x78\x00"
    b"\xa0\x50\xf0\x88\x88\x88\x88\x00\xa0\x50\x88\xc8\xa8\x98\x88\x00"
    b"\x60\x90\x78\x00\x00\x00\x00\x00\x30\x48\x48\x30\x00\x00\x00\x00"
    b"\x20\x00\x20\x40\x80\x88\x70\x00\x00\x00\x00\xf8\x80\x00\x00\x00"
    b"\x00\x00\x00\xf8\x08\x00\x00\x00\x40\x48\x50\x38\x48\x90\x38\x00"
    b"\x40\x48\x50\x28\x58\xb8\x08\x00\x20\x00\x20\x20\x20\x20\x20\x00"
    b"\x00\x28\x50\xa0\x50\x28\x00\x00\x00\xa0\x50\x28\x50\xa0\x00\x00"
    b"\x92\x00\x49\x00\x24\x00\x92\x00\xaa\x55\xaa\x55\xaa\x55\xaa\x55"
    b"\x6d\xff\xb6\xff\xdb\xff\x6d\xff\x10\x10\x10\x10\x10\x10\x10\x10"
    b"\x10\x10\x10\xf0\x10\x10\x10\x10\x10\x10\x10\xf0\xf0\x10\x10\x10"
    b"\x18\x18\x18\xf8\x18\x18\x18\x18\x00\x00\x00\xf8\x18\x18\x18\x18"
    b"\x00\x00\x00\xf0\xf0\x10\x10\x10\x18\x18\x18\xf8\xf8\x18\x18\x18"
    b"\x18\x18\x18\x18\x18\x18\x18\x18\x00\x00\x00\xf8\xf8\x18\x18\x18"
    b"\x18\x18\x18\xf8\xf8\x00\x00\x00\x18\x18\x18\xf8\x00\x00\x00\x00"
    b"\x10\x10\x10\xf0\xf0\x00\x00\x00\x00\x00\x00\xf0\x10\x10\x10\x10"
    b"\x10\x10\x10\x1f\x00\x00\x00\x00\x10\x10\x10\xff\x00\x00\x00\x00"
    b"\x00\x00\x00\xff\x10\x10\x10\x10\x10\x10\x10\x1f\x10\x10\x10\x10"
    b"\x00\x00\x00\xff\x00\x00\x00\x00\x10\x10\x10\xff\x10\x10\x10\x10"
    b"\x10\x10\x10\x1f\x1f\x10\x10\x10\x18\x18\x18\x1f\x18\x18\x18\x18"
    b"\x18\x18\x18\x1f\x1f\x00\x00\x00\x00\x00\x00\x1f\x1f\x18\x18\x18"
    b"\x18\x18\x18\xff\xff\x00\x00\x00\x00\x00\x00\xff\xff\x18\x18\x18"
    b"\x18\x18\x18\x1f\x1f\x18\x18\x18\x00\x00\x00\xff\xff\x00\x00\x00"
    b"\x18\x18\x18\xff\xff\x18\x18\x18\x10\x10\x10\xff\xff\x00\x00\x00"
    b"\x18\x18\x18\xff\x00\x00\x00\x00\x00\x00\x00\xff\xff\x10\x10\x10"
    b"\x00\x00\x00\xff\x18\x18\x18\x18\x18\x18\x18\x1f\x00\x00\x00\x00"
    b"\x10\x10\x10\x1f\x1f\x00\x00\x00\x00\x00\x00\x1f\x1f\x10\x10\x10"
    b"\x00\x00\x00\x1f\x18\x18\x18\x18\x18\x18\x18\xff\x18\x18\x18\x18"
    b"\x10\x10\x10\xff\xff\x10\x10\x10\x10\x10\x10\xf0\x00\x00\x00\x00"
    b"\x00\x00\x00\x1f\x10\x10\x10\x10\xff\xff\xff\xff\xff\xff\xff\xff"
    b"\x00\x00\x00\x00\xff\xff\xff\xff\xe0\xe0\xe0\xe0\xe0\xe0\xe0\xe0"
    b"\x1c\x1c\x1c\x1c\x1c\x1c\x1c\x1c\xfc\xfc\xfc\xfc\x00\x00\x00\x00"
    b"\x00\x00\x68\x98\x90\x90\x68\x00\x70\x88\x88\xf0\x88\x88\xf0\x80"
    b"\xf8\x80\x80\x80\x80\x80\x80\x00\xf8\x50\x50\x50\x50\x50\x50\x00"
    b"\xf8\x40\x20\x10\x20\x40\xf8\x00\x00\x18\x60\x98\x88\x88\x70\x00"
    b"\x00\x00\x88\x88\x88\x98\xe8\x80\x00\x00\x00\xf8\x20\x20\x20\x00"
    b"\x20\x70\xa8\xa8\xa8\x70\x20\x00\x70\x88\xd8\xf8\xd8\x88\x70\x00"
    b"\x70\x88\x88\x88\x88\x50\xd8\x00\x78\x80\x70\x88\x88\x88\x70\x00"
    b"\x00\x00\x50\xa8\xa8\x50\x00\x00\x00\x58\xa8\xa8\x70\x20\x20\x00"
    b"\x00\x00\x70\x80\x70\x80\x70\x00\x20\x50\x88\x88\x88\x88\x88\x00"
    b"\x00\xf8\x00\xf8\x00\xf8\x00\x00\x20\x20\xf8\x20\x20\x00\xf8\x00"
    b"\x00\x40\x30\x08\x30\x40\xf8\x00\x00\x10\x60\x80\x60\x10\xf8\x00"
    b"\x18\x20\x20\x20\x20\x20\x20\x00\x20\x20\x20\x20\x20\x20\x20\xc0"
    b"\x00\x30\x00\xf8\x00\x60\x00\x00\x00\x30\xc8\x00\x30\xc8\x00\x00"
    b"\x30\x48\x48\x30\x00\x00\x00\x00\x00\x00\x00\x10\x00\x00\x00\x00"
    b"\x00\x00\x00\x10\x00\x00\x00\x00\x08\x08\x10\x10\x20\xa0\x40\x00"
    b"\xe0\x90\x90\x90\x00\x00\x00\x00\x60\x10\x40\xf0\x00\x00\x00\x00"
    b"\x00\x00\x70\x70\x70\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
)
_SPECIALS = b"\xb0\xb1\xb2\xb3\xba\xc4\xcd\xdb\xdc\xdd\xde\xdf"


def _tables():
    # to_codes()' tables, from pico_utils' CP437 mapping and folds: [0:128]
    # codes for U+0080..U+00FF, [128:384] flags per code, then the code
    # points above U+00FF (uint16, little-endian, sorted) and their codes.
    latin = bytearray(b"?" * 128)
    wide = {}
    for src, dst in zip(_pu._CP437_SRC, _pu._CP437_DST):
        if ord(src) < 0x100:
            latin[ord(src) - 0x80] = ord(dst)
        else:
            wide[ord(src)] = ord(dst)
    for src, dst in _pu._FOLDS.items():
        code = ord(dst) if len(dst) == 1 else 0
        if ord(src) < 0x100:
            latin[ord(src) - 0x80] = code
        else:
            wide[ord(src)] = code
    wide[ord("←")] = 0x1B  # an escape for the terminal, a glyph here
    keys = sorted(wide)
    kinds = bytearray(256)
    for code in range(0x8E, 256):
        kinds[code] = HIGH
    for code in _SPECIALS:
        kinds[code] = SPECIAL
    packed = bytearray(latin) + kinds
    for k in keys:
        packed += bytes((k & 0xFF, k >> 8))
    packed += bytes(wide[k] for k in keys)
    return packed, len(keys), kinds


_TABLES, _NKEYS, _KINDS = _tables()


def _scan_py(buf, start, end):
    high = 0
    for i in range(start, end):
        if buf[i] < 0x20:
            return (i << 1) | high
        if buf[i] >= 0x80:
            high = 1
    return (end << 1) | high


def _to_codes_py(src, dst, tables, nkeys):
    codes = _codes_py(_decode(src))
    dst[: len(codes)] = codes
    return len(codes) | (_kinds_of_py(codes, 0, len(codes), tables[128:384]) << 16)


def _kinds_of_py(buf, start, end, kinds):
    flags = 0
    for i in range(start, end):
        flags |= kinds[buf[i]]
    return flags


def _move_up_py(buf, dst, src, nbytes):
    # Front to back in blocks no longer than the shift: they never overlap.
    view = memoryview(buf)
    step = src - dst
    done = 0
    while done < nbytes:
        size = min(step, nbytes - done)
        view[dst + done : dst + done + size] = view[src + done : src + done + size]
        done += size


try:
    from gfx_native import kinds_of as _kinds_of, move_up as _move_up
    from gfx_native import scan as _scan, to_codes as _to_codes

    NATIVE = True
except Exception:  # no native code on this port: SyntaxError, ImportError
    _scan, _to_codes, _kinds_of, _move_up = _scan_py, _to_codes_py, _kinds_of_py, _move_up_py
    NATIVE = False


def _decode(data):
    try:
        return str(data, "utf-8")
    except Exception:
        return "".join(chr(b) if b < 0x80 else "?" for b in data)


def _codes_py(text):
    # 0x1B is an escape for the terminal (pico_utils folds ← to <), a glyph here
    return bytes(map(ord, _to_cp437(text.replace("←", "\x1b"))))


def cp(text):
    """Text as bytes in the font's codes (CP437). Bytes pass through; codes
    below 16 draw as spaces."""
    kind = type(text)
    if kind is bytes:
        return text
    if kind is not str:
        text = str(text)
    data = text.encode()
    if len(data) == len(text):
        return data
    return _codes_py(text)


# ── target ──────────────────────────────────────


def surface():
    # picocalc.display: a framebuf.FrameBuffer, 4 bpp, colours 0-15 = the
    # terminal palette (SGR 38;5;n draws nibble n). Looked up at each
    # begin(): every run of boot.py builds a new one. None away from the
    # PicoCalc.
    mod = sys.modules.get("picocalc")
    if mod is None:
        return None
    return getattr(mod, "display", None)


def available():
    return surface() is not None


_terminal = _pu_terminal


def _term_write(text):
    term = _terminal()
    if term is not None and hasattr(term, "wr"):
        term.wr(text)


def begin():
    """Take the screen from the terminal. Paint all of it next: drawing works
    from here to end(), and what is printed meanwhile goes to the console."""
    # The firmware cursor blinks from a 250 ms timer interrupt that draws over
    # whatever is at its cell, and hiding it repaints the terminal's own
    # (stale) cell there: hence the full paint. Home: should the terminal ever
    # print again, it starts on row 1, never on row 40 (pico_utils._set_margins).
    fb = surface()
    _FB[0] = fb
    _pu._CONSOLE[0] = CON if fb is not None else None
    CON.reset()
    CON.cursor = False
    _term_write("\x1b[?25l\x1b[H")


def end():
    """Give the screen back to the terminal: cleared, cursor on."""
    _pu._CONSOLE[0] = None
    fb = _FB[0]
    _FB[0] = None
    if fb is not None:
        # The terminal clears its 53 columns: 318 of the 320 pixels.
        fb.fill_rect(0, 0, WIDTH, HEIGHT, BLACK)
    _clear_screen()
    _term_write("\x1b[?25h")


def app():
    """A clean screen for an app run from the launcher: what it prints is
    drawn by the console from the top."""
    CON.clear()


def suspend():
    """Hand the screen to the firmware terminal for a program that writes to
    it directly (the pye editor). True if there was a console to resume."""
    if _pu._CONSOLE[0] is None or _FB[0] is None:
        return False
    _pu._CONSOLE[0] = None
    _FB[0].fill_rect(0, 0, WIDTH, HEIGHT, BLACK)
    _clear_screen()
    _term_write("\x1b[?25h")
    return True


def resume():
    if _FB[0] is None:  # end() came in between: begin() takes the screen again
        return
    _term_write("\x1b[?25l\x1b[H")
    _pu._CONSOLE[0] = CON
    CON.clear()


# ── drawing ─────────────────────────────────────


def fill_rect(x, y, w, h, color):
    fb = _FB[0]
    if fb is not None:
        fb.fill_rect(x, y, w, h, color)


def hline(x, y, w, color):
    fb = _FB[0]
    if fb is not None:
        fb.hline(x, y, w, color)


def clear(color=BLACK):
    fill_rect(0, 0, WIDTH, HEIGHT, color)


_GLYPHS = {}
_PALETTE = [None]


def _glyph(fb, code, x, y, fg):
    # A glyph from 0x8E on, from the realigned table.
    if _framebuf is None:
        fb.text(bytes((code,)), x, y, fg)
        return
    glyph = _GLYPHS.get(code)
    if glyph is None:
        base = (code - 0x8E) * 8
        rows = bytearray(b & 0xF8 for b in _HIGH_FONT[base : base + 8])  # 5 columns, like text()
        glyph = _framebuf.FrameBuffer(rows, 8, 8, _framebuf.MONO_HLSB)
        _GLYPHS[code] = glyph
    pal = _PALETTE[0]
    if pal is None:
        pal = _framebuf.FrameBuffer(bytearray(1), 2, 1, _framebuf.GS4_HMSB)
        _PALETTE[0] = pal
    off = (fg + 1) & 15  # blit() compares its key after the palette
    pal.pixel(0, 0, off)
    pal.pixel(1, 0, fg)
    fb.blit(glyph, x, y, off, pal)


def _special(fb, code, x, y, w, fg, band_y, band_h):
    # Box and block characters as lines and rectangles: they join up across
    # cells and rows, where the 5-column glyphs leave gaps.
    if code == 0xC4:  # ─
        fb.hline(x, y + 3, w, fg)
    elif code == 0xCD:  # ═
        fb.hline(x, y + 2, w, fg)
        fb.hline(x, y + 4, w, fg)
    elif code == 0xDF:  # ▀
        fb.fill_rect(x, y, w, 4, fg)
    elif code == 0xDC:  # ▄
        fb.fill_rect(x, y + 4, w, 4, fg)
    elif code in (0xDD, 0xDE, 0xB3, 0xBA):  # ▌ ▐ │ ║: per cell
        for cx in range(x, x + w, CHAR_W):
            if code == 0xDD:
                fb.fill_rect(cx, y, 3, CHAR_H, fg)
            elif code == 0xDE:
                fb.fill_rect(cx + 3, y, 3, CHAR_H, fg)
            elif code == 0xB3:
                fb.vline(cx + 2, band_y, band_h, fg)
            else:
                fb.vline(cx + 1, band_y, band_h, fg)
                fb.vline(cx + 3, band_y, band_h, fg)
    else:  # █ ░ ▒ ▓
        fb.fill_rect(x, y, w, CHAR_H, fg)


def _draw_codes(fb, codes, x, y, fg, flags, band_y, band_h, left=False, right=False):
    # Draw CP437 codes with their top left at x, y. flags (to_codes/kinds_of)
    # say whether anything needs more than one text() call. left/right: a
    # line of ─ that starts or ends the run reaches the screen's edge.
    if not flags:
        fb.text(codes, x, y, fg)
        return
    n = len(codes)
    kinds = _KINDS
    start = i = 0
    while i < n:
        code = codes[i]
        kind = kinds[code]
        if not kind:
            i += 1
            continue
        if i > start:
            fb.text(codes[start:i], x + start * CHAR_W, y, fg)
        j = i + 1
        if kind & SPECIAL:
            while j < n and codes[j] == code:
                j += 1
            x0 = x + i * CHAR_W
            x1 = x + j * CHAR_W
            if code == 0xC4:
                if left and i == 0:
                    x0 = 0
                if right and j == n:
                    x1 = WIDTH
            _special(fb, code, x0, y, x1 - x0, fg, band_y, band_h)
        else:
            _glyph(fb, code, x + i * CHAR_W, y, fg)
        i = start = j
    if start < n:
        fb.text(codes[start:] if start else codes, x + start * CHAR_W, y, fg)


def text(s, x, y, fg=WHITE, bg=None):
    """Draw s with its top left at pixel x, y; returns the x after it. The font
    draws glyph pixels only: bg fills the text's cells first."""
    if type(s) is not bytes:
        s = cp(s)
    fb = _FB[0]
    if fb is not None:
        if bg is not None:
            fb.fill_rect(x, y, len(s) * CHAR_W, CHAR_H, bg)
        _draw_codes(fb, s, x, y, fg, _kinds_of(s, 0, len(s), _KINDS), y, CHAR_H)
    return x + len(s) * CHAR_W


def center(s, y, fg=WHITE):
    s = cp(s)
    text(s, max(0, (WIDTH - len(s) * CHAR_W) // 2), y, fg)


def key_bar(pairs, x, y, fg=WHITE):
    # "key label  key label", keys highlighted, like pico_utils.key_bar().
    for key, label in pairs:
        x = text(key, x, y, BYELLOW) + CHAR_W
        x = text(label, x, y, fg) + 2 * CHAR_W
    return x


def title_bar(title, status=True):
    # Full-width bar: title left, time/Wi-Fi/battery right.
    fill_rect(0, 0, WIDTH, TITLE_H, BLUE)
    y = (TITLE_H - CHAR_H) // 2
    text(title, CHAR_W, y, BWHITE)
    right = cp(_status_text()) if status else b""
    if right:
        text(right, WIDTH - (len(right) + 1) * CHAR_W, y, BWHITE)


# ── console ─────────────────────────────────────
# pico_utils' screen writer hands everything printed to Console.write()
# while gfx owns the screen: apps, prompts, input() echo, tracebacks. It
# understands the VT100 the toolkit and MicroPython's readline send: SGR
# colours (0, 1, 7, 22, 27, 30-37, 39, 40-47, 49, 90-97, 100-107, 38;5;n,
# 48;5;n), cursor position and moves (H f A B C D G d, ESC 7/8, s u), erase
# in line and display (K J), ?25 l/h for its own cursor; CR, LF (as CR LF),
# BS, TAB. Other sequences are dropped. Row 1 is the 12-pixel title row and
# never scrolls; rows 2-31 are 10 pixels; 53 columns; wrap at the edge.

_X0 = 1  # left of column 1


_WIDE = _pu.DISPLAY_WIDTH  # the apps' line width: a run reaching it spans the screen


class Console:
    # The hot path counts calls: on this device each costs 10-20 us, more
    # than drawing the text (gfx timings), so row geometry is kept, not
    # recomputed, and ASCII skips the conversion.

    def __init__(self):
        self.out = bytearray(128)
        self.reset()

    def reset(self):
        self._goto(1)
        self.col = 1  # CON_COLS + 1: a wrap is due before the next character
        self.fg = WHITE
        self.bg = BLACK
        self.bold = False
        self.rev = False
        self.saved = (1, 1)
        self.carry = b""
        self.cursor = True  # shown while input waits (?25l hides it)
        self.beam = None  # (x, y, colour under it) while the cursor is drawn

    def clear(self):
        fb = _FB[0]
        if fb is not None:
            fb.fill_rect(0, 0, WIDTH, HEIGHT, BLACK)
        self.reset()

    def _goto(self, row):
        self.row = row
        if row == 1:
            self.y = 0
            self.h = CON_TITLE_H
            self.ty = (CON_TITLE_H - CHAR_H) // 2
        else:
            self.y = CON_ROW_TOP + (row - 2) * CON_ROW_H
            self.h = CON_ROW_H
            self.ty = self.y + 1

    # ── the hook ──

    def draw(self, buf):
        # pico_utils' screen writer calls this; True once drawn. Small on
        # purpose (a frame over 11 words costs ~240 us): print's line end, a
        # lone "\r\n", is handled here, the rest by write(). A Ctrl+C landing
        # in here is kept for the next key read (raised out of the writer,
        # it would make dupterm detach the screen); other errors fall back
        # to the terminal.
        try:
            if len(buf) == 2 and buf[0] == 13 and buf[1] == 10 and not self.carry and self.beam is None:
                if _FB[0] is not None:
                    self._newline(_FB[0])
            else:
                self.write(buf)
        except KeyboardInterrupt:
            _pu._INTERRUPTED[0] = True
        except Exception:
            return False
        return True

    def write(self, buf):
        # One function for the common cases: a call with this many locals
        # takes ~240 us here (MicroPython puts the frame on the heap), a
        # small one ~20. ASCII that fits the row and the colour codes paint()
        # sends are handled inline; the rest goes to the helpers below.
        fb = _FB[0]
        if fb is None:
            return
        kind = type(buf)
        data = buf if kind is bytes else buf.encode() if kind is str else bytes(buf)
        if self.carry:
            data = self.carry + data
            self.carry = b""
        if self.beam is not None:
            self._beam_off(fb)
        n = len(data)
        i = 0
        while i < n:
            res = _scan(data, i, n)
            j = res >> 1
            if j > i:
                col = self.col
                m = j - i
                if res & 1 or col + m > CON_COLS + 1 or self.bold:
                    if res & 1 and j == n and data[n - 1] >= 0x80:
                        cut = _utf8_cut(data, i, n)
                        if cut < n:
                            self.carry = data[cut:]
                            if cut > i:
                                self._text(fb, data[i:cut], 1)
                            return
                    self._text(fb, data if m == n else data[i:j], res & 1)
                else:
                    x = col * CHAR_W - CHAR_W + _X0
                    if self.rev:
                        fg = self.bg
                        bg = self.fg
                    else:
                        fg = self.fg
                        bg = self.bg
                    x0 = 0 if col == 1 else x
                    x1 = WIDTH if bg != BLACK and col + m > _WIDE else x + m * CHAR_W
                    fb.fill_rect(x0, self.y, x1 - x0, self.h, bg)
                    fb.text(data if m == n else data[i:j], x, self.ty, fg)
                    self.col = col + m
                i = j
                continue
            code = data[i]
            if code == 0x1B:
                if data.startswith(b"\x1b[0m", i):
                    self.fg = WHITE
                    self.bg = BLACK
                    self.bold = self.rev = False
                    i += 4
                    continue
                if data.startswith(b"8;5;", i + 3) and data[i + 1] == 0x5B and data[i + 2] in (0x33, 0x34):
                    end = data.find(b"m", i + 7, i + 10)  # ESC [ 38;5;N m / 48;5;N m
                    if end > 0 and data[i + 7 : end].isdigit():
                        color = int(data[i + 7 : end]) & 15
                        if data[i + 2] == 0x33:
                            self.fg = color
                        else:
                            self.bg = color
                        i = end + 1
                        continue
                k = self._escape(fb, data, i, n)
                if k < 0:
                    self.carry = data[i:]
                    return
                i = k
                continue
            if code == 0x0A:
                self._newline(fb)
            elif code == 0x0D:
                self.col = 1
            elif code == 0x08:
                self._back(1)
            elif code == 0x09:
                self.col = min(CON_COLS, (min(self.col, CON_COLS) - 1) // 8 * 8 + 9)
            i += 1

    def blocks(self, rows, row, col, fg):
        # Text rows whose █ cells join into solid shapes: each run of █ fills
        # its rows' full height (the clock's digits); the rest is cleared.
        fb = _FB[0]
        if fb is None:
            return
        if self.beam is not None:
            self._beam_off(fb)
        x0 = _X0 + (col - 1) * CHAR_W
        for line in rows:
            self._goto(min(row, CON_ROWS))
            fb.fill_rect(0, self.y, WIDTH, self.h, BLACK)
            i = line.find("\u2588")
            while i >= 0:
                j = i
                while j < len(line) and line[j] == "\u2588":
                    j += 1
                fb.fill_rect(x0 + i * CHAR_W, self.y, (j - i) * CHAR_W, self.h, fg)
                i = line.find("\u2588", j)
            row += 1
        self.col = 1

    def waiting(self):
        # Someone waits for typed text (input(), read_line): show the cursor,
        # a line under the cell in the gap between rows, where no glyph is.
        fb = _FB[0]
        if fb is None or not self.cursor or self.beam is not None:
            return
        x = _X0 + (min(self.col, CON_COLS) - 1) * CHAR_W
        y = self.ty + CHAR_H  # this row's last line, then the next row's first
        self.beam = (x, y, fb.pixel(x, y), fb.pixel(x, y + 1))
        fb.fill_rect(x, y, CHAR_W - 1, 2, BWHITE)

    def _beam_off(self, fb):
        x, y, top, bottom = self.beam
        fb.hline(x, y, CHAR_W - 1, top)
        fb.hline(x, y + 1, CHAR_W - 1, bottom)
        self.beam = None

    def _back(self, n):
        # n columns left; past column 1 on to the end of the row above (not
        # into the title row), as a typed line that wrapped is erased. From
        # the wrap position (after the last column) the first step is the
        # last column.
        col = self.col - n
        while col < 1 and self.row > 2:
            col += CON_COLS
            self._goto(self.row - 1)
        self.col = max(1, col)

    # ── text ──

    def _text(self, fb, run, high):
        # A run without control bytes; high: it has non-ASCII bytes.
        flags = 0
        codes = run
        if high:
            if len(run) > len(self.out):
                self.out = bytearray(len(run) + 32)
            res = _to_codes(run, self.out, _TABLES, _NKEYS)
            flags = res >> 16
            if flags & SLOW:
                codes = _codes_py(_decode(run))
                flags = _kinds_of(codes, 0, len(codes), _KINDS)
            else:
                codes = bytes(memoryview(self.out)[: res & 0xFFFF])
        n = len(codes)
        if self.col > CON_COLS:
            self._newline(fb)
        if self.col + n <= CON_COLS + 1:  # fits on this row
            self._put(fb, codes, flags)
            self.col += n
            return
        pos = 0
        while pos < n:
            if self.col > CON_COLS:
                self._newline(fb)
            k = min(CON_COLS - self.col + 1, n - pos)
            part = codes[pos : pos + k]
            self._put(fb, part, flags and _kinds_of(part, 0, k, _KINDS))
            self.col += k
            pos += k

    def _put(self, fb, codes, flags):
        # A run on the current row from the current column. From column 1 it
        # also paints the left edge; a coloured run out to the apps' width
        # paints to the right edge (title and highlight bars span the
        # screen). A black one stops at its last cell: a typed character in
        # column 53 must survive the space that erases column 52.
        col = self.col
        n = len(codes)
        x = col * CHAR_W - CHAR_W + _X0
        if self.rev:
            fg = self.bg
            bg = self.fg
        else:
            fg = self.fg
            bg = self.bg
        x0 = 0 if col == 1 else x
        reaches = col + n > _WIDE
        x1 = WIDTH if bg != BLACK and reaches else x + n * CHAR_W
        fb.fill_rect(x0, self.y, x1 - x0, self.h, bg)
        if flags:
            _draw_codes(fb, codes, x, self.ty, fg, flags, self.y, self.h, col == 1, reaches)
        else:
            fb.text(codes, x, self.ty, fg)
        if self.bold:
            _draw_codes(fb, codes, x + 1, self.ty, fg, flags, self.y, self.h)

    def _newline(self, fb):
        self.col = 1
        if self.row < CON_ROWS:
            self._goto(self.row + 1)
        else:
            self._scroll(fb)

    def _scroll(self, fb):
        # Rows 3..31 up one; row 1 (the title) stays. Bytes, not pixels:
        # framebuf.scroll() took 55 ms, this ~4 ms.
        top = CON_ROW_TOP * _ROW_BYTES
        step = CON_ROW_H * _ROW_BYTES
        _move_up(fb, top, top + step, (CON_ROWS - 2) * step)
        last, _ = _band(CON_ROWS)
        fb.fill_rect(0, last, WIDTH, HEIGHT - last, BLACK)

    # ── escapes ──

    def _escape(self, fb, data, i, n):
        # Index after the sequence at data[i] (ESC); -1 if it is unfinished.
        if i + 1 >= n:
            return -1
        kind = data[i + 1]
        if kind == 0x5B:  # CSI
            k = i + 2
            while k < n:
                c = data[k]
                if 0x40 <= c <= 0x7E:
                    self._csi(fb, data[i + 2 : k], c)
                    return k + 1
                if k - i > 32:
                    return k  # garbage: drop it
                k += 1
            return -1
        if kind in (0x28, 0x29):  # charset: ESC ( x
            return i + 3 if i + 2 < n else -1
        if kind == 0x37:  # ESC 7
            self.saved = (self.row, self.col)
        elif kind == 0x38:  # ESC 8
            self._goto(self.saved[0])
            self.col = self.saved[1]
        return i + 2

    def _csi(self, fb, params, final):
        if final == 0x6D:  # m
            self._sgr(params)
            return
        if params[:1] == b"?":
            if params == b"?25" and final in (0x68, 0x6C):
                self.cursor = final == 0x68  # h shows, l hides
            return
        nums = params.split(b";") if params else ()
        first = _num(nums, 0, 0)
        if final in (0x48, 0x66):  # H f
            self._goto(min(max(first, 1), CON_ROWS))
            self.col = min(max(_num(nums, 1, 1), 1), CON_COLS)
        elif final == 0x4B:  # K
            self._erase_line(fb, first)
        elif final == 0x4A:  # J
            self._erase_screen(fb, first)
        elif final == 0x41:  # A
            self._goto(max(1, self.row - max(first, 1)))
        elif final == 0x42:  # B
            self._goto(min(CON_ROWS, self.row + max(first, 1)))
        elif final == 0x43:  # C
            self.col = min(CON_COLS, min(self.col, CON_COLS) + max(first, 1))
        elif final == 0x44:  # D
            self._back(max(first, 1))
        elif final == 0x47:  # G
            self.col = min(max(first, 1), CON_COLS)
        elif final == 0x64:  # d
            self._goto(min(max(first, 1), CON_ROWS))
        elif final == 0x73:  # s
            self.saved = (self.row, self.col)
        elif final == 0x75:  # u
            self._goto(self.saved[0])
            self.col = self.saved[1]

    def _sgr(self, params):
        nums = params.split(b";") if params else (b"",)
        k = 0
        while k < len(nums):
            value = _num(nums, k, 0)
            if value == 0:
                self.fg, self.bg, self.bold, self.rev = WHITE, BLACK, False, False
            elif value == 1:
                self.bold = True
            elif value == 22:
                self.bold = False
            elif value == 7:
                self.rev = True
            elif value == 27:
                self.rev = False
            elif 30 <= value <= 37:
                self.fg = value - 30
            elif value == 39:
                self.fg = WHITE
            elif 40 <= value <= 47:
                self.bg = value - 40
            elif value == 49:
                self.bg = BLACK
            elif 90 <= value <= 97:
                self.fg = value - 82
            elif 100 <= value <= 107:
                self.bg = value - 92
            elif value in (38, 48) and _num(nums, k + 1, 0) == 5:
                color = _num(nums, k + 2, 0) & 15
                if value == 38:
                    self.fg = color
                else:
                    self.bg = color
                k += 2
            k += 1

    def _erase_line(self, fb, mode):
        x = _X0 + (min(self.col, CON_COLS) - 1) * CHAR_W
        bg = self.fg if self.rev else self.bg
        if mode == 0:
            x0, x1 = (0 if self.col == 1 else x), WIDTH
        elif mode == 1:
            x0, x1 = 0, x + CHAR_W
        else:
            x0, x1 = 0, WIDTH
        fb.fill_rect(x0, self.y, x1 - x0, self.h, bg)

    def _erase_screen(self, fb, mode):
        bg = self.fg if self.rev else self.bg
        if mode == 0:
            self._erase_line(fb, 0)
            fb.fill_rect(0, self.y + self.h, WIDTH, HEIGHT - self.y - self.h, bg)
        elif mode == 1:
            fb.fill_rect(0, 0, WIDTH, self.y, bg)
            self._erase_line(fb, 1)
        else:
            fb.fill_rect(0, 0, WIDTH, HEIGHT, bg)


def _band(row):
    # Top and height of a console row.
    if row == 1:
        return 0, CON_TITLE_H
    return CON_ROW_TOP + (row - 2) * CON_ROW_H, CON_ROW_H


def _num(nums, index, default):
    if index < len(nums) and nums[index]:
        try:
            return int(nums[index])
        except ValueError:
            pass
    return default


def _utf8_cut(data, start, end):
    # Where an unfinished UTF-8 sequence at the end of data[start:end] begins
    # (it waits for the next write), else end.
    k = end - 1
    while k >= start and k >= end - 3:
        b = data[k]
        if b < 0x80:
            return end
        if b >= 0xC0:
            need = 2 if b < 0xE0 else 3 if b < 0xF0 else 4
            return k if end - k < need else end
        k -= 1
    return end


CON = Console()


def ver():
    print("gfx:", MODULE_VERSION, "(native)" if NATIVE else "(python)")
    return MODULE_VERSION


def help():
    print("-- gfx: drawing on the PicoCalc screen --")
    print("begin() / end()   take the screen / give it back")
    print("app()             clear it for an app (prints are drawn)")
    print("suspend()/resume() lend it to the firmware terminal (pye)")
    print("text(s, x, y, fg, bg)   pixel coordinates, 6x8 font")
    print("fill_rect, hline, clear, center, key_bar, title_bar")


def h():
    return help()
