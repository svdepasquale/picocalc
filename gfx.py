"""Draw straight into the PicoCalc framebuffer, for screens that are drawn
rather than printed (the launcher). A full screen of text takes ~18 ms here
against ~220 ms through the terminal (measured 2026-10-06)."""

import sys

from pico_utils import _terminal as _pu_terminal
from pico_utils import clear_screen as _clear_screen, status_text as _status_text
from pico_utils import to_cp437 as _to_cp437
from pico_utils import BLACK, BLUE, BWHITE, BYELLOW, WHITE


MODULE_VERSION = "2026-10-06.3"

WIDTH = 320
HEIGHT = 320
CHAR_W = 6  # 6x8 font: text() advances 6 pixels and draws 5 of them
CHAR_H = 8
COLUMNS = WIDTH // CHAR_W
TITLE_H = 12

# Every call costs: ~11 us a Python call, ~15-20 us a builtin, 103 us str()
# of a str (measured 2026-10-06). Hence the display kept from begin() to
# end() and text passed as bytes where it is drawn often (cp() once).
_FB = [None]


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


def begin():
    """Take the screen from the terminal; paint all of it next. Drawing works
    from here to end(), and nothing may print in between."""
    # The cursor blinks from a 250 ms timer interrupt that draws over whatever
    # is at its cell, and hiding it repaints the terminal's own (stale) cell
    # there: hence the full paint. Home: a traceback printed from the last row
    # would scroll past the framebuffer (pico_utils._set_margins).
    _FB[0] = surface()
    term = _terminal()
    if term is not None and hasattr(term, "wr"):
        term.wr("\x1b[?25l\x1b[H")


def end():
    """Give the screen back to the terminal: cleared, cursor on."""
    fb = _FB[0]
    _FB[0] = None
    if fb is not None:
        # The terminal clears its 53 columns: 318 of the 320 pixels.
        fb.fill_rect(0, 0, WIDTH, HEIGHT, BLACK)
    _clear_screen()
    term = _terminal()
    if term is not None and hasattr(term, "wr"):
        term.wr("\x1b[?25h")


# ── drawing ─────────────────────────────────────


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
    # 0x1B is an escape for the terminal (pico_utils folds ← to <), a glyph here
    return bytes(map(ord, _to_cp437(text.replace("←", "\x1b"))))


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


def text(s, x, y, fg=WHITE, bg=None):
    """Draw s with its top left at pixel x, y; returns the x after it. The font
    draws glyph pixels only: bg fills the text's cells first."""
    if type(s) is not bytes:
        s = cp(s)
    fb = _FB[0]
    if fb is not None:
        if bg is not None:
            fb.fill_rect(x, y, len(s) * CHAR_W, CHAR_H, bg)
        fb.text(s, x, y, fg)
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


def ver():
    print("gfx:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- gfx: drawing on the PicoCalc screen --")
    print("begin() / end()   take the screen / give it back")
    print("text(s, x, y, fg, bg)   pixel coordinates, 6x8 font")
    print("fill_rect, hline, clear, center, key_bar, title_bar")


def h():
    return help()
