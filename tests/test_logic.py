# Host-side checks for the pure logic (parsers, formatters, helpers).
# Run from the repo root with either interpreter:
#   python3 tests/test_logic.py
#   micropython tests/test_logic.py
# No hardware needed: machine/network/picocalc are stubbed where used.

import io
import os
import sys

try:
    _HERE = __file__.rsplit("/", 1)[0] if "/" in __file__ else "."
except NameError:  # mpremote run: executed on the device, modules in /
    _HERE = "."
sys.path.insert(0, _HERE + "/..")

import pico_utils as pu

_TMP = _HERE + "/_tmp_test.json"
_FAILS = []


def check(name, got, want):
    if got != want:
        _FAILS.append(name)
        print("FAIL", name, "\n  got: ", repr(got), "\n  want:", repr(want))


def _rm(path):
    for p in (path, path + ".tmp", path + ".bak"):
        try:
            os.remove(p)
        except OSError:
            pass


class FakeRequests:
    def __init__(self, reject_timeout=False):
        self.calls = []
        self.reject_timeout = reject_timeout

    def request(self, method, url, **kw):
        if self.reject_timeout and "timeout" in kw:
            raise TypeError("unexpected keyword argument 'timeout'")
        self.calls.append((method, url, kw))
        return "resp"


class FakeRaw:
    def __init__(self, data):
        self.f = io.BytesIO(data)

    def readinto(self, buf):
        return self.f.readinto(buf)


class FakeResponse:
    def __init__(self, data):
        self.raw = FakeRaw(data)


# ── pico_utils ──────────────────────────────────


def test_http_request_timeout():
    req = FakeRequests()
    pu.http_request(req, "GET", "http://x", headers={"a": "b"})
    check("timeout passed", req.calls[0][2].get("timeout"), pu.HTTP_TIMEOUT)
    old = FakeRequests(reject_timeout=True)
    check("old urequests fallback", pu.http_request(old, "GET", "http://x"), "resp")
    check("fallback drops timeout", "timeout" in old.calls[0][2], False)


def test_local_time_offset():
    saved = pu.CLOCK_CONFIG_FILE
    pu.CLOCK_CONFIG_FILE = _TMP
    try:
        _rm(_TMP)
        check("no config -> 0", pu.utc_offset_hours(), 0)
        pu.save_json(_TMP, {"utc_offset": 2})
        check("offset read", pu.utc_offset_hours(), 2)
        # builtin modules can't be monkeypatched on MicroPython: compare
        # the two clocks' time of day instead (tolerates a tick between calls)
        a = pu.local_time(0)
        b = pu.local_time()
        diff = ((b[3] * 3600 + b[4] * 60 + b[5]) - (a[3] * 3600 + a[4] * 60 + a[5])) % 86400
        check("local_time shifts by offset", diff in (7200, 7201), True)
    finally:
        pu.CLOCK_CONFIG_FILE = saved
        _rm(_TMP)


def test_http_request_user_agent():
    req = FakeRequests()
    pu.http_request(req, "GET", "http://x", headers={"X-Auth-Token": "t"})
    headers = req.calls[0][2]["headers"]
    check("ua added", headers.get("User-Agent"), pu.USER_AGENT)
    check("caller header kept", headers.get("X-Auth-Token"), "t")


# keys: feed raw bytes the way the PicoCalc driver emits them


class KeyScript:
    def __init__(self, data):
        self.data = list(data)

    def __call__(self, timeout_ms=None):
        if self.data:
            return self.data.pop(0)
        if timeout_ms is None:
            raise RuntimeError("script exhausted")
        return None


def keys_from(data):
    script = KeyScript(data)
    pu._key_byte = script
    return script


_REAL_KEY_BYTE = pu._key_byte


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
        pu._key_byte = _REAL_KEY_BYTE


def test_read_line_mask_and_edit():
    try:
        keys_from(b"ab\x7fc\r")
        check("backspace edits", pu.read_line("", mask="*"), "ac")
        keys_from(b"xy\x1b\x1b")
        check("esc cancels", pu.read_line(""), None)
    finally:
        pu._key_byte = _REAL_KEY_BYTE


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
        pu._key_byte = _REAL_KEY_BYTE


def test_paged_lines_stop():
    try:
        keys_from(b"q")
        check("q stops", pu.paged_lines([str(i) for i in range(20)], page_lines=5), 20)
        keys_from(b"  ")
        check("keys page on", pu.paged_lines([str(i) for i in range(12)], page_lines=5), 12)
    finally:
        pu._key_byte = _REAL_KEY_BYTE


# screen: the CP437 writer that replaces the firmware's vt.write()


class FakeVt:
    def __init__(self):
        self.drawn = []

    def wr(self, text):
        self.drawn.append(text)
        return len(text)


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


class EioVt(FakeVt):
    def readinto(self, buf):
        raise OSError(5)


def test_screen_term_survives_keyboard_eio():
    term = pu._ScreenTerm(EioVt())
    check("EIO read is no key", term.readinto(bytearray(1)), None)
    saved = pu._terminal
    try:
        eio = EioVt()
        pu._terminal = lambda: eio
        pu._STDIN_POLL = False
        check("poll survives EIO", pu._poll_byte(), None)
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


# ── rss_news ────────────────────────────────────

import rss_news


def test_read_body_cap_and_decode():
    body = rss_news._read_body(FakeResponse(b"x" * 100), 40)
    check("cap", len(body), 40)
    check("utf8 cut at cap", rss_news._read_body(FakeResponse("abcà".encode()), 4), "abc")
    check("latin-1 falls back", rss_news._read_body(FakeResponse(b"caf\xe9 ok"), 50), "caf? ok")
    check("short body", rss_news._read_body(FakeResponse(b"<rss/>"), 50), "<rss/>")


def test_clean_text():
    check(
        "escaped html stripped",
        rss_news._clean_text("&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;"),
        "Hello world",
    )
    check("bare < kept", rss_news._clean_text("a < b and c<d"), "a < b and c<d")
    check("escaped bare < kept", rss_news._clean_text("1 &lt; 2"), "1 < 2")
    check("cdata html", rss_news._clean_text("<![CDATA[<p>l&rsquo;Italia &hellip;</p>]]>"), "l'Italia ...")


def test_parse_feed():
    xml = (
        "<rss><channel><title>C</title>"
        "<item><title>One</title><description>&lt;p&gt;First&lt;/p&gt;</description>"
        "<link>http://a</link></item>"
        "<item><title><![CDATA[Two]]></title><description>Second</description></item>"
        "</channel></rss>"
    )
    items = rss_news._parse_feed(xml, 2)
    check("titles", [i["title"] for i in items], ["One", "Two"])
    check("summary", items[0]["summary"], "First")


def test_latest_pages_once():
    pages = []
    saved_config = rss_news.CONFIG_FILE
    rss_news.CONFIG_FILE = _TMP  # defaults, not a device's saved feeds
    _rm(_TMP)
    saved = (rss_news.check_wifi, rss_news._http_module, rss_news._fetch_feed, rss_news._paged_lines)
    rss_news.check_wifi = lambda: True
    rss_news._http_module = lambda: object()
    rss_news._fetch_feed = lambda name, url, per, req: [
        {"title": "T" + name, "summary": "S", "source": name, "link": "", "date": ""}
    ]
    rss_news._paged_lines = lambda lines, page_lines=8: pages.append(lines)
    try:
        count = rss_news.latest()
    finally:
        rss_news.check_wifi, rss_news._http_module, rss_news._fetch_feed, rss_news._paged_lines = saved
        rss_news.CONFIG_FILE = saved_config
        _rm(_TMP)
    check("one paged call", len(pages), 1)
    check("item count", count, len(rss_news.DEFAULT_FEEDS))
    check("first line", pages[0][0], "[1] BBC World")


# ── scientific_calc ─────────────────────────────

import scientific_calc as sc


def test_format_result():
    check("small int", sc._format_result(42), "42")
    check("integral float", sc._format_result(3.0), "3")
    check("2**100 fits 30", sc._format_result(2 ** 100, 30), "1.2676506002282294014967032e30")
    check("30! no cut digits", sc._format_result(sc._calc_factorial(30), 10), "2.65253e32")
    check("negative big", sc._format_result(-(10 ** 40), 12), "-1.000000e40")
    check("round up carries", sc._format_result(99999999999, 8), "1.000e11")
    check("inf", sc._format_result(float("inf")), "inf")
    check("-inf", sc._format_result(float("-inf")), "-inf")
    check("nan", sc._format_result(float("nan")), "nan")


# ── notes ───────────────────────────────────────

import notes


def test_note_stamp_needs_clock():
    saved = notes._clock_synced
    notes._clock_synced = lambda: False
    try:
        check("no stamp before sync", notes._ts(), "")
    finally:
        notes._clock_synced = saved


class ChunkRaw:
    # A response body handed out in small chunks, like a socket.
    def __init__(self, data, chunk=7):
        self.data = data
        self.chunk = chunk
        self.pos = 0

    def readinto(self, buf):
        piece = self.data[self.pos : self.pos + min(self.chunk, len(buf))]
        buf[: len(piece)] = piece
        self.pos += len(piece)
        return len(piece)


class LazyRaw:
    # head + n filler bytes + tail, made on demand: no big test allocation
    def __init__(self, head, fill, tail):
        self.data = head + tail  # for json() on short bodies only
        self.head, self.fill, self.tail = head, fill, tail
        self.pos = 0

    def readinto(self, buf):
        size = len(self.head) + self.fill + len(self.tail)
        n = min(len(buf), 4096, size - self.pos)
        for i in range(n):
            p = self.pos + i
            if p < len(self.head):
                buf[i] = self.head[p]
            elif p < len(self.head) + self.fill:
                buf[i] = 120
            else:
                buf[i] = self.tail[p - len(self.head) - self.fill]
        self.pos += n
        return n


class HttpResponse:
    def __init__(self, status, body=b"", chunk=64, raw=None):
        self.status_code = status
        self.raw = raw or ChunkRaw(body, chunk)
        self.closed = False

    def json(self):
        import json as _json

        return _json.loads(self.raw.data)

    def close(self):
        self.closed = True


class ScriptedRequests:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)


# ── openrouter_ai: streaming ────────────────────

import openrouter_ai as ai


def test_sse_stream():
    body = (
        b": OPENROUTER PROCESSING\n\n"
        b'data: {"choices":[{"delta":{"content":"Ciao"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":" mondo"}}]}\n\n'
        b'data: {"choices":[{"delta":{}}]}\n\n'
        b"data: [DONE]\n\n"
        b'data: {"choices":[{"delta":{"content":"late"}}]}\n\n'
    )
    got = []
    text = ai._sse_text(ChunkRaw(body, 5), got.append)
    check("sse text", text, "Ciao mondo")
    check("sse emitted", "".join(got), "Ciao mondo")
    err = b'data: {"error":{"message":"quota"}}\n\n'
    check("sse error stops", ai._sse_text(ChunkRaw(err), lambda t: None), "")


def test_stream_printer_wraps():
    out = []
    printer = ai._StreamPrinter(width=10, out=out.append)
    printer("one two thr")
    printer("ee four\nfive")
    printer.close()
    check("wrapped lines", "".join(out), "one two\nthree four\nfive\n")


# ── synthesizer / calc ──────────────────────────

import synthesizer as synth


def test_rtttl_parse():
    notes = synth._rtttl_parse("t:d=4,o=5,b=120:8e6,p,4c#.5,a")
    check("count", len(notes), 4)
    check("e6 eighth", (int(notes[0][0]), notes[0][1]), (1318, 250))
    check("rest", notes[1], (0, 500))
    check("c#5 dotted quarter", (int(notes[2][0]), notes[2][1]), (554, 750))
    check("a5 default octave", int(notes[3][0]), 880)
    check("bad song", synth._rtttl_parse("nope"), None)


def test_calc_ans_chaining():
    saved = sc._LAST
    try:
        sc._LAST = 10
        check("+ chains", sc._prepare_expr("+5"), "ans+5")
        check("* chains", sc._prepare_expr("*2"), "ans*2")
        check("- stays negative", sc._prepare_expr("-5"), "-5")
        sc._LAST = None
        check("no ans yet", sc._prepare_expr("+5"), "+5")
    finally:
        sc._LAST = saved


# ── rss_news: Miniflux ──────────────────────────

_MF_BODY = (
    b'{"total": 7, "entries": [{"id": 11, "title": "Uno &amp; due",'
    b' "url": "https://a", "content": "<p>Testo <b>forte</b></p>",'
    b' "published_at": "2026-10-04T08:15:00Z", "feed": {"title": "Blog"}}]}'
)


def _with_miniflux(fn):
    saved = (rss_news.MF_CONFIG_FILE, rss_news.check_wifi, rss_news._http_module, rss_news._LAST_ITEMS)
    rss_news.MF_CONFIG_FILE = _TMP
    rss_news.check_wifi = lambda: True
    try:
        pu.save_json(_TMP, {"url": "https://feed.example", "token": "k"})
        fn()
    finally:
        (rss_news.MF_CONFIG_FILE, rss_news.check_wifi, rss_news._http_module, rss_news._LAST_ITEMS) = saved
        _rm(_TMP)


def test_miniflux_fetch_and_mark():
    def run():
        req = ScriptedRequests([HttpResponse(200, _MF_BODY), HttpResponse(204)])
        rss_news._http_module = lambda: req
        check("one entry", rss_news.mf(), 1)
        item = rss_news._LAST_ITEMS[0]
        check("title cleaned", item["title"], "Uno & due")
        check("content cleaned", item["summary"], "Testo forte")
        check("source", item["source"], "Blog")
        check("date", item["date"], "2026-10-04 08:15")
        method, url, kw = req.calls[0]
        check("auth header", kw["headers"]["X-Auth-Token"], "k")
        check("unread query", "status=unread" in url and "limit=3" in url, True)
        check("mark read", rss_news.mf_done(), True)
        method, url, kw = req.calls[1]
        check("PUT entries", (method, url), ("PUT", "https://feed.example/v1/entries"))
        check("ids sent", rss_news.json.loads(kw["data"]), {"entry_ids": [11], "status": "read"})
        check("not sent twice", rss_news.mf_done(), False)

    _with_miniflux(run)


def test_miniflux_large_and_401():
    def run():
        def big():
            return HttpResponse(200, raw=LazyRaw(b'{"entries": [', rss_news.MF_MAX_BYTES + 10, b"]}"))

        req = ScriptedRequests([big(), HttpResponse(200, _MF_BODY)])
        rss_news._http_module = lambda: req
        check("falls back to 1", rss_news.mf(), 1)
        check("retry used limit=1", "limit=1" in req.calls[1][1], True)
        req = ScriptedRequests([HttpResponse(401, b"{}")])
        rss_news._http_module = lambda: req
        check("401 stops", rss_news.mf(), 0)
        check("no retry on 401", len(req.calls), 1)
        rss_news._LAST_ITEMS = [{"title": "old RSS item"}]
        req = ScriptedRequests([big(), big()])
        rss_news._http_module = lambda: req
        check("too large", rss_news.mf(), 0)
        check("no stale RSS items left", rss_news._LAST_ITEMS, [])

    _with_miniflux(run)


# ── wifi_manager: saved / forget ────────────────

import wifi_manager


def test_wifi_saved_forget():
    saved = wifi_manager.CREDENTIALS_FILE
    wifi_manager.CREDENTIALS_FILE = _TMP
    try:
        wifi_manager.save_credentials({"home": "p1", "office": "p2"})
        check("saved sorted", wifi_manager.saved(), ["home", "office"])
        check("forget by index", wifi_manager.forget(2), True)
        check("left", wifi_manager.saved(), ["home"])
        check("forget by name", wifi_manager.forget("home"), True)
        check("unknown", wifi_manager.forget("nope"), False)
    finally:
        wifi_manager.CREDENTIALS_FILE = saved
        _rm(_TMP)


def test_menu_imports():
    import menu

    check("menu apps", len(menu._APPS), 9)


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        try:
            fn()
        except Exception as error:
            _FAILS.append(name)
            print("ERROR", name, repr(error))
    print("{} tests, {} failed".format(len(tests), len(_FAILS)))
    if _FAILS:
        sys.exit(1)


main()
