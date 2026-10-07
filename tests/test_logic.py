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


def test_net_error_words():
    room = pu.DISPLAY_WIDTH - 6  # after "Err: " on one line
    eof = "SSL - The connection indicated an EOF"
    cases = [
        ("dns", OSError(-2), "DNS failed"),
        ("timeout", OSError(110), "timed out"),
        ("reset", OSError(104), "connection reset"),
        ("aborted", OSError(103), "connection reset"),
        ("unreachable", OSError(113), "unreachable"),
        ("tls buffers", OSError(12), "out of memory"),
        ("heap", MemoryError(), "out of memory"),
        ("mbedtls text", OSError(-29312, eof), eof[:room]),
        ("text only", OSError("no route"), "no route"),
        ("other clipped", ValueError("x" * 80), "x" * room),
        ("never blank", OSError(), "OSError"),
    ]
    for name, error, want in cases:
        check("net_error " + name, pu.net_error(error), want)


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


# ── rss_news ────────────────────────────────────

import rss_news


def test_read_body_cap_and_decode():
    body = rss_news._read_body(FakeResponse(b"x" * 100), bytearray(40))
    check("cap", len(body), 40)
    check("utf8 cut at cap", rss_news._read_body(FakeResponse("abcà".encode()), bytearray(4)), "abc")
    check("latin-1 falls back", rss_news._read_body(FakeResponse(b"caf\xe9 ok"), bytearray(50)), "caf? ok")
    check("short body", rss_news._read_body(FakeResponse(b"<rss/>"), bytearray(50)), "<rss/>")


def test_clean_text():
    check(
        "escaped html stripped",
        rss_news._clean_text("&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;"),
        "Hello world",
    )
    check("bare < kept", rss_news._clean_text("a < b and c<d"), "a < b and c<d")
    check("escaped bare < kept", rss_news._clean_text("1 &lt; 2"), "1 < 2")
    check("cdata html", rss_news._clean_text("<![CDATA[<p>l&rsquo;Italia &hellip;</p>]]>"), "l'Italia ...")


def test_strip_tags():
    check(
        "a tag ends a word",
        rss_news._clean_text("<p>Primo paragrafo.</p><p>Secondo.</p><li>Uno</li><li>Due</li>riga<br/>dopo"),
        "Primo paragrafo. Secondo. Uno Due riga dopo",
    )
    check("< in an attribute", rss_news._clean_text('x <a title="x<y">text</a> z'), "x text z")
    check("unterminated is text", rss_news._clean_text("a<b c<d e"), "a<b c<d e")
    check("tag, then unterminated", rss_news._clean_text("x <b y> z <c"), "x z <c")
    check("comment, pi", rss_news._clean_text("<!-- c --> after <?xml ?> <br/>end"), "after end")
    check("no tag", rss_news._strip_tags("a > b"), "a > b")


def test_decode_entities():
    check(
        "named entities",
        rss_news._clean_text("Citt&agrave; e caff&egrave; &laquo;ok&raquo; &euro;5"),
        "Citt\xe0 e caff\xe8 \xabok\xbb EUR5",
    )
    check("capitals", rss_news._clean_text("&Egrave; &Agrave; &eacute;"), "\xc8 \xc0 \xe9")
    check("signs", rss_news._clean_text("20&deg; &copy; &reg; 2&times;3 a&middot;b"), "20\xb0 (c) (R) 2x3 a\xb7b")
    check("numeric", rss_news._clean_text("&#39;&#x27;&#X27; &#8217;"), "''' ’")
    check("past 0xFFFF", rss_news._clean_text("&#x1F600;"), chr(0x1F600))
    kept = "&#; &#x; &#abc; &#99999999; &#0; &#65 &foo; R&D; a & b"
    check("not entities", rss_news._clean_text(kept), kept)
    check("escaped html's entities", rss_news._clean_text("Rust&amp;#39;s AT&amp;amp;T"), "Rust's AT&T")
    check("escaped html's tags", rss_news._clean_text("&amp;lt;b&amp;gt;x&amp;lt;/b&amp;gt;"), "x")
    check("adjacent", rss_news._decode_entities("&lt;&lt;x&gt;"), "<<x>")


def test_titles_keep_angle_brackets():
    # a title is plain text: the tag strips took "<T>" and a CDATA "<u8>"
    clean = rss_news._clean_text("Rust&#39;s Option&lt;T&gt;", rss_news.MAX_TITLE_CHARS, html=False)
    check("escaped title", clean, "Rust's Option<T>")
    xml = (
        "<rss><item><title><![CDATA[Vec<u8> explained]]></title>"
        "<description><![CDATA[<p>Bytes</p>]]></description></item>"
        "<item><title>A &lt;b&gt; tag</title></item></rss>"
    )
    items = rss_news._parse_feed(xml, 2)
    check("cdata title", [i["title"] for i in items], ["Vec<u8> explained", "A <b> tag"])
    check("summary still stripped", items[0]["summary"], "Bytes")


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


def test_parse_feed_blocks():
    # RSS 1.0 lists its links in <items> before the items: the image's title
    # stood in for the newest story
    rdf = (
        '<rdf:RDF><channel><title>Site</title><items><rdf:Seq><rdf:li resource="http://a"/>'
        "</rdf:Seq></items></channel><image><title>Site logo</title></image>"
        '<item rdf:about="http://a"><title>Newest</title><link>http://a</link></item>'
        '<item rdf:about="http://b"><title>Second</title></item></rdf:RDF>'
    )
    items = rss_news._parse_feed(rdf, 2)
    check("rss 1.0 newest first", [i["title"] for i in items], ["Newest", "Second"])
    check("rss 1.0 link", items[0]["link"], "http://a")
    atom = (
        '<feed><title>F</title><entry xml:lang="it"><title>A</title></entry>'
        "<entry>\n<title>B</title><updated>2026-10-04T08:15:00Z</updated></entry></feed>"
    )
    items = rss_news._parse_feed(atom, 4)
    check("atom entries", [i["title"] for i in items], ["A", "B"])
    check("atom date", items[1]["date"], "2026-10-04T08:15:00Z")
    rss = "<rss><item><title>T</title><pubDate>Sat, 04 Oct 2026 08:15:00 +0200</pubDate></item></rss>"
    check("pubDate in any case", rss_news._parse_feed(rss, 1)[0]["date"], "Sat, 04 Oct 2026 08:15:00 +0200")


def test_fetch_feed_parse_out_of_memory():
    # a MemoryError while parsing skips that feed; it escaped latest() and
    # lost the feeds already read
    body = b"<rss><channel><item><title>One</title></item></channel></rss>"
    real_parse = rss_news._parse_feed

    def no_memory(xml, count):
        raise MemoryError("memory allocation failed")

    rss_news._parse_feed = no_memory
    try:
        req = ScriptedRequests([HttpResponse(200, body)])
        check("parse oom skips the feed", rss_news._fetch_feed("F", "https://f", 2, req), [])
    finally:
        rss_news._parse_feed = real_parse
    req = ScriptedRequests([HttpResponse(200, body)])
    check("feed parsed", [i["title"] for i in rss_news._fetch_feed("F", "https://f", 2, req)], ["One"])


def test_latest_pages_once():
    pages = []
    saved_config = rss_news.CONFIG_FILE
    rss_news.CONFIG_FILE = _TMP  # defaults, not a device's saved feeds
    _rm(_TMP)
    saved = (rss_news.check_wifi, rss_news._http_module, rss_news._fetch_feed, rss_news._paged_lines, rss_news._poll_key)
    rss_news.check_wifi = lambda: True
    rss_news._http_module = lambda: object()
    rss_news._fetch_feed = lambda name, url, per, req: [
        {"title": "" if name == "ANSA" else "T" + name, "summary": "S", "source": name, "link": "", "date": ""}
    ]
    rss_news._paged_lines = lambda lines, page_lines=8: pages.append(lines)
    rss_news._poll_key = lambda: None
    try:
        count = rss_news.latest()
    finally:
        (rss_news.check_wifi, rss_news._http_module, rss_news._fetch_feed, rss_news._paged_lines, rss_news._poll_key) = saved
        rss_news.CONFIG_FILE = saved_config
        _rm(_TMP)
    check("one paged call", len(pages), 1)
    check("item count", count, len(rss_news.DEFAULT_FEEDS))
    check("first line", pages[0][0], "[1] BBC World")
    check("empty title shown as such", "(no title)" in pages[0], True)


def test_dates_titles_folds():
    check("rss zone dropped", rss_news._short_date("Sat, 04 Oct 2026 08:15:00 +0200"), "Sat, 04 Oct 2026 08:15:00")
    check("named zone dropped", rss_news._short_date("Mon, 5 Oct 2026 8:15 GMT"), "Mon, 5 Oct 2026 8:15")
    check("atom zone dropped", rss_news._short_date("2026-10-04T08:15:00+02:00"), "2026-10-04 08:15")
    check("no zone", rss_news._short_date("Sat, 04 Oct 2026 08:15:00"), "Sat, 04 Oct 2026 08:15:00")
    check("miniflux date", rss_news._short_date("2026-10-04 08:15"), "2026-10-04 08:15")
    check("no date", rss_news._short_date(""), "")
    # one character that the screen draws as three: counted as three
    check("folds", rss_news._clean_text("Attesa… 5€ &#8230; &#8364;"), "Attesa... 5EUR ... EUR")
    printed = []
    previews = []
    saved = rss_news._preview_print
    rss_news.print = lambda *args, **kw: printed.append(" ".join([str(a) for a in args]))
    rss_news._preview_print = lambda text, width=0, max_lines=0, fg=None: previews.append(text)
    try:
        item = {"source": "ANSA", "date": "Sat, 04 Oct 2026 08:15:00 +0200", "title": "", "summary": "S"}
        rss_news._render_news_summary(item, 0, 1)
    finally:
        del rss_news.print
        rss_news._preview_print = saved
    check("summary date, no zone", ("08:15:00" in printed[0], "+02" in printed[0]), (True, False))
    check("summary empty title", previews[0], "(no title)")
    pages = []
    saved = (rss_news.CONFIG_FILE, rss_news._paged_lines)
    rss_news.CONFIG_FILE = _TMP
    rss_news._paged_lines = lambda lines, page_lines=8: pages.append(lines)
    try:
        url = "https://example.com/" + "a" * 80
        pu.save_json(_TMP, {"feeds": [{"name": "Lungo", "url": url}], "preview_chars": 110, "items_per_feed": 2})
        rss_news.feeds()
    finally:
        rss_news.CONFIG_FILE, rss_news._paged_lines = saved
        _rm(_TMP)
    check("feed urls fit", max(len(x) for x in pages[0]) <= pu.DISPLAY_WIDTH, True)


def test_latest_stops_between_feeds():
    # q (or Ctrl+C) between feeds stops and keeps what came; the old list
    # is dropped before the first feed's buffer
    saved_config = rss_news.CONFIG_FILE
    rss_news.CONFIG_FILE = _TMP  # the three default feeds
    _rm(_TMP)
    saved = (rss_news.check_wifi, rss_news._http_module, rss_news._fetch_feed, rss_news._poll_key, rss_news._LAST_ITEMS)
    seen = []
    keys = []

    def fetch(name, url, per, req):
        seen.append(len(rss_news._LAST_ITEMS))
        return [{"title": "T" + name, "summary": "", "source": name, "link": "", "date": ""}]

    def poll():
        key = keys.pop(0)
        if key == "ctrl-c":
            raise KeyboardInterrupt
        return key

    rss_news.check_wifi = lambda: True
    rss_news._http_module = lambda: object()
    rss_news._fetch_feed = fetch
    rss_news._poll_key = poll
    try:
        rss_news._LAST_ITEMS = [{"title": "old"}]
        keys[:] = [None, "q"]
        check("q stops after one feed", rss_news.latest(show=False), 1)
        check("old list gone before the fetch", seen, [0])
        check("q keeps what came", [i["title"] for i in rss_news._LAST_ITEMS], ["TBBC World"])
        keys[:] = [None, None, "ctrl-c"]
        try:
            rss_news.latest(show=False)
        except KeyboardInterrupt:
            pass
        check("ctrl-c keeps what came", len(rss_news._LAST_ITEMS), 2)
    finally:
        (rss_news.check_wifi, rss_news._http_module, rss_news._fetch_feed, rss_news._poll_key, rss_news._LAST_ITEMS) = saved
        rss_news.CONFIG_FILE = saved_config
        _rm(_TMP)


def test_news_detail_one_pager():
    # one pager for every field: a pager per field let a long summary
    # scroll the lines above it off before the first prompt
    pages = []
    saved = (rss_news._paged_lines, rss_news._screen_header)
    rss_news._paged_lines = lambda lines, page_lines=pu.PAGE_LINES: pages.append(lines)
    rss_news._screen_header = lambda title, status=True: None
    item = {
        "source": "BBC World",
        "date": "Sat, 04 Oct 2026 08:15:00 +0200",
        "title": "Titolo",
        "summary": "parola " * 60,
        "link": "http://a",
    }
    try:
        rss_news._render_news_detail(item, 1, 3)
    finally:
        rss_news._paged_lines, rss_news._screen_header = saved
    check("one pager", len(pages), 1)
    lines = pages[0]
    check("source first", lines[0], "[2/3] BBC World")
    check("title after the date", lines[lines.index("Title:") + 1], "Titolo")
    check("summary, link last", ("Summary:" in lines, lines[-2:]), (True, ["Link:", "http://a"]))
    check("lines fit the width", max(len(x) for x in lines) <= pu.DISPLAY_WIDTH, True)


def test_fetch_feed_low_memory():
    # no 26 KB block free: a 16 KB read, allocated before the one request
    body = b"<rss><channel><item><title>One</title></item></channel></rss>"
    real_buffer = rss_news._buffer
    sizes = []

    def tight_buffer(limit):
        sizes.append(limit)
        if limit > rss_news.MIN_XML_BYTES:
            raise MemoryError("memory allocation failed")
        return real_buffer(limit)

    printed = []
    rss_news._buffer = tight_buffer
    rss_news.print = lambda *args, **kw: printed.append(" ".join([str(a) for a in args]))
    req = ScriptedRequests([HttpResponse(200, body)])
    try:
        items = rss_news._fetch_feed("F", "https://f", 2, req)
    finally:
        rss_news._buffer = real_buffer
        del rss_news.print
    check("16 KB retry", sizes, [rss_news.MAX_XML_BYTES, rss_news.MIN_XML_BYTES])
    check("one request", len(req.calls), 1)
    check("items read", [i["title"] for i in items], ["One"])
    check("said so", "Low memory, reading 16 KB" in printed, True)
    done = printed[-1].split(" ")
    check("progress units", (done[:3], done[-1]), (["ok", "1", "items"], "ms"))


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


def test_format_result_float32():
    # rp2 floats are float32: the values the device produces, shown as there.
    saved = sc._SINGLE
    try:
        sc._SINGLE = True
        check("1e11", sc._format_result(99999997952.0), "1e+11")
        check("100000000/3", sc._format_result(33333334.0), "3.333333e+07")
        check("exp(20)", sc._format_result(485165184.0), "4.851652e+08")
        check("integral below 1e7", sc._format_result(9999999.0), "9999999")
        check("1/3", sc._format_result(0.3333333432674408), "0.3333333")
        check("small negative", sc._format_result(-1.2345678e-05), "-1.234568e-05")
        check("history column: fewer digits", sc._format_result(33333334.0, 10), "3.3333e+07")
        check("ints stay exact", sc._format_result(2 ** 40), "1099511627776")
    finally:
        sc._SINGLE = saved


def test_calc_huge_ints():
    # str() of an int is quadratic (2**100000 took seconds on the device):
    # from 10**1000 on none is printed, nor kept in the history.
    saved = (sc._LAST, list(sc._HISTORY))
    try:
        big = 10 ** 1000
        check("too large", sc._format_result(big), "too large to show")
        check("negative too", sc._format_result(-big), "too large to show")
        check("170! still shown", sc._format_result(sc._calc_factorial(170), 12), "7.257416e306")
        check("2^1000 still shown", sc._format_result(2 ** 1000, 12), "1.071509e301")
        del sc._HISTORY[:]
        sc._store("10**1000", big)
        check("ans keeps it", sc._LAST == big, True)
        check("the history doesn't", sc._HISTORY, [])
    finally:
        sc._LAST = saved[0]
        sc._HISTORY[:] = saved[1]


def test_calc_degrees():
    # Degrees: zeros at multiples of 90 come out as zeros (float32's pi made
    # sin(180) -8.7e-08) and tan(90) is an error, not -22877332.
    import math

    saved = (sc.DEG_MODE, sc._LAST, list(sc._HISTORY))
    try:
        sc.DEG_MODE = True
        ns = sc._calc_namespace()  # what the prompt evaluates with
        check("sin(180)", ns["sin"](180), 0)
        check("cos(90)", ns["cos"](90), 0)
        check("sin(540)", ns["sin"](540), 0)
        check("tan(180)", ns["tan"](180), 0)
        check("sin(-90)", abs(ns["sin"](-90) + 1) < 1e-6, True)
        check("reduced before converting", abs(ns["sin"](360 * 10 ** 20 + 30) - 0.5) < 1e-6, True)
        check("tan(45)", abs(ns["tan"](45) - 1) < 1e-6, True)
        undefined = []
        for angle in (90, -90, 270, 450.0):
            try:
                ns["tan"](angle)
            except ValueError:
                undefined.append(angle)
        check("tan at odd multiples of 90", undefined, [90, -90, 270, 450.0])
        check("sin() at the REPL too", sc.sin(180), 0)
        sc.DEG_MODE = False
        check("radians untouched", ns["sin"](math.pi) != 0, True)
    finally:
        sc.DEG_MODE, sc._LAST = saved[:2]
        sc._HISTORY[:] = saved[2]


# ── notes ───────────────────────────────────────

import notes


def test_note_stamp_needs_clock():
    saved = notes._clock_synced
    notes._clock_synced = lambda: False
    try:
        check("no stamp before sync", notes._ts(), "")
    finally:
        notes._clock_synced = saved


def test_note_detail_one_pager():
    # header and body through one pager: the body's own pager let a long
    # note scroll the header off before the first prompt
    pages = []
    saved = (notes._paged_lines, notes._screen_header)
    notes._paged_lines = lambda lines, page_lines=pu.PAGE_LINES: pages.append(lines)
    notes._screen_header = lambda title, status=True: None
    try:
        note = {"t": "Spesa", "b": "pane\n" + "latte " * 40, "ts": "10-07 09:30", "done": True}
        notes._render_note_detail(note, 0, 2)
    finally:
        notes._paged_lines, notes._screen_header = saved
    check("one pager", len(pages), 1)
    lines = pages[0]
    check("header first", lines[:7], ["#1/2 [DONE]", "Date: 10-07 09:30", "Title:", "Spesa", "---", "Body:", "pane"])
    check("body wrapped to the width", len(lines) > 8 and max(len(x) for x in lines) <= pu.DISPLAY_WIDTH, True)


def test_note_cut_is_said():
    # add() cut the body to MAX_NOTE_CHARS without a word
    saved = (notes.DATA_FILE, notes._NOTES)
    notes.DATA_FILE = _TMP
    notes._NOTES = []
    printed = []
    notes.print = lambda *args, **kw: printed.append(" ".join([str(a) for a in args]))
    try:
        _rm(_TMP)
        check("added", notes.add("x" * 900), True)
        check("body cut", len(notes._NOTES[0]["b"]), notes.MAX_NOTE_CHARS)
        check("said so", printed[0], "Too long: kept 800 of 900 chars.")
        printed[:] = []
        notes.add("short")
        check("quiet when it fits", [p for p in printed if "Too long" in p], [])
        printed[:] = []
        notes.edit(2, "y" * 801)
        check("edit says so too", printed[0], "Too long: kept 800 of 801 chars.")
    finally:
        del notes.print
        notes.DATA_FILE, notes._NOTES = saved
        _rm(_TMP)


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


_SSE_OK = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'


def _with_ai(config, body):
    # ask() against a scripted server; returns what openrouter_ai printed
    saved = (ai.CONFIG_FILE, ai.check_wifi, ai._http_module)
    out = []
    ai.CONFIG_FILE = _TMP
    ai.check_wifi = lambda: True
    ai.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
    try:
        pu.save_json(_TMP, config)
        body()
    finally:
        ai.CONFIG_FILE, ai.check_wifi, ai._http_module = saved
        del ai.print
        ai._HISTORY[:] = []
        ai._LAST_RESPONSES[:] = []
        _rm(_TMP)
    return out


def test_ai_body_is_utf8_bytes():
    # requests sends len(data) as Content-Length: a str counts characters,
    # and MicroPython's json writes accents as raw UTF-8
    prompt = "perché — caffè"
    req = ScriptedRequests([HttpResponse(200, _SSE_OK)])

    def run():
        ai._http_module = lambda: req
        check("reply", ai.ask(prompt), "ok")

    _with_ai({"api_key": "k"}, run)
    data = req.calls[0][2]["data"]
    check("body is bytes", isinstance(data, bytes), True)
    check("prompt round-trips", ai.json.loads(data.decode())["messages"][-1]["content"], prompt)


def test_ai_key_only_to_openrouter():
    lan = "http://192.168.1.20:8080/v1/chat/completions"

    def auth(config):
        req = ScriptedRequests([HttpResponse(200, _SSE_OK)])

        def run():
            ai._http_module = lambda: req
            ai.ask("hi")

        _with_ai(config, run)
        return req.calls[0][2]["headers"].get("Authorization") if req.calls else "no request"

    check("OpenRouter gets its key", auth({"api_key": "sk-or"}), "Bearer sk-or")
    check("LAN server: not the OpenRouter key", auth({"api_key": "sk-or", "endpoint": lan}), None)
    check("LAN server: its own key", auth({"api_key": "sk-or", "endpoint": lan, "endpoint_key": "lan"}), "Bearer lan")
    check("look-alike URL", auth({"api_key": "sk-or", "endpoint": "http://x/?openrouter.ai"}), None)

    def run():
        ai.set_endpoint(lan, "lan")
        check("key kept with its URL", ai._load_config().get("endpoint_key"), "lan")
        ai.set_endpoint("http://10.0.0.2/v1/chat/completions")
        check("new URL drops the old key", "endpoint_key" in ai._load_config(), False)
        ai.set_endpoint(lan, "lan")
        ai.set_endpoint()
        check("back to OpenRouter", ai._load_config(), {"api_key": "sk-or"})

    _with_ai({"api_key": "sk-or"}, run)


class FailingRequests:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def request(self, method, url, **kw):
        self.calls += 1
        raise self.error


class CutRaw:
    # hands out `data`, then the connection drops
    def __init__(self, data, error):
        self.data, self.error = data, error

    def readinto(self, buf):
        if not self.data:
            raise self.error
        n = len(self.data)
        buf[:n] = self.data
        self.data = b""
        return n


def test_ai_network_errors_in_words():
    def run():
        ai._http_module = lambda: FailingRequests(OSError(110))
        check("no reply", ai.ask("hi"), None)
        text = ai._sse_text(CutRaw(b'data: {"choices":[{"delta":{"content":"Hal"}}]}\n', OSError(104)), lambda t: None)
        check("text before the cut", text, "Hal")

    out = _with_ai({"api_key": "k"}, run)
    check("request error", "Err: timed out" in out, True)
    check("stream cut", "Cut: connection reset" in out, True)


# ── weather ─────────────────────────────────────

import weather


def test_weather_failed_save():
    saved = weather.CONFIG_FILE
    out = []
    weather.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
    try:
        weather.CONFIG_FILE = _TMP
        check("saved", weather.set_location(45.5, 9.25, "Milano"), True)
        check("stored", weather._get_location(), (45.5, 9.25, "Milano"))
        weather.CONFIG_FILE = _HERE + "/no_such_dir/weather.json"
        check("failed save is no success", weather.set_location(41.75, 12.5, "Roma"), False)
        check("said so", "Location not saved." in out, True)
    finally:
        weather.CONFIG_FILE = saved
        del weather.print
        _rm(_TMP)


_WEATHER_STUBS = ("CONFIG_FILE", "check_wifi", "_http_module", "_ticks_ms")


def _with_weather(body, online=True):
    # body() against a scripted server; returns what weather printed
    saved = [getattr(weather, name) for name in _WEATHER_STUBS]
    out = []
    weather.CONFIG_FILE = _TMP
    weather.check_wifi = lambda: online
    weather.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
    try:
        body()
    finally:
        for name, value in zip(_WEATHER_STUBS, saved):
            setattr(weather, name, value)
        del weather.print
        _rm(_TMP)
    return out


def test_weather_network_error_in_words():
    def run():
        weather._http_module = lambda: FailingRequests(OSError(-2))
        check("no weather", weather.now(), None)

    check("said in words", "Err: DNS failed" in _with_weather(run), True)


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


class _RecPWM:
    # machine.PWM stand-in that records what is done to the speaker pins
    log = []

    def __init__(self, pin):
        self.pin = pin

    def freq(self, hz):
        _RecPWM.log.append(("freq", self.pin))

    def duty_u16(self, duty):
        _RecPWM.log.append(("duty", self.pin, duty))

    def deinit(self):
        _RecPWM.log.append(("deinit", self.pin))


class _RecMachine:
    PWM = _RecPWM


_RecMachine.Pin = lambda n: n


class _InterruptedI2S:
    def write(self, data):
        raise KeyboardInterrupt


def test_synth_ctrl_c_stops_the_tune():
    def interrupted(ms):
        raise KeyboardInterrupt

    out = []
    saved = (sys.modules.get("machine"), synth._sleep_ms, synth._poll_key, synth._init_audio, synth._use_pwm)
    sys.modules["machine"] = _RecMachine
    synth._sleep_ms = interrupted
    synth._poll_key = lambda: None
    synth.print = lambda *args, **kw: out.append(" ".join(str(a) for a in args))
    del _RecPWM.log[:]
    try:
        try:
            synth._play_freq_pwm(440, 50)
            check("pwm: Ctrl+C reaches the caller", "returned", "KeyboardInterrupt")
        except KeyboardInterrupt:
            pass
        silent = [("duty", 26, 0), ("duty", 27, 0), ("deinit", 26), ("deinit", 27)]
        check("pwm: silenced and released first", _RecPWM.log[-4:], silent)
        del _RecPWM.log[:]
        check("seq stops", synth.seq("C D E F"), False)
        check("on its first note", (_RecPWM.log.count(("deinit", 26)), out[-1]), (1, "Stopped."))
        synth._init_audio = lambda: _InterruptedI2S()
        try:
            synth._play_freq_i2s(440, 50)
            check("i2s: Ctrl+C reaches the caller", "returned", "KeyboardInterrupt")
        except KeyboardInterrupt:
            pass
        synth._use_pwm = False
        synth._sleep_ms = lambda ms: None
        check("rtttl stops", synth.rtttl("t:d=4,o=5,b=120:c,d,e"), False)
    finally:
        if saved[0] is None:
            sys.modules.pop("machine", None)
        else:
            sys.modules["machine"] = saved[0]
        synth._sleep_ms, synth._poll_key, synth._init_audio, synth._use_pwm = saved[1:]
        del synth.print


def test_calc_ans_chaining():
    saved = sc._LAST
    try:
        sc._LAST = 10
        check("+ chains", sc._prepare_expr("+5"), "ans+5")
        check("* chains", sc._prepare_expr("*2"), "ans*2")
        check("- stays negative", sc._prepare_expr("-5"), "-5")
        check("^2 chains as a power", sc._prepare_expr("^2"), "ans**2")
        sc._LAST = None
        check("no ans yet", sc._prepare_expr("+5"), "+5")
        check("^ is a power, not XOR", eval(sc._prepare_expr("2^10")), 1024)
        check("10^3", eval(sc._prepare_expr("10^3")), 1000)
    finally:
        sc._LAST = saved


def test_calc_prompt():
    # What the calc's help lists works at its prompt: deg, rad, history...
    # as commands; ln, store() and recall() in expressions.
    saved = (sc.safe_input, sc._calc_help, sc.clip, sc._LAST, list(sc._HISTORY), dict(sc._VARS), sc.DEG_MODE)
    script = ["2^10", "^2", "deg", "sin(90)", 'store("r", 7)', "r*2", "ln(1)", 'recall("r")']
    script += ["nope + 1", "history", "last", "variables()", "h", "rad()", "q"]
    helps = []
    limits = []
    real_clip = sc.clip
    try:
        sc._LAST = None
        del sc._HISTORY[:]
        sc._VARS.clear()
        sc.safe_input = lambda prompt: script.pop(0)
        sc._calc_help = lambda: helps.append(1)
        sc.clip = lambda text, limit: limits.append(limit) or real_clip(text, limit)
        sc.calc()
        check("results kept", [item["result"] for item in sc._HISTORY], [1024, 1048576, 1, 14, 0])
        check("store() at the prompt", sc._VARS, {"r": 7})
        check("the calc's own help", helps, [1])
        check("deg and rad() switch", sc.DEG_MODE, False)
        check("errors clipped to the screen", sc.DISPLAY_WIDTH - 5 in limits, True)
        check("every line read", script, [])
    finally:
        sc.safe_input, sc._calc_help, sc.clip, sc._LAST = saved[:4]
        sc._HISTORY[:] = saved[4]
        sc._VARS.clear()
        sc._VARS.update(saved[5])
        sc.DEG_MODE = saved[6]


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
    items = rss_news._mf_items({"entries": [{"title": "Vec<u8> &amp; co", "content": "<p>x</p>"}]})
    check("miniflux title keeps <", items[0]["title"], "Vec<u8> & co")


def test_miniflux_large_and_401():
    def run():
        def big():
            return HttpResponse(200, raw=LazyRaw(b'{"entries": [', rss_news.MF_MAX_BYTES + 10, b"]}"))

        req = ScriptedRequests([big(), HttpResponse(200, _MF_BODY)])
        rss_news._http_module = lambda: req
        check("falls back to 1", rss_news.mf(), 1)
        check("retry used limit=1", "limit=1" in req.calls[1][1], True)
        # no 48 KB block free: one entry in the smaller buffer, and the
        # failed allocation comes before any request
        real_buffer = rss_news._buffer

        def tight_buffer(limit):
            if limit > rss_news.MF_MIN_BYTES:
                raise MemoryError("memory allocation failed")
            return real_buffer(limit)

        rss_news._buffer = tight_buffer
        req = ScriptedRequests([HttpResponse(200, _MF_BODY)])
        rss_news._http_module = lambda: req
        try:
            check("low memory falls back", rss_news.mf(), 1)
            check("one request, for 1", [("limit=1" in c[1]) for c in req.calls], [True])
        finally:
            rss_news._buffer = real_buffer
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


def test_miniflux_content_cut():
    # a post opening with a long tag: the cut fell inside it, and the
    # unterminated tag read as the summary
    content = '<figure><img srcset="' + "x" * 8000 + '"></figure><p>Testo</p>'
    items = rss_news._mf_items({"entries": [{"title": "T", "content": content}]})
    check("no tag text as summary", items[0]["summary"], "")
    long_text = "<p>" + "parola " * 900 + "</p>"  # cut inside the text: kept
    items = rss_news._mf_items({"entries": [{"title": "T", "content": long_text}, {"title": "U", "content": None}]})
    check("summary from the head", items[0]["summary"][:13], "parola parola")
    check("no content", items[1]["summary"], "")

    def run():
        # cleaning the entries runs out of memory: no MemoryError out of mf()
        real_clean = rss_news._clean_text

        def no_memory(text, limit=0, html=True):
            raise MemoryError("memory allocation failed")

        rss_news._clean_text = no_memory
        rss_news._LAST_ITEMS = [{"title": "old RSS item"}]
        req = ScriptedRequests([HttpResponse(200, _MF_BODY)])
        rss_news._http_module = lambda: req
        try:
            check("out of memory while cleaning", rss_news.mf(), 0)
            check("no stale items", rss_news._LAST_ITEMS, [])
        finally:
            rss_news._clean_text = real_clean

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


class FakeWlan:
    # network.WLAN(STA_IF) on cyw43: scan() hands out `items`; connect()
    # reaches the status `reach` gives that SSID (default: still joining).
    def __init__(self, items=(), reach=None, up=True, linked=False):
        self.items = list(items)
        self.reach = reach or {}
        self.up = up
        self.state = 3 if linked else 0
        self.joins = []
        self.scans = 0

    def active(self, on=None):
        if on is None:
            return self.up
        self.up = bool(on)

    def scan(self):
        self.scans += 1
        return self.items

    def connect(self, ssid, key):
        self.joins.append((ssid, key))
        self.state = self.reach.get(ssid, 1)

    def disconnect(self):
        self.state = 0

    def status(self):
        return self.state

    def isconnected(self):
        return self.state == 3

    def ifconfig(self):
        return ("10.0.0.7", "255.255.255.0", "10.0.0.1", "10.0.0.1")

    def config(self, key):
        return self.joins[-1][0] if self.joins else ""


def _scan_item(ssid, rssi, auth=5):
    # (ssid, bssid, channel, RSSI, security, hidden) as cyw43 reports them
    return (ssid, b"\x02\x00\x00\x00\x00\x01", 6, rssi, auth, 1)


def test_wifi_scan_skips_bad_names():
    wlan = FakeWlan(
        [
            _scan_item(b"home", -60),
            _scan_item(b"\xff\xfebad", -40),  # not UTF-8: 1.27 raised, the scan failed
            _scan_item(b"\x00\x00\x00\x00", -45),  # hidden network
            _scan_item(b"", -50),
            _scan_item("caffè".encode(), -70),
            _scan_item(b"home", -80),  # a second access point
        ]
    )
    check("names, strongest first", [n[0] for n in wifi_manager.scan_networks(wlan)], ["home", "caffè"])
    check("undecodable name is empty", wifi_manager._decode_ssid(b"\xc3"), "")


class FakeNetwork:
    # the network module of the rp2 port: cyw43 link states as STAT_*
    STA_IF = 0
    STAT_IDLE = 0
    STAT_CONNECTING = 1
    STAT_GOT_IP = 3
    STAT_CONNECT_FAIL = -1
    STAT_NO_AP_FOUND = -2
    STAT_WRONG_PASSWORD = -3

    def __init__(self, wlan):
        self.wlan = wlan

    def WLAN(self, interface):
        return self.wlan


_WIFI_STUBS = (
    "_NETWORK_MODULE",
    "CREDENTIALS_FILE",
    "_sleep_ms",
    "_ticks_ms",
    "_poll_key",
    "_read_key",
    "_read_line",
    "_screen_header",
    "_paged_lines",
    "_sync_clock",
)


def _with_wifi(wlan, body, keys=(), line=None, polls=()):
    # body() against a fake radio, keyboard and clock (500 ms a reading);
    # returns what wifi_manager printed, its sleeps, the chooser's lines
    # and the password prompts.
    w = wifi_manager
    saved = [getattr(w, name) for name in _WIFI_STUBS]
    got = {"out": [], "sleeps": [], "shown": [], "prompts": []}
    keys = list(keys)
    polls = list(polls)
    clock = [0]

    def tick():
        clock[0] += 500
        return clock[0]

    def read_line(prompt, mask=None):
        got["prompts"].append(prompt)
        return line

    w._NETWORK_MODULE = FakeNetwork(wlan)
    w.CREDENTIALS_FILE = _TMP
    w._sleep_ms = got["sleeps"].append
    w._ticks_ms = tick
    w._poll_key = lambda: polls.pop(0) if polls else None
    w._read_key = lambda timeout_ms=None: keys.pop(0) if keys else "q"
    w._read_line = read_line
    w._screen_header = lambda *a, **k: None
    w._paged_lines = lambda lines, page_lines=8: got["shown"].extend(lines)
    w._sync_clock = lambda: None
    w.print = lambda *a, **k: got["out"].append(" ".join(str(x) for x in a))
    try:
        body()
    finally:
        for name, value in zip(_WIFI_STUBS, saved):
            setattr(w, name, value)
        del w.print
        _rm(_TMP)
    return got


def test_wifi_cancel_stops():
    # q/Esc while joining: no "Fail", no next network, no chooser after it
    wlan = FakeWlan([_scan_item(b"home", -50), _scan_item(b"office", -60)])

    def run():
        wifi_manager.save_credentials({"home": "p1", "office": "p2"})
        check("cancel is not a failure", wifi_manager.auto_connect_or_prompt(), None)

    got = _with_wifi(wlan, run, polls=["q"])
    check("first network only", wlan.joins, [("home", "p1")])
    check("said once, no Fail", [l for l in got["out"] if l == "Cancelled." or l.startswith("Fail")], ["Cancelled."])
    check("no chooser", (wlan.scans, got["shown"]), (1, []))


def test_wifi_failed_save_reported():
    wlan = FakeWlan([_scan_item(b"cafe", -50)], reach={"cafe": 3})

    def run():
        wifi_manager.CREDENTIALS_FILE = _HERE + "/no_such_dir/wifi.json"
        check("joined all the same", wifi_manager.auto_connect_or_prompt(), True)

    got = _with_wifi(wlan, run, keys=["1"], line="secret")
    check("no false OK", "OK. Saved." in got["out"], False)
    check("says not saved", "Connected, not saved." in got["out"], True)


def _fails(got):
    return [line for line in got["out"] if line.startswith("Fail")]


def test_wifi_says_why():
    # the link status in words, on the WiFi screen and for each failure;
    # names whole (32 bytes fit the 52 columns)
    name = "x" * 32
    wlan = FakeWlan([_scan_item(b"home", -50), _scan_item(name.encode(), -60)], reach={"home": -3, name: -2})

    def run():
        wifi_manager.save_credentials({"home": "p1", name: "p2"})
        check("none joined", wifi_manager.auto_connect_or_prompt(interactive=False), False)
        wifi_manager.print_connection_status()

    got = _with_wifi(wlan, run)
    check("reasons", _fails(got), ["Fail: wrong password", "Fail: network not found"])
    check("name whole", "Try: " + name in got["out"], True)
    check("status screen", "WiFi: network not found" in got["out"], True)

    slow = FakeWlan([_scan_item(b"home", -50)])  # never gets past joining
    got = _with_wifi(slow, lambda: wifi_manager.connect_saved_networks(slow, {"home": "p1"}))
    check("timeout", _fails(got), ["Fail: timed out, connecting"])

    def ask():
        check("chosen, refused", wifi_manager.auto_connect_or_prompt(), False)

    got = _with_wifi(FakeWlan([_scan_item(b"home", -50)], reach={"home": -3}), ask, keys=["1"], line="bad")
    check("prompt says Enter", got["prompts"], ["Password then Enter: "])
    check("chooser failure", _fails(got), ["Fail: wrong password"])

    def up():
        check("status on success", wifi_manager.connect_to_wifi(joined, "home", "p1"), 3)
        wifi_manager.print_connection_status()

    joined = FakeWlan(reach={"home": 3})
    got = _with_wifi(joined, up)
    check("connected screen", got["out"], ["WiFi: connected", "SSID: home", "IP: 10.0.0.7"])


def test_wifi_open_networks_and_nine():
    items = [_scan_item("net{}".format(i).encode(), -50 - i) for i in range(12)]
    items.append(_scan_item(b"cafe", -40, auth=0))
    check("auth kept", wifi_manager.scan_networks(FakeWlan(items[:1]))[0], ("net0", -50, 5))
    wlan = FakeWlan(items, reach={"cafe": 3})

    def run():
        check("open joined", wifi_manager.auto_connect_or_prompt(), True)
        check("saved with no key", wifi_manager.load_credentials(), {"cafe": ""})

    got = _with_wifi(wlan, run, keys=["1"])
    check("no password asked", got["prompts"], [])
    check("empty key: open auth", wlan.joins, [("cafe", "")])
    check("nine listed", len(got["shown"]), 9)
    check("open marked", got["shown"][0], "1: cafe -40dBm open")
    wlan = FakeWlan(items, reach={"net7": 3})
    got = _with_wifi(wlan, wifi_manager.auto_connect_or_prompt, keys=["9"], line="pw")
    check("9 picks the ninth", wlan.joins, [("net7", "pw")])


def test_wifi_one_scan_no_idle_wait():
    up = FakeWlan(linked=True)
    got = _with_wifi(up, lambda: check("already up", wifi_manager.auto_connect_or_prompt(), True))
    check("no warm-up wait when up", got["sleeps"], [])

    down = FakeWlan([_scan_item(b"cafe", -50)], up=False)

    def run():
        wifi_manager.save_credentials({"home": "p1"})  # saved, out of range
        check("nothing chosen", wifi_manager.auto_connect_or_prompt(), False)

    got = _with_wifi(down, run)
    check("radio switched on", down.up, True)
    check("one warm-up wait", got["sleeps"], [wifi_manager.WLAN_WARMUP_MS])
    check("one scan for both", down.scans, 1)
    check("chooser from it", got["shown"], ["1: cafe -50dBm"])


class BusyWlan(FakeWlan):
    def scan(self):
        self.scans += 1
        raise OSError(110)


def test_wifi_scan_error_in_words():
    wlan = BusyWlan()
    got = _with_wifi(wlan, lambda: check("nothing found", wifi_manager._scan_retry(wlan), []))
    check("said in words, retried", got["out"], ["Scan: timed out", "Scan retry", "Scan: timed out"])


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
        pu._key_byte = _REAL_KEY_BYTE


# apps from the SD card


def _write(path, text):
    with open(path, "w") as f:
        f.write(text)


def _rmtree(path):
    try:
        names = os.listdir(path)
    except OSError:
        return
    for name in names:
        full = path + "/" + name
        try:
            os.remove(full)
        except OSError:
            _rmtree(full)
    os.rmdir(path)


def test_app_header():
    import apps

    check("two fields", apps.parse_header("# picocalc-app: Tetris | Blocks"), ("Tetris", "", "Blocks"))
    check("three fields", apps.parse_header("# picocalc-app: Tetris | Games | Blocks "), ("Tetris", "Games", "Blocks"))
    check("name only", apps.parse_header("# picocalc-app:Hello"), ("Hello", "", ""))
    check("no name", apps.parse_header("# picocalc-app:  | x"), None)
    check("plain comment", apps.parse_header("# hello"), None)


def test_app_discover_and_run():
    import apps

    folder = _HERE + "/_tmp_apps"
    _rmtree(folder)
    os.mkdir(folder)
    try:
        _write(folder + "/zeta.py", "# picocalc-app: Alpha | Games | fun\nimport pico_utils\npico_utils._APP_RAN = __name__\n")
        _write(folder + "/beta.py", "import _tmp_helper\n_tmp_helper.go()\n")
        _write(folder + "/_tmp_helper.py", "import pico_utils\ndef go():\n    pico_utils._APP_RAN = 'helper'\n")
        _write(folder + "/._beta.py", "junk")
        _write(folder + "/notes.txt", "x")
        _write(folder + "/boom.py", "1/0\n")
        _write(folder + "/leave.py", "import sys\nsys.exit()\n")
        found = apps.discover((folder,))
        names = [app[0] for app in found]
        check("sorted, dot files and non-py skipped", names, ["_tmp_helper", "Alpha", "beta", "boom", "leave"])
        check("header fields", found[1][:3], ("Alpha", "Games", "fun"))
        keys_from(b" ")
        check("runs as main", apps.run_app(folder + "/zeta.py"), True)
        check("saw __main__", getattr(pu, "_APP_RAN", None), "__main__")
        keys_from(b" ")
        apps.run_app(folder + "/beta.py")
        check("sibling import", getattr(pu, "_APP_RAN", None), "helper")
        check("its modules dropped", "_tmp_helper" in sys.modules, False)
        check("path restored", folder in sys.path, False)
        keys_from(b" ")
        check("error comes back", apps.run_app(folder + "/boom.py"), False)
        keys_from(b" ")
        check("sys.exit comes back", apps.run_app(folder + "/leave.py"), True)
        check("missing dir", apps.discover((folder + "/nope",)), [])
    finally:
        pu._key_byte = _REAL_KEY_BYTE
        if hasattr(pu, "_APP_RAN"):
            del pu._APP_RAN
        _rmtree(folder)


def test_app_puts_back_cwd_and_console():
    # Apps may chdir (the toolkit and its config files are found through the
    # cwd) or give the screen back to the terminal (gfx.end(), or
    # console_suspend() and an error): run_app restores both.
    import apps

    folder = _HERE + "/_tmp_apps"
    _rmtree(folder)
    os.mkdir(folder)
    cwd = os.getcwd()
    saved_print = apps._print_error
    try:
        _write(folder + "/away.py", "import os\nos.chdir(" + repr(folder) + ")\n")
        _write(folder + "/ender.py", "import gfx\ngfx.end()\n")
        _write(folder + "/lender.py", "import pico_utils\npico_utils.console_suspend()\n1/0\n")
        _write(folder + "/plain.py", "x = 1\n")
        keys_from(b" ")
        apps.run_app(folder + "/away.py")
        check("cwd put back", os.getcwd(), cwd)
        os.chdir(cwd)  # _HERE is relative on MicroPython: keep the rest independent

        def body(fake, vt):
            gfx.begin()
            gfx.app()
            keys_from(b" ")
            apps.run_app(folder + "/ender.py")
            check("console back after gfx.end()", (pu._CONSOLE[0] is gfx.CON, gfx._FB[0] is fake), (True, True))
            seen = []
            apps._print_error = lambda error: seen.append(pu._CONSOLE[0] is gfx.CON)
            keys_from(b" ")
            check("error after a suspend", apps.run_app(folder + "/lender.py"), False)
            check("error shown once the console is back", seen, [True])
            gfx.end()
            keys_from(b" ")
            apps.run_app(folder + "/plain.py")
            check("from the REPL: no console", (pu._CONSOLE[0], gfx._FB[0]), (None, None))

        _with_display(body)
    finally:
        pu._key_byte = _REAL_KEY_BYTE
        apps._print_error = saved_print
        os.chdir(cwd)
        _rmtree(folder)


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


# files


def test_files_paths_and_listing():
    import files

    check("join root", files.join("/", "a.py"), "/a.py")
    check("join dir", files.join("/sd/", "b"), "/sd/b")
    check("parent", files.parent("/sd/apps"), "/sd")
    check("parent of top", files.parent("/sd"), "/")
    check("parent of root", files.parent("/"), "/")
    folder = _HERE + "/_tmp_files"
    _rmtree(folder)
    os.mkdir(folder)
    try:
        _write(folder + "/b.txt", "12345")
        _write(folder + "/A.txt", "")
        os.mkdir(folder + "/zdir")
        check("dirs first, then by name", files.entries(folder), [("zdir", True, 0), ("A.txt", False, 0), ("b.txt", False, 5)])
        keys_from(b"n")
        check("delete needs y", files.delete(folder + "/b.txt"), False)
        keys_from(b"y")
        check("delete file", files.delete(folder + "/b.txt"), True)
        keys_from(b"y")
        check("delete empty dir", files.delete(folder + "/zdir", True), True)
        check("left", [e[0] for e in files.entries(folder)], ["A.txt"])
    finally:
        pu._key_byte = _REAL_KEY_BYTE
        _rmtree(folder)


def _ctrl_c(fn, *args):
    # fn(*args) with Ctrl+C typed: what it returns, or that it let it through.
    keys_from(b"\x03")
    try:
        return fn(*args)
    except KeyboardInterrupt:
        return "KeyboardInterrupt"


def test_files_prompts_and_delete():
    # Ctrl+C at a prompt cancels instead of leaving Files; a folder with
    # something in it is said so before any y/n.
    import files

    folder = _HERE + "/_tmp_files"
    _rmtree(folder)
    os.mkdir(folder)
    saved = files.edit
    try:
        _write(folder + "/a.txt", "x")
        check("Ctrl+C at y/n", _ctrl_c(files.delete, folder + "/a.txt"), False)
        check("still there", files._exists(folder + "/a.txt"), True)
        check("Ctrl+C at a name", _ctrl_c(files._ask_name, "Name, then Enter: "), None)
        files.edit = lambda path: True  # quit the editor without saving
        keys_from(b"new.txt\r")
        check("nothing saved, nothing to highlight", files._new_file(folder), None)
        files.edit = lambda path: _write(path, "")
        keys_from(b"new.txt\r")
        check("saved: highlighted", files._new_file(folder), "new.txt")
        os.mkdir(folder + "/full")
        _write(folder + "/full/f.txt", "x")
        keys_from(b"y")
        check("folder not empty", (files.delete(folder + "/full", True), files._exists(folder + "/full")), (False, True))
    finally:
        pu._key_byte = _REAL_KEY_BYTE
        files.edit = saved
        _rmtree(folder)


def test_files_text():
    import files

    check("text", files.is_binary(b"def f():\n\treturn 1\n"), False)
    check("utf-8 is text", files.is_binary("città".encode()), False)
    check("nul is binary", files.is_binary(b"ab\x00cd"), True)
    check("control bytes", files.is_binary(bytes(range(1, 9)) * 4), True)
    check("empty", files.is_binary(b""), False)
    check("cut character dropped", files.decode("caffè".encode()[:-1]), "caff")
    check("bad byte", files.decode(b"a\xffb\xfe"), "a?b?")
    check("whole multi-byte kept", files.decode("è".encode()), "è")
    latin = b"caff\xe8 latte\n" * 200  # small: the suite also runs on the device
    check("latin-1 as ?", files.decode(latin), "caff? latte\n" * 200)
    check(
        "hard wrap keeps indent",
        files.text_lines("    abcdef\n\n\tx\x1by\n", width=6),
        ["    ab", "cdef", "", "    x?", "y"],
    )
    check("crlf", files.text_lines("a\r\nb"), ["a", "b"])


class ReadSpy:
    # The file files.view() opens, its reads recorded.
    def __init__(self, f, sizes):
        self.f = f
        self.sizes = sizes

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.f.close()

    def read(self, n=-1):
        self.sizes.append(n)
        return self.f.read(n)

    def seek(self, pos):
        return self.f.seek(pos)


def test_files_view():
    import files

    folder = _HERE + "/_tmp_files"
    _rmtree(folder)
    os.mkdir(folder)
    shown = []
    reads = []
    saved = (files.pager, files.decode)
    try:
        files.pager = lambda title, lines: shown.append(lines)
        files.open = lambda path, mode="r": ReadSpy(open(path, mode), reads)
        text = "".join("line {}\n".format(i) for i in range(200))  # past the 512-byte sample
        _write(folder + "/t.txt", text)
        check("text shown", files.view(folder + "/t.txt"), True)
        check("all of it, from the start", shown, [files.text_lines(text)])
        check("the sample, then the text", reads, [512, files.VIEW_MAX])
        with open(folder + "/b.bin", "wb") as f:
            f.write(b"\x00" * 600 + b"text")
        del reads[:]
        keys_from(b" ")
        check("binary not shown", (files.view(folder + "/b.bin"), len(shown)), (False, 1))
        check("binary: only the sample read", reads, [512])
        with open(folder + "/l.txt", "wb") as f:
            f.write(b"caff\xe8 latte\n" * 100)
        files.view(folder + "/l.txt")
        check("latin-1 shown", shown[-1][:2], ["caff? latte", "caff? latte"])

        def no_memory(data):
            raise MemoryError

        files.decode = no_memory
        keys_from(b" ")
        check("out of memory stays in Files", (files.view(folder + "/t.txt"), len(shown)), (False, 2))
    finally:
        pu._key_byte = _REAL_KEY_BYTE
        files.pager, files.decode = saved
        if hasattr(files, "open"):
            del files.open
        _rmtree(folder)


# snake


class FakeRng:
    def __init__(self, values):
        self.values = list(values)

    def randint(self, low, high):
        return self.values.pop(0) if self.values else low


def test_snake_moves_and_turns():
    import snake

    s = snake.new_game(10, 5, FakeRng([0, 0]))
    check("start", s["snake"], [(3, 2), (4, 2), (5, 2)])
    check("food placed", s["food"], (0, 0))
    check("moved", snake.step(s), "moved")
    check("head right", s["snake"][-1], (6, 2))
    check("length kept", len(s["snake"]), 3)
    snake.turn(s, (-1, 0))
    snake.step(s)
    check("reverse ignored", s["snake"][-1], (7, 2))
    snake.turn(s, (0, -1))
    snake.turn(s, (-1, 0))
    snake.step(s)
    check("first queued turn", s["snake"][-1], (7, 1))
    snake.step(s)
    check("second queued turn", s["snake"][-1], (6, 1))
    check("cells match", s["cells"], set(s["snake"]))


def test_snake_eats_and_dies():
    import snake

    s = snake.new_game(10, 5, FakeRng([6, 2]))
    check("food ahead", s["food"], (6, 2))
    check("ate", snake.step(s), "ate")
    check("grew", len(s["snake"]), 4)
    check("score", s["score"], 1)
    for _ in range(3):
        snake.step(s)
    check("wall", snake.step(s), "dead")
    loop = snake.new_game(5, 5, FakeRng([4, 4]))
    loop["snake"] = [(1, 1), (2, 1), (2, 2), (1, 2)]
    loop["cells"] = set(loop["snake"])
    loop["dir"] = (0, -1)
    check("tail cell is free", snake.step(loop), "moved")
    bite = snake.new_game(5, 5, FakeRng([4, 4]))
    bite["snake"] = [(0, 0), (1, 0), (2, 0), (2, 1), (1, 1)]
    bite["cells"] = set(bite["snake"])
    bite["dir"] = (0, -1)
    check("own body", snake.step(bite), "dead")


def test_snake_food_placement():
    import snake

    s = snake.new_game(4, 1, FakeRng([1, 0] * 60))
    check("crowded: first free cell", s["food"], (3, 0))
    s["cells"].add((3, 0))
    check("full field", snake.place_food(s), None)
    check("no food left", s["food"], None)


def test_snake_turn_queue():
    import snake

    R, U, L, D = (1, 0), (0, -1), (-1, 0), (0, 1)
    s = snake.new_game(10, 5, FakeRng([0, 0]))
    for heading in (R, R, U):  # right, right, up within one step
        snake.turn(s, heading)
    snake.step(s)
    check("repeats take no slot: the up lands", s["snake"][-1], (5, 1))
    s = snake.new_game(10, 5, FakeRng([0, 0]))
    for heading in (U, D, U, L):
        snake.turn(s, heading)
    check("reverse and repeat of the queued turn dropped", s["turns"], [U, L])
    snake.step(s)
    snake.step(s)
    check("up then left", s["snake"][-1], (4, 1))


# music


def _wav_bytes(channels, rate, bits, data, extra=b"", kind=1, ext=False):
    fmt = (
        (0xFFFE if ext else kind).to_bytes(2, "little")
        + channels.to_bytes(2, "little")
        + rate.to_bytes(4, "little")
        + (rate * channels * bits // 8).to_bytes(4, "little")
        + (channels * bits // 8).to_bytes(2, "little")
        + bits.to_bytes(2, "little")
    )
    if ext:
        fmt += (22).to_bytes(2, "little") + bytes(6) + kind.to_bytes(2, "little") + bytes(14)
    body = b"WAVE" + b"fmt " + len(fmt).to_bytes(4, "little") + fmt + extra
    body += b"data" + len(data).to_bytes(4, "little") + data
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def test_wav_info():
    import music

    data = bytes(range(16))
    listc = b"LIST" + (5).to_bytes(4, "little") + b"abcde" + b"\x00"  # odd size, padded
    fllr = b"FLLR" + (8).to_bytes(4, "little") + bytes(8)  # afconvert's filler
    wav = _wav_bytes(2, 22050, 16, data, extra=listc + fllr)
    info = music.wav_info(io.BytesIO(wav))
    check("stereo 16-bit", info[:3], (2, 22050, 16))
    check("data offset", wav[info[3] : info[3] + info[4]], data)
    check("extensible PCM", music.wav_info(io.BytesIO(_wav_bytes(1, 8000, 8, data, ext=True)))[:3], (1, 8000, 8))
    cut = _wav_bytes(1, 22050, 16, data)[:-6]
    check("cut file clamps the size", music.wav_info(io.BytesIO(cut))[4], 10)
    for name, blob in (
        ("float rejected", _wav_bytes(1, 22050, 32, data, kind=3)),
        ("24-bit rejected", _wav_bytes(1, 22050, 24, data)),
        ("not a WAV", b"ID3" + bytes(40)),
    ):
        try:
            music.wav_info(io.BytesIO(blob))
            check(name, "accepted", "ValueError")
        except ValueError:
            pass


def test_music_convert():
    import music
    from array import array

    def word(d):
        return (d << 16) | d

    pcm = b"".join(v.to_bytes(2, "little") for v in (0, 32767, 32768, 65472))  # 0, max, min, -64
    check("16-bit mono full", music.convert_reference(pcm, 4, 1, 16, 16), [word(512), word(1023), word(0), word(511)])
    check("16-bit mono half", music.convert_reference(pcm, 3, 1, 16, 8), [word(512), word(767), word(256)])
    check("8-bit stereo", music.convert_reference(bytes([255, 0]), 1, 2, 8, 16), [1020])
    check("silence at 0", music.convert_reference(pcm, 2, 1, 16, 0), [word(512), word(512)])
    data = bytes((i * 37 + 11) % 256 for i in range(4 * 64))
    for channels, bits in ((2, 16), (1, 16), (2, 8), (1, 8)):
        frames = len(data) // (channels * bits // 8)
        frames = min(frames, 64)
        out = array("I", [0] * frames)
        music.convert(data, out, frames, channels, bits, 11)
        check(
            "convert {}ch {}-bit matches reference".format(channels, bits),
            list(out),
            music.convert_reference(data, frames, channels, bits, 11),
        )
    if sys.platform == "rp2":  # no native emitter on the unix port for arm64
        check("viper compiled", music._VIPER is not None, True)


def test_music_songs():
    import music

    folder = _HERE + "/_tmp_music"
    _rmtree(folder)
    os.mkdir(folder)
    try:
        _write(folder + "/b.wav", "x" * 10)
        _write(folder + "/A.WAV", "")
        _write(folder + "/._b.wav", "junk")
        _write(folder + "/note.txt", "x")
        os.mkdir(folder + "/x.wav")
        found = music.songs((folder,))
        check("wav only, by name", [song[0] for song in found], ["A", "b"])
        check("size", found[1][2], 10)
        check("missing folder", music.songs((folder + "/nope",)), [])
    finally:
        _rmtree(folder)


# play_file on a model of the hardware, in virtual microseconds (integers:
# the same run on every interpreter): the pacer ticks at the sample rate,
# each tick moves one word of the busy DMA channel into the compare
# register, a finished channel triggers the one it chains to and queues its
# IRQ handler. Soft IRQs run while the SD driver (Python) reads, in sleeps
# and key polls, never inside the (viper) converter. Frame i of the file
# carries i in its low half, so the words that reach the speaker show every
# replay and skip.


def _swap(module, attrs):
    # Stub module attributes; returns what _unswap() puts back (None: a
    # builtin such as open or print, whose stub is deleted again)
    old = {}
    for name in attrs:
        old[name] = getattr(module, name, None)
        setattr(module, name, attrs[name])
    return old


def _unswap(module, old):
    for name in old:
        if old[name] is None:
            delattr(module, name)
        else:
            setattr(module, name, old[name])


_SIM = [None]


class _SimDMA:
    def __init__(self):
        sim = _SIM[0]
        self.channel = len(sim.dmas)
        sim.dmas.append(self)
        self.buf = None
        self.pos = self.reload = self.left = self.ctrl = 0
        self.busy = False
        self.handler = None

    @property
    def read(self):
        return self.buf

    @read.setter
    def read(self, buf):
        self.buf = buf
        self.pos = 0

    @property
    def count(self):
        return self.left

    @count.setter
    def count(self, n):
        self.reload = n

    def pack_ctrl(self, size=2, inc_read=True, inc_write=False, treq_sel=0, chain_to=0, irq_quiet=True):
        return 1 | chain_to << 1

    def config(self, read=None, write=None, count=None, ctrl=None, trigger=False):
        self.read = read
        self.count = count
        self.ctrl = ctrl
        if trigger:
            self.start(_SIM[0])

    def irq(self, handler=None, hard=False):
        self.handler = handler

    def close(self):
        if self.busy and self.ctrl & 1:
            _SIM[0].errors.append("abort of an enabled channel")

    def start(self, sim):
        if self.buf[0] & 0xFFFF != sim.heard + 1:
            sim.stale += 1  # not the next frames of the file: old samples
        self.busy = True
        self.left = self.reload

    def move(self, sim):
        if self.pos >= len(self.buf):  # READ_ADDR not reset before a chain
            if "read past a buffer" not in sim.errors:
                sim.errors.append("read past a buffer")
            self.pos = 0
        word = self.buf[self.pos]
        sim.mem[sim.cc] = word
        low = word & 0xFFFF
        if low != sim.last + 1:
            sim.jumps += 1
        sim.last = low
        sim.heard = max(sim.heard, low)
        sim.words += 1
        self.pos += 1
        self.left -= 1
        if self.left <= 0:
            self.busy = False
            sim.pending.append(self)
            chain = self.ctrl >> 1 & 0x1F
            if chain != self.channel:
                sim.dmas[chain].start(sim)


class _SimMem:
    def __getitem__(self, addr):
        return _SIM[0].mem.get(addr, 0)

    def __setitem__(self, addr, value):
        sim = _SIM[0]
        sim.mem[addr] = value
        if addr == sim.cc:
            sim.ramp.append(value)  # Python's writes of the level (the DMA's aren't kept)


class _SimMachine:
    mem32 = _SimMem()


_SimMachine.freq = lambda: 150000000
_SimMachine.PWM = lambda pin: pin
_SimMachine.Pin = lambda n: n


class _SimRp2:
    DMA = _SimDMA


class _SimWav:
    def __init__(self, data, sim):
        self.data = data
        self.sim = sim
        self.pos = 0
        self.closed = False

    def seek(self, offset, whence=0):
        self.pos = (0, self.pos, len(self.data))[whence] + offset
        return self.pos

    def tell(self):
        return self.pos

    def read(self, n):
        out = self.data[self.pos : self.pos + n]
        self.pos += len(out)
        return out

    def readinto(self, buf):
        out = self.read(len(buf))
        buf[: len(out)] = out
        for _ in range((len(out) + 63) // 64):
            self.sim.run(64000 // self.sim.kbs)
        return len(out)

    def close(self):
        self.closed = True


class _PlaySim:
    def __init__(self, rate, kbs, keys):
        import music

        self.period = 1000000 // rate  # us per sample
        self.kbs = kbs  # SD speed, bytes per ms
        self.keys = list(keys)  # (ms, key)
        self.t = 0
        self.tick = None
        self.mem = {}
        self.dmas = []
        self.pending = []
        self.errors = []
        self.ramp = []
        self.last = self.heard = -1
        self.words = self.jumps = self.stale = self.polls = 0
        self.csr = music._reg(music._PACER, music._CSR)
        self.cc = music._reg(music._AUDIO, music._CC)

    def run(self, us):
        end = self.t + us
        while self.mem.get(self.csr, 0) & 1:
            if self.tick is None:
                self.tick = self.t + self.period
            if self.tick > end:
                break
            self.t = self.tick
            self.tick += self.period
            for dma in self.dmas:
                if dma.busy and dma.ctrl & 1:
                    dma.move(self)
                    break
        else:
            self.tick = None  # pacer off
        self.t = end
        while self.pending:
            dma = self.pending.pop(0)
            if dma.handler is not None:
                dma.handler(dma)

    def sleep(self, ms):
        self.run(ms * 1000)

    def key(self):
        self.polls += 1
        self.run(300)  # an I2C read
        if self.keys and self.t >= self.keys[0][0] * 1000:
            return self.keys.pop(0)[1]
        return None


def _sim_convert(data, out, frames, channels, bits, volume):
    for i in range(frames):
        at = 4 * i
        out[i] = data[at] | data[at + 1] << 8 | data[at + 2] << 16 | data[at + 3] << 24


def _play_sim(frames, rate, kbs, keys=(), extra=None):
    # (play_file's result or exception, the model) for a 16-bit stereo file
    # of `frames`; `extra`: more module attributes to stub
    import music

    sim = _PlaySim(rate, kbs, keys)
    _SIM[0] = sim
    pcm = bytearray(4 * frames)
    for i in range(frames):
        pcm[4 * i] = i & 0xFF
        pcm[4 * i + 1] = i >> 8 & 0xFF
        pcm[4 * i + 2] = 0x23  # a right half unlike the left
        pcm[4 * i + 3] = 0x01
    wav = _SimWav(_wav_bytes(2, rate, 16, bytes(pcm)), sim)
    stubs = {
        "FRAMES": 256,
        "convert": _sim_convert,
        "open": lambda path, mode="r": wav,
        "print": lambda *args, **kw: None,
        "_sleep_ms": sim.sleep,
        "_ticks_ms": lambda: sim.t // 1000,
        "_poll_key": sim.key,
        "_sd_card": lambda: None,
        "_screen_header": lambda *args: None,
    }
    stubs.update(extra or {})
    saved = _swap(music, stubs)
    modules = {"rp2": sys.modules.get("rp2"), "machine": sys.modules.get("machine")}
    sys.modules["rp2"] = _SimRp2
    sys.modules["machine"] = _SimMachine
    sim.file = wav
    try:
        result = music.play_file("x.wav", "x")
    except Exception as error:
        result = error
    finally:
        _unswap(music, saved)
        for name in modules:
            if modules[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = modules[name]
        _SIM[0] = None
    return result, sim


def test_music_play_gaps():
    import music

    total = 10 * 256 + 37  # 4000 Hz stereo 16-bit: 16 bytes per ms
    result, sim = _play_sim(total, 4000, 200)
    stats = music.last_stats
    check("fast SD: to the end", result, "end")
    check("fast SD: every frame once, in order", (sim.words, sim.jumps), (total, 0))
    check("fast SD: no gaps", stats["gaps"], 0)
    check("fast SD: frames played", stats["frames"], total)
    check("fast SD: file closed, no channel aborted", (sim.file.closed, sim.errors), (True, []))
    result, sim = _play_sim(total, 4000, 8)  # the SD at half the data rate
    stats = music.last_stats
    check("slow SD: to the end", result, "end")
    check("slow SD: old samples replayed", sim.stale > 1 and sim.words > total, True)
    check("slow SD: every replay is a gap", stats["gaps"], sim.stale)
    check("slow SD: played time from frames read, not replays", stats["frames"], total)
    check("slow SD: no channel aborted", sim.errors, [])


def test_music_keys_ramp_memory():
    import music

    result, sim = _play_sim(10 * 256 + 37, 4000, 200, keys=[(300, "q")])
    check("q stops", result, "quit")
    check("q: file closed, no channel aborted", (sim.file.closed, sim.errors), (True, []))
    check("keys polled every 50 ms at most", sim.polls <= sim.t // 50000 + 1, True)
    check("ramp up: both sides to the middle", (sim.ramp[1], sim.ramp[33]), (0, 512 << 16 | 512))
    down = sim.ramp[-33:]
    check("ramp down from both sides' levels", (down[0], down[-1]), (0x0123 << 16 | sim.last, 0))
    rights = [word >> 16 for word in down]
    steps = [a - b for a, b in zip(rights, rights[1:])]
    check("right side slides down", (min(steps) >= 0, max(steps) <= 10), (True, True))

    def no_memory(n):
        raise MemoryError("memory allocation failed")

    result, sim = _play_sim(1000, 4000, 200, extra={"bytearray": no_memory})
    check("no memory: raised, file closed", (isinstance(result, MemoryError), sim.file.closed), (True, True))
    out = []
    waits = []
    picks = [("enter", 0), ("q", 0)]

    def play_file(path, name):
        raise MemoryError("memory allocation failed")

    saved = _swap(
        music,
        {
            "songs": lambda: [("a", "/sd/music/a.wav", 10)],
            "_pick": lambda title, labels, hints=None, pos=0: picks.pop(0),
            "play_file": play_file,
            "_wait_key": lambda *args: waits.append(1),
            "_clear_screen": lambda: None,
            "print": lambda *args, **kw: out.append(" ".join(str(a) for a in args)),
        },
    )
    try:
        check("player stays up on MemoryError", music.player(), None)
        check("told, then back to the list", ("Can't play: memory" in out[0], waits, picks), (True, [1], []))
    finally:
        _unswap(music, saved)


# clock


class _ClockTime:
    # clock_ntp's time module in virtual milliseconds
    ms = 0

    def time(self):
        return _ClockTime.ms // 1000

    def gmtime(self, secs):
        import time

        return time.gmtime(secs)


def test_clock_live():
    # Across the end of EU summer time (2026-10-25 01:00 UTC) and two
    # minute changes.
    import time
    import clock_ntp

    start = 1792889998 * 1000 + 500  # 00:59:58.5 UTC
    switch = 1792890000 * 1000
    stop = switch + 60200  # a key at 01:01:00.2
    Clock = _ClockTime
    Clock.ms = start

    def sleep(ms):
        Clock.ms += ms

    def poll():
        return "q" if Clock.ms >= stop else None

    def read_key(timeout_ms=None):  # the old loop's wait
        sleep(timeout_ms or 0)
        return poll()

    reads = []

    def offset():
        reads.append(Clock.ms)
        return 2 if Clock.ms < switch else 1

    drawn = []
    out = []
    saved = _swap(
        clock_ntp,
        {
            "time": Clock(),
            "_get_offset": offset,
            "_sleep_ms": sleep,
            "_poll_key": poll,
            "_read_key": read_key,
            "_block_rows": lambda rows, row, col, fg: drawn.append((Clock.ms, rows)),
            "_screen_header": lambda title: out.append("<header>"),
            "_title_bar": lambda title: "<" + title + " bar>",
            "print": lambda *args, **kw: out.append("".join(str(a) for a in args)),
        },
    )
    try:
        check("live returns", clock_ntp.live(), True)
    finally:
        _unswap(clock_ntp, saved)
    text = "".join(out)

    def digits(secs):
        t = time.gmtime(secs)
        return clock_ntp.big_lines("{:02d}:{:02d}:{:02d}".format(t[3], t[4], t[5]))

    check("zone read once a minute", len(reads), 3)
    check("one draw a second", len(drawn), 63)
    check("each within 50 ms of its second", max(ms % 1000 for ms, rows in drawn[1:]) < 50, True)
    check("summer time to its last second", drawn[1][1], digits(switch // 1000 - 1 + 7200))
    check("winter time from the switch", drawn[2][1], digits(switch // 1000 + 3600))
    check("title bar repainted each minute", text.count("\x1b[1;1H<Clock bar>"), 2)
    check("date line only when it changes", text.count("\x1b[3;1H"), 2)
    check("hint printed once", text.count("any key"), 1)


def test_clock_zone_saves():
    import clock_ntp

    saves = []
    ok = [True]
    out = []

    def save(data):
        saves.append(dict(data))
        return ok[0]

    saved = _swap(
        clock_ntp,
        {
            "_load_config": lambda: {"utc_offset": 1, "dst": "eu"},
            "_save_config": save,
            "print": lambda *args, **kw: out.append(" ".join(str(a) for a in args)),
        },
    )
    try:
        check("same offset: no flash write", (clock_ntp.set_utc_offset(1), saves), (True, []))
        check("same summer time: no flash write", (clock_ntp.set_dst(True), saves), (True, []))
        ok[0] = False
        check("failed save reported", (clock_ntp.set_utc_offset(0), out[-1]), (False, "UTC offset not saved."))
        check("failed DST save reported", (clock_ntp.set_dst(False), out[-1]), (None, "EU DST not saved."))
        ok[0] = True
        check("new offset saved", (clock_ntp.set_utc_offset(0), saves[-1]), (True, {"utc_offset": 0, "dst": "eu"}))
        check("summer time off saved", (clock_ntp.set_dst(False), saves[-1]), (False, {"utc_offset": 1}))
    finally:
        _unswap(clock_ntp, saved)


# system


class _KeyPresses:
    # _key_byte() for the key test: a press's bytes come together, then a
    # pause (a read with a timeout gets None)
    def __init__(self, presses):
        self.presses = list(presses)
        self.tail = []

    def __call__(self, timeout_ms=None):
        if timeout_ms is not None:
            return self.tail.pop(0) if self.tail else None
        press = self.presses.pop(0)
        self.tail = list(press[1:])
        return press[0]


class _LogFile:
    writes = []

    def __init__(self, path, mode):
        self.path = path
        self.mode = mode

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, text):
        _LogFile.writes.append((self.path, self.mode, text))
        return len(text)

    def flush(self):
        pass

    def close(self):
        pass


def test_status_key_log_batches():
    import sys_status

    out = []
    saved = _swap(
        sys_status,
        {
            "open": _LogFile,
            "print": lambda *args, **kw: out.append(" ".join(str(a) for a in args)),
            "screen_header": lambda title: None,
        },
    )
    del _LogFile.writes[:]
    try:
        pu._key_byte = _KeyPresses([b"a", b"\x1b[A", b"q"])
        sys_status.keys()
        log = "61               'a'\n1b 5b 41         'up'\n71               'q'\n"
        check("one write at the end", _LogFile.writes, [("keylog.txt", "a", log)])
        del _LogFile.writes[:]
        pu._key_byte = _KeyPresses([b"x"] * 44 + [b"q"])
        sys_status.keys()
        check("a write per 20 keys", [w[2].count("\n") for w in _LogFile.writes], [20, 20, 5])
        del _LogFile.writes[:]
        pu._key_byte = _KeyPresses([b"q"])
        sys_status.keys(log=False)
        check("no log, no write", _LogFile.writes, [])
    finally:
        pu._key_byte = _REAL_KEY_BYTE
        _unswap(sys_status, saved)


def test_status_colour_samples():
    import sys_status

    out = []
    saved = _swap(
        sys_status,
        {
            "print": lambda *args, **kw: out.append(" ".join(str(a) for a in args)),
            "screen_header": lambda title: None,
        },
    )
    try:
        sys_status.colors()
    finally:
        _unswap(sys_status, saved)
    black = [n for n in range(16) if pu.paint(" sample ", pu.BLACK, n) in out[n]]
    white = [n for n in range(16) if pu.paint(" sample ", pu.BWHITE, n) in out[n]]
    check("black text on the light colours", black, [7, 10, 11, 13, 14, 15])
    check("white on the rest", white, [0, 1, 2, 3, 4, 5, 6, 8, 9, 12])


def test_status_text_battery():
    saved = (pu.battery, pu.wifi_connected, pu.clock_synced, pu.utc_offset_hours)
    try:
        pu.battery = lambda: (87, True)
        pu.wifi_connected = lambda: True
        pu.clock_synced = lambda: False
        pu.utc_offset_hours = lambda: 0
        pu.refresh_status()
        check("battery labelled", pu.status_text(), "WiFi  Bat 87%+")
        pu.battery = lambda: None
        pu.refresh_status()
        check("no reading, no battery", pu.status_text(), "WiFi")
    finally:
        pu.battery, pu.wifi_connected, pu.clock_synced, pu.utc_offset_hours = saved
        pu.refresh_status()


def test_menu_imports():
    import menu

    check("menu apps", len(menu._APPS), 13)
    check("one key each", len(menu._KEY_INDEX), len(menu._APPS))
    check("new keys", [menu._KEY_INDEX[k] for k in ("0", "m", "a", "s")], [9, 10, 11, 12])
    check("every app described", sorted(menu._ABOUT), sorted(app[0] for app in menu._APPS))


# ── gfx ─────────────────────────────────────────

import gfx


class FakeDisplay:
    def __init__(self):
        self.calls = []

    def fill_rect(self, x, y, w, h, c):
        self.calls.append(("fill_rect", x, y, w, h, c))

    def hline(self, x, y, w, c):
        self.calls.append(("hline", x, y, w, c))

    def vline(self, x, y, h, c):
        self.calls.append(("vline", x, y, h, c))

    def text(self, s, x, y, c):
        self.calls.append(("text", s, x, y, c))

    def blit(self, src, x, y, key=-1, palette=None):
        self.calls.append(("blit", x, y))

    def pixel(self, x, y, c=None):
        return 0


def _with_display(body):
    saved = (gfx.surface, gfx._terminal, gfx._clear_screen, gfx._status_text, gfx._move_up)
    fake = FakeDisplay()
    vt = FakeVt()
    try:
        gfx.surface = lambda: fake
        gfx._terminal = lambda: vt
        gfx._clear_screen = lambda: vt.drawn.append("<clear>")
        gfx._status_text = lambda: "12:34  WiFi"
        gfx._move_up = lambda fb, dst, src, n: fake.calls.append(("move_up", dst, src, n))
        body(fake, vt)
    finally:
        gfx.surface, gfx._terminal, gfx._clear_screen, gfx._status_text, gfx._move_up = saved
        gfx._FB[0] = None
        pu._CONSOLE[0] = None


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

    _with_display(body)


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

    _with_display(body)


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

    _with_display(run)


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
        check("readline moves back", con.col, 1)
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
        check("cursor erased first", fake.calls[1], ("fill_rect", 1, 313, 5, 2, 0))

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


def test_console_failure_falls_back():
    class Broken:
        def write(self, buf):
            raise ValueError("bug")

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
