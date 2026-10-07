try:
    from harness import *  # noqa
except ImportError:  # on the device tools/test_device.sh puts harness.py in front
    pass

# Host checks for pico_utils, gfx and its screen console, the launcher menu and go.
# Run from the repo root with either interpreter:
#   python3 tests/test_core.py
#   micropython tests/test_core.py


# ── pico_utils ──────────────────────────────────


def test_local_time_offset():
    saved = pu.CLOCK_CONFIG_FILE
    pu.CLOCK_CONFIG_FILE = TMP
    try:
        rm_tmp(TMP)
        check("no config -> 0", pu.utc_offset_hours(), 0)
        pu.save_json(TMP, {"utc_offset": 2})
        check("offset read", pu.utc_offset_hours(), 2)
        # builtin modules can't be monkeypatched on MicroPython: compare
        # the two clocks' time of day instead (tolerates a tick between calls)
        a = pu.local_time(0)
        b = pu.local_time()
        diff = ((b[3] * 3600 + b[4] * 60 + b[5]) - (a[3] * 3600 + a[4] * 60 + a[5])) % 86400
        check("local_time shifts by offset", diff in (7200, 7201), True)
    finally:
        pu.CLOCK_CONFIG_FILE = saved
        rm_tmp(TMP)


def test_wrap_text():
    code = "def f(x):\n    if x:\n        return 1"
    check("code keeps its indentation", pu.wrap_text(code, 32), ["def f(x):", "    if x:", "        return 1"])
    check("indent on the first line only", pu.wrap_text("  - one two three", 10), ["  - one", "two three"])
    check("indent at most half", pu.wrap_text(" " * 12 + "x", 10), ["     x"])
    check("indented long word", pu.wrap_text("  abcdefghijkl", 10), ["  abcdefgh", "ijkl"])
    # as before
    check("words wrap", pu.wrap_text("one two three four", 9), ["one two", "three", "four"])
    check("gaps collapse", pu.wrap_text("a  b   c ", 10), ["a b c"])
    check("tab is no indent", pu.wrap_text("\tx", 10), ["x"])
    check("blank lines kept", pu.wrap_text("a\r\n\n  \nb", 10), ["a", "", "", "b"])
    check("long word split", pu.wrap_text("x" * 25, 10), ["x" * 10, "x" * 10, "x" * 5])


# keys: feed raw bytes the way the PicoCalc driver emits them


def test_decode_keys():
    cases = [
        (b"a", "a"),
        (b"\r", "enter"),
        (b"\x7f", "backspace"),
        (b"\x1b\x1b", "esc"),
        (b"\x1b", "esc"),
        (b"\x1b[A", "up"),
        (b"\x1b[B", "down"),
        (b"\x1b[C", "right"),
        (b"\x1b[D", "left"),
        (b"\x1b[1;2A", "up"),
        (b"\x1b[H", "home"),
        (b"\x1b[F", "end"),
        (b"\x1b[3~", "del"),
        (b"\x1bOC", "right"),
        (b"\x1bx", "x"),
        (b"\x04", "eof"),
    ]
    try:
        for raw, want in cases:
            keys_from(raw)
            check("key " + repr(raw), pu.read_key(), want)
        keys_from(b"\x03")
        try:
            pu.read_key()
            check("ctrl-c raises", False, True)
        except KeyboardInterrupt:
            pass
    finally:
        pu._key_byte = REAL_KEY_BYTE


def test_read_line_mask_and_edit():
    try:
        keys_from(b"ab\x7fc\r")
        check("backspace edits", pu.read_line("", mask="*"), "ac")
        keys_from(b"xy\x1b\x1b")
        check("esc cancels", pu.read_line(""), None)
    finally:
        pu._key_byte = REAL_KEY_BYTE


def test_browse_items_keys():
    items = ["a", "b", "c", "d"]
    try:
        keys_from(b"nn\x1b[Dq")
        check("n n left q", pu.browse_items("T", items), "b")
        keys_from(b"3\rq")
        check("jump to 3", pu.browse_items("T", items), "c")
        keys_from(b"\x1b\x1b")
        check("esc quits", pu.browse_items("T", items, 4), "d")
        seen = []
        keys_from(b"xq")
        pu.browse_items("T", items, 2, on_key=lambda k, it, pos: seen.append((k, it, pos)))
        check("on_key extra", seen, [("x", "b", 1)])
    finally:
        pu._key_byte = REAL_KEY_BYTE


def test_paged_lines_stop():
    try:
        keys_from(b"q")
        check("q stops", pu.paged_lines([str(i) for i in range(20)], page_lines=5), 20)
        keys_from(b"  ")
        check("keys page on", pu.paged_lines([str(i) for i in range(12)], page_lines=5), 12)
    finally:
        pu._key_byte = REAL_KEY_BYTE


class FakePoll:
    def __init__(self, ready):
        self.ready = ready

    def poll(self, timeout=0):
        return [1] if self.ready else []


class IdleVt(FakeVt):
    def readinto(self, buf):
        return 0


def test_host_takeover():
    # USB input while the PicoCalc keyboard is the key source is a host tool
    # asking for the REPL: it must not be read as keys.
    saved = pu._terminal
    try:
        idle = IdleVt()
        pu._terminal = lambda: idle
        pu._STDIN_POLL = FakePoll(False)
        check("nothing pending", pu._poll_byte(), None)
        pu._STDIN_POLL = FakePoll(True)
        try:
            pu._poll_byte()
            check("usb input hands over", False, True)
        except pu.HostTakeover:
            pass
        check("not an Exception", issubclass(pu.HostTakeover, Exception), False)
        check("not a Ctrl+C", issubclass(pu.HostTakeover, KeyboardInterrupt), False)
    finally:
        pu._terminal = saved
        pu._STDIN_POLL = None


# lists


def test_pick_keys():
    try:
        keys_from(b"\x1b[B\x1b[Bx")
        check("down down x", pu.pick("T", ["a", "b", "c"]), ("x", 2))
        keys_from(b"2")
        check("digit picks", pu.pick("T", ["a", "b", "c"]), ("enter", 1))
        keys_from(b"9q")
        check("digit past end ignored", pu.pick("T", ["a", "b"]), ("q", 0))
        keys_from(b"\x1b[Ak\x1b[Bn")
        check("clamped at top", pu.pick("T", ["a", "b"]), ("n", 1))
        keys_from(b"n")
        check("empty list", pu.pick("T", []), ("n", None))
        keys_from(b"j" * 40 + b"x")
        check("scrolls", pu.pick("T", [str(i) for i in range(50)]), ("x", 40))
        keys_from(b"jq")
        rows = ((("a", "b"),), (("c", "d"),))
        check("two footer rows", pu.pick("T", ["a", "b"], footer=rows, pos=1), ("q", 1))
        keys_from(b"\x1b[B\r")
        check("select_list enter", pu.select_list("T", ["a", "b"]), 1)
        keys_from(b"zj\r")
        check("select_list other key", pu.select_list("T", ["a", "b"]), 1)
        keys_from(b"\x1b\x1b")
        check("select_list esc", pu.select_list("T", ["a", "b"]), None)
    finally:
        pu._key_byte = REAL_KEY_BYTE


# screen: the CP437 writer that replaces the firmware's vt.write()


def test_screen_term_bytes_and_cp437():
    vt = FakeVt()
    term = pu._ScreenTerm(vt)
    raw = "città è ’ok…".encode()
    check("returns bytes, not chars", term.write(bytearray(raw)), len(raw))
    check("cp437 glyph codes", vt.drawn[-1], "citt\x85 \x8a 'ok...")
    check("bad utf-8 never raises", term.write(bytearray(b"a\xa0b")), 3)
    check("bad byte shown as ?", vt.drawn[-1], "a?b")
    check("ascii untouched", pu.to_cp437("plain"), "plain")
    check("upper accent folded", pu.to_cp437("È €"), "E EUR")
    check("left arrow folds, right maps", pu.to_cp437("←→"), "<\x1a")


def _cp437_one_by_one(text):
    out = []
    for ch in text:
        if ord(ch) < 128:
            out.append(ch)
            continue
        idx = pu._CP437_SRC.find(ch)
        out.append(pu._CP437_DST[idx] if idx >= 0 else pu._FOLDS.get(ch, "?"))
    return "".join(out)


def test_cp437_replace_matches_loop():
    samples = [
        "città è ’ok…",
        "\x1b[38;5;8m─────\x1b[0m ≈ » ¶ ° ≡ ± ∩ Θ ■ ▬ ► §",
        "Perché più già",
        "á\xa0é",  # á maps to 0xa0, a real NBSP must not take its glyph
        "€ … — “quoted” ✓",
    ]
    for text in samples:
        check("cp437 " + repr(text[:12]), pu.to_cp437(text), _cp437_one_by_one(text))


class EioVt(FakeVt):
    def readinto(self, buf):
        raise OSError(5)


class CaptureBugVt(FakeVt):
    # The firmware's Ctrl+U screenshot raised AttributeError from readinto().
    def readinto(self, buf):
        raise AttributeError("'PicoDisplay' object has no attribute 'buffer'")


def test_screen_term_survives_keyboard_eio():
    term = pu._ScreenTerm(EioVt())
    check("EIO read is no key", term.readinto(bytearray(1)), None)
    check("firmware bug is no key", pu._ScreenTerm(CaptureBugVt()).readinto(bytearray(1)), None)
    saved = pu._terminal
    try:
        eio = EioVt()
        pu._terminal = lambda: eio
        pu._STDIN_POLL = False
        check("poll survives EIO", pu._poll_byte(), None)
        bug = CaptureBugVt()
        pu._terminal = lambda: bug
        check("poll survives a firmware bug", pu._poll_byte(), None)
    finally:
        pu._terminal = saved
        pu._STDIN_POLL = None


def test_screen_term_print_path():
    # MicroPython's print(file=...) goes through mp_stream_write, the same
    # retry-on-short-write loop dupterm uses; CPython hands write() a str.
    if sys.implementation.name != "micropython":
        return
    vt = FakeVt()
    term = pu._ScreenTerm(vt)
    print("perché", file=term)
    check("one write per chunk", vt.drawn[0], "perch\x82")
    check("no tail re-send", len(vt.drawn), 2)  # text, then "\n"


class FakeOs:
    def __init__(self, current):
        self.slot = current

    def dupterm(self, stream):
        prev, self.slot = self.slot, stream
        return prev


def test_install_screen():
    saved = (pu.os, pu._terminal)
    try:
        vt = FakeVt()
        pu._terminal = lambda: vt
        pu.os = FakeOs(vt)
        check("wraps the PicoCalc terminal", pu._install_screen(), True)
        wrapper = pu.os.slot
        check("wrapper attached", getattr(wrapper, "_cp437_screen", False), True)
        check("already wrapped stays", pu._install_screen(), True)
        check("no double wrap", pu.os.slot is wrapper, True)
        newer = FakeVt()  # boot.py ran again: new terminal, stale wrapper
        pu._terminal = lambda: newer
        check("stale wrapper replaced", pu._install_screen(), True)
        check("wraps the new terminal", pu.os.slot._term is newer, True)
        pu._terminal = lambda: vt
        pu.os = FakeOs(None)
        check("re-attaches a detached screen", pu._install_screen(), True)
        check("wrapper in the empty slot", getattr(pu.os.slot, "_cp437_screen", False), True)
        other = object()
        pu.os = FakeOs(other)
        check("foreign terminal left alone", pu._install_screen(), False)
        check("foreign restored", pu.os.slot is other, True)
    finally:
        pu.os, pu._terminal = saved


# time


def test_weekday_and_eu_dst():
    check("2026-10-04 is Sunday", pu._weekday(2026, 10, 4), 6)
    check("2026-03-29 is Sunday", pu._weekday(2026, 3, 29), 6)
    cases = [
        ((2026, 1, 15, 12), False),
        ((2026, 3, 29, 0), False),
        ((2026, 3, 29, 1), True),
        ((2026, 7, 1, 0), True),
        ((2026, 10, 25, 0), True),
        ((2026, 10, 25, 1), False),
        ((2026, 12, 1, 0), False),
        ((2027, 3, 28, 1), True),
        ((2027, 10, 31, 0), True),
    ]
    for t, want in cases:
        check("dst " + repr(t), pu.eu_dst_active(t), want)


# ── menu, go ────────────────────────────────────


def test_menu_imports():
    import menu

    check("menu apps", len(menu._APPS), 13)
    check("one key each", len(menu._KEY_INDEX), len(menu._APPS))
    check("new keys", [menu._KEY_INDEX[k] for k in ("0", "m", "a", "s")], [9, 10, 11, 12])
    check("every app described", sorted(menu._ABOUT), sorted(app[0] for app in menu._APPS))


def test_go_run_or_imported():
    # Executed rather than imported (mpremote run go.py, r on /go.py in
    # Files), go was never in sys.modules: dropping itself must not raise.
    class FakeMenu:
        runs = 0

        def run(self):
            FakeMenu.runs += 1

    saved = sys.modules.get("menu")
    sys.modules["menu"] = FakeMenu()
    root = pu.__file__.rsplit("/", 1)[0] if "/" in pu.__file__ else "."  # where the toolkit is
    try:
        sys.modules.pop("go", None)
        with open(root + "/go.py") as f:
            exec(f.read(), {"__name__": "__main__"})
        check("run as a script", FakeMenu.runs, 1)
        if sys.implementation.name == "micropython":  # CPython's import wants it kept
            import go  # noqa: F401

            check("import go opens it", FakeMenu.runs, 2)
            check("and drops itself", "go" in sys.modules, False)
    finally:
        if saved is None:
            sys.modules.pop("menu", None)
        else:
            sys.modules["menu"] = saved


# ── gfx ─────────────────────────────────────────

import gfx


def test_gfx_font_codes():
    check("ascii as bytes", gfx.cp("plain"), b"plain")
    check("accents as cp437 bytes", gfx.cp("città"), b"citt\x85")
    check("left arrow is a glyph here", gfx.cp("←→"), b"\x1b\x1a")
    check("bytes pass", gfx.cp(b"\x10"), b"\x10")
    check("numbers drawn", gfx.cp(42), b"42")


def test_gfx_text_and_mode():
    def body(fake, vt):
        gfx.text("before", 0, 0)
        check("no drawing before begin()", fake.calls, [])
        gfx.begin()
        check("cursor off and home", vt.drawn[-1], "\x1b[?25l\x1b[H")
        check("x after the text", gfx.text("ab", 6, 8, 7, bg=4), 18)
        check("background first", fake.calls[0], ("fill_rect", 6, 8, 12, 8, 4))
        check("then the glyphs", fake.calls[1], ("text", b"ab", 6, 8, 7))
        del fake.calls[:]
        gfx.title_bar("T")
        check("title bar", fake.calls[0], ("fill_rect", 0, 0, 320, gfx.TITLE_H, pu.BLUE))
        check("status on the right", fake.calls[-1], ("text", b"12:34  WiFi", 320 - 12 * 6, 2, pu.BWHITE))
        del fake.calls[:]
        gfx.end()
        check("all 320 columns blanked", fake.calls, [("fill_rect", 0, 0, 320, 320, pu.BLACK)])
        check("cleared, cursor on", vt.drawn[-2:], ["<clear>", "\x1b[?25h"])
        del fake.calls[:]
        gfx.text("after", 0, 0)
        check("no drawing after end()", fake.calls, [])

    with_display(body)


def test_menu_draw_and_move():
    import menu

    def body(fake, vt):
        hints = [""] * len(menu._APPS)
        hints[1] = "città"
        menu._draw(1, hints)
        check("screen taken first", vt.drawn[0], "\x1b[?25l\x1b[H")
        check("whole screen painted", fake.calls[0], ("fill_rect", 0, 0, 320, 320, pu.BLACK))
        bands = [c[5] for c in fake.calls if c[0] == "fill_rect" and c[4] == menu._ROW_H]
        check("one band per app", len(bands), len(menu._APPS))
        check("selected band", bands[1], pu.BCYAN)
        check("the others black", [b for i, b in enumerate(bands) if i != 1], [pu.BLACK] * (len(bands) - 1))
        hint = ("text", b"citt\x85", 6 * 20, menu._LIST_Y + menu._ROW_H + 1, pu.BLACK)
        check("hint in font codes", hint in fake.calls, True)
        check("nothing past the screen", [c for c in fake.calls if c[0] == "text" and c[3] > 312], [])
        del fake.calls[:]
        menu._move(1, 2, hints)
        bands = [c[5] for c in fake.calls if c[0] == "fill_rect" and c[4] == menu._ROW_H]
        check("move repaints two rows", bands, [pu.BLACK, pu.BCYAN])
        gfx.surface = lambda: None
        menu._draw(0, hints)  # away from the PicoCalc: draws nothing, no error

    with_display(body)


def _console(body):
    # gfx's console drawing on a recording display, as when the launcher has
    # started an app; writes go through pico_utils' screen writer, like print.
    def run(fake, vt):
        gfx.begin()
        gfx.app()
        del fake.calls[:]
        before = list(vt.drawn)
        body(fake, pu._ScreenTerm(vt).write)
        check("nothing reached the terminal", vt.drawn, before)

    with_display(run)


def test_console_text_and_colours():
    def body(fake, write):
        write(b"Hi")
        check("row 1 band from the edge", fake.calls[0], ("fill_rect", 0, 0, 13, 12, pu.BLACK))
        check("row 1 text", fake.calls[1], ("text", b"Hi", 1, 2, pu.WHITE))
        del fake.calls[:]
        write(b"\x1b[3;5H\x1b[38;5;14mok\x1b[0m")
        check("row 3 col 5 band", fake.calls[0], ("fill_rect", 25, 24, 12, 10, pu.BLACK))
        check("coloured text", fake.calls[1], ("text", b"ok", 25, 25, 14))
        del fake.calls[:]
        write(b"\x1b[H" + pu.title_bar("T", status=False).encode())
        check("title bar spans the screen", fake.calls[0], ("fill_rect", 0, 0, 320, 12, pu.BLUE))
        del fake.calls[:]
        write(b"\x1b[7mR\x1b[27m")
        check("reverse video", fake.calls[0][5], pu.WHITE)

    _console(body)


def test_console_wrap_newline_erase():
    def body(fake, write):
        con = gfx.CON
        write(b"\x1b[2;1H" + b"a" * 60)
        check("wrapped to row 3", (con.row, con.col), (3, 8))
        write(b"\x1b[4;1H" + b"b" * 53 + b"\r\n")
        check("a full row then CR LF: one line down", (con.row, con.col), (5, 1))
        write(b"abc\b\x1b[K")
        check("backspace then erase to the end", fake.calls[-1], ("fill_rect", 13, 44, 307, 10, pu.BLACK))
        write(b"\x1b[5D")
        check("moving back past column 1 goes up a row", (con.row, con.col), (4, 51))
        write(b"\x1b[2;3H\x1b[9D")
        check("never into the title row", (con.row, con.col), (2, 1))
        write(b"\x1b[?25l")
        check("?25l hides the console cursor", con.cursor, False)
        write(b"\x1b[?25h")
        check("?25h shows it", con.cursor, True)
        write(b"\x1b[2J")
        check("clear screen", fake.calls[-1], ("fill_rect", 0, 0, 320, 320, pu.BLACK))

    _console(body)


def test_console_split_writes():
    def body(fake, write):
        write(b"\x1b[3")
        write(b"8;5;2mX")
        check("escape split across writes", fake.calls[-1], ("text", b"X", 1, 2, 2))
        write("è".encode()[:1])
        write("è".encode()[1:] + b"!")
        texts = [c for c in fake.calls if c[0] in ("text", "blit")]
        check("utf-8 split across writes", texts[-1], ("text", b"\x8a!", 7, 2, 2))
        write(b"\x1b[38;5;1")  # the inline colour path, split
        write(b"4mY")
        check("colour code split across writes", fake.calls[-1], ("text", b"Y", 19, 2, 14))
        write(b"\x1b[mZ")
        check("ESC [ m resets", fake.calls[-1], ("text", b"Z", 25, 2, pu.WHITE))

    _console(body)


def test_console_specials_and_high_glyphs():
    def body(fake, write):
        write(pu.paint("\u2500" * pu.DISPLAY_WIDTH, pu.GREY).encode())
        lines = [c for c in fake.calls if c[0] == "hline"]
        if pu.DISPLAY_WIDTH == 52:  # PicoCalc width: the line spans the screen
            check("separator edge to edge", lines, [("hline", 0, 5, 320, pu.GREY)])
        del fake.calls[:]
        write(b"\r\n" + pu.bar(0.3, 10).encode())
        rects = [c for c in fake.calls if c[0] == "fill_rect" and c[4] == 8]
        check("bar as two rectangles", [(r[3], r[5]) for r in rects], [(18, pu.GREEN), (42, pu.GREY)])
        del fake.calls[:]
        write("\r\nò".encode())
        drawn = [c for c in fake.calls if c[0] in ("blit", "text")]
        if gfx._framebuf is not None:
            check("ò from the realigned table", drawn, [("blit", 1, 25)])
        else:
            check("ò as its code", drawn, [("text", b"\x95", 1, 25, pu.WHITE)])

    _console(body)


def test_console_scroll_and_cursor():
    def body(fake, write):
        con = gfx.CON
        write(b"\x1b[31;1H\n")
        check("still the last row", con.row, pu.CON_ROWS)
        moves = [c for c in fake.calls if c[0] == "move_up"]
        check("rows 3-31 up to 2-30, title kept", moves, [("move_up", 14 * 160, 24 * 160, 29 * 1600)])
        check("last row cleared", fake.calls[-1], ("fill_rect", 0, 304, 320, 16, pu.BLACK))
        del fake.calls[:]
        con.waiting()
        check("cursor under the cell", fake.calls[-1], ("fill_rect", 1, 313, 5, 2, pu.BWHITE))
        write(b"x")
        check("cursor erased first, each line its own colour", fake.calls[1:3], [("hline", 1, 313, 5, 0), ("hline", 1, 314, 5, 0)])

    _console(body)


def test_gfx_native_matches_python():
    # On the device gfx uses viper; the Python fallbacks must agree with it.
    plain = "Città ─ █░ ò ù · ≈ ←→ “ok” ✓ x"
    src = plain.encode()
    out = bytearray(len(src) + 8)
    res = gfx._to_codes(src, out, gfx._TABLES, gfx._NKEYS)
    codes = bytes(out[: res & 0xFFFF])
    check("codes", codes, b"Citt\x85 \xc4 \xdb\xb0 \x95 \x97 \xfa \xf7 \x1b\x1a \"ok\" ? x")
    check("same as Python", codes, gfx._codes_py(plain))
    check("flags", res >> 16, gfx.SPECIAL | gfx.HIGH)
    res = gfx._to_codes("… 5€".encode(), out, gfx._TABLES, gfx._NKEYS)
    if gfx.NATIVE:  # the console converts these in Python
        check("several-character folds flagged", bool(res >> 16 & gfx.SLOW), True)
    else:
        check("folds in Python", bytes(out[: res & 0xFFFF]), b"... 5EUR")
    check("kinds", gfx._kinds_of(b"a\xc4\x95", 0, 3, gfx._KINDS), gfx.SPECIAL | gfx.HIGH)
    scans = (gfx._scan(b"ab\x1bc", 0, 4), gfx._scan(b"abc", 0, 3), gfx._scan("à\x1b".encode(), 0, 3))
    check("scan: control index, non-ASCII bit", [(r >> 1, r & 1) for r in scans], [(2, 0), (3, 0), (2, 1)])
    buf = bytearray(range(64))
    gfx._move_up(buf, 8, 24, 32)
    check("move up", bytes(buf[8:40]), bytes(range(24, 56)))


def test_block_rows():
    def body(fake, write):
        pu.block_rows(["\u2588\u2588  \u2588\u2588", "  \u2588\u2588  "], 5, 3, pu.BCYAN)
        rects = [c[1:] for c in fake.calls if c[0] == "fill_rect" and c[5] == pu.BCYAN]
        check("solid blocks, full row height", rects, [(13, 44, 12, 10, 14), (37, 44, 12, 10, 14), (25, 54, 12, 10, 14)])

    _console(body)
    if sys.implementation.name != "micropython":  # sys.stdout is fixed there
        printed = io.StringIO()
        real = sys.stdout
        sys.stdout = printed
        try:
            pu.block_rows(["\u2588 \u2588"], 5, 3, pu.BCYAN)
        finally:
            sys.stdout = real
        check("printed on the terminal", "\x1b[5;1H\x1b[K  " in printed.getvalue(), True)


def test_console_fits_pick_and_pager():
    # pick() repaints rows at fixed positions: its list, separator and two
    # footer rows must fit the console without scrolling; pagers likewise.
    rows = pu.CON_ROWS - 6 - 4  # PAGE_LINES on the PicoCalc, minus pick's margin
    check("pick fits", pu._PICK_TOP + rows + 1 + 2 <= pu.CON_ROWS, True)
    check("a page plus header and prompt fits", 2 + (pu.CON_ROWS - 6) + 1 <= pu.CON_ROWS, True)


def test_console_backspace_and_edge():
    def body(fake, write):
        con = gfx.CON
        write(b"\x1b[3;1H" + b"x" * pu.CON_COLS)
        check("after the last column: a wrap is due", (con.row, con.col), (3, pu.CON_COLS + 1))
        write(b"\b")
        check("backspace from there lands on the last column", con.col, pu.CON_COLS)
        del fake.calls[:]
        write(b"\x1b[4;1H" + b"y" * (gfx._WIDE + 3))
        check("a black run stops at its last cell", fake.calls[0][3], 1 + (gfx._WIDE + 3) * 6)
        write(b"\x1b[3;1H\x1b[41m" + b"z" * gfx._WIDE + b"\x1b[0m")
        bars = [c for c in fake.calls if c[0] == "fill_rect" and c[5] == 1]
        check("a coloured run spans the screen", (bars[-1][1], bars[-1][3]), (0, 320))

    _console(body)


def test_screen_writer_keeps_ctrl_c():
    vt = FakeVt()
    term = pu._ScreenTerm(vt)
    try:
        pu._CONSOLE[0] = None
        check("no poll support, no error", term.ioctl(3, 1), 0)
        con = gfx.Console()
        gfx._FB[0] = FakeDisplay()
        pu._CONSOLE[0] = con

        def stop(buf):
            raise KeyboardInterrupt

        con.write = stop  # a Ctrl+C from USB landing inside the console
        check("the writer still reports bytes", term.write(b"ab"), 2)
        check("kept", pu._INTERRUPTED[0], True)
        buf = bytearray(1)
        check("handed back to input() as the byte", (term.readinto(buf), buf[0]), (1, 3))
        pu._INTERRUPTED[0] = True
        raised = False
        try:
            pu._poll_byte()
        except KeyboardInterrupt:
            raised = True
        check("or raised at the next key read", (raised, pu._INTERRUPTED[0]), (True, False))
    finally:
        pu._CONSOLE[0] = None
        pu._INTERRUPTED[0] = False
        gfx._FB[0] = None


def test_wrap_counts_three_for_folds():
    check("… and € counted as drawn", pu.wrap_text("ab… 5€", 40), ["ab... 5EUR"])


def test_resume_after_end_stays_off():
    def body(fake, vt):
        gfx.begin()
        gfx.end()
        gfx.resume()
        check("no console without a screen", pu._CONSOLE[0], None)

    with_display(body)


def test_console_failure_falls_back():
    class Broken:
        beam = None

        def draw(self, buf):
            return False  # what Console.draw() answers when write() raised

        def waiting(self):
            raise ValueError("bug")

    vt = FakeVt()
    try:
        pu._CONSOLE[0] = Broken()
        term = pu._ScreenTerm(vt)
        check("still reports bytes", term.write(b"ok"), 2)
        check("drawn by the terminal", vt.drawn[-1], "ok")
    finally:
        pu._CONSOLE[0] = None


run(globals())
