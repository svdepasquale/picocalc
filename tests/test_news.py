try:
    from harness import *  # noqa
except ImportError:  # on the device tools/test_device.sh puts harness.py in front
    pass

# Host checks for rss_news, its Miniflux source and notes.
# Run from the repo root with either interpreter:
#   python3 tests/test_news.py
#   micropython tests/test_news.py


# ── rss_news ────────────────────────────────────

import rss_news


class FakeRaw:
    def __init__(self, data):
        self.f = io.BytesIO(data)

    def readinto(self, buf):
        return self.f.readinto(buf)


class FakeResponse:
    def __init__(self, data):
        self.raw = FakeRaw(data)


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
    rss_news.CONFIG_FILE = TMP  # defaults, not a device's saved feeds
    rm_tmp(TMP)
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
        rm_tmp(TMP)
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
    rss_news.CONFIG_FILE = TMP
    rss_news._paged_lines = lambda lines, page_lines=8: pages.append(lines)
    try:
        url = "https://example.com/" + "a" * 80
        pu.save_json(TMP, {"feeds": [{"name": "Lungo", "url": url}], "preview_chars": 110, "items_per_feed": 2})
        rss_news.feeds()
    finally:
        rss_news.CONFIG_FILE, rss_news._paged_lines = saved
        rm_tmp(TMP)
    check("feed urls fit", max(len(x) for x in pages[0]) <= pu.DISPLAY_WIDTH, True)


def test_latest_stops_between_feeds():
    # q (or Ctrl+C) between feeds stops and keeps what came; the old list
    # is dropped before the first feed's buffer
    saved_config = rss_news.CONFIG_FILE
    rss_news.CONFIG_FILE = TMP  # the three default feeds
    rm_tmp(TMP)
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
        rm_tmp(TMP)


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


# ── rss_news: Miniflux ──────────────────────────


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


_MF_BODY = (
    b'{"total": 7, "entries": [{"id": 11, "title": "Uno &amp; due",'
    b' "url": "https://a", "content": "<p>Testo <b>forte</b></p>",'
    b' "published_at": "2026-10-04T08:15:00Z", "feed": {"title": "Blog"}}]}'
)


def _with_miniflux(fn):
    saved = (rss_news.MF_CONFIG_FILE, rss_news.check_wifi, rss_news._http_module, rss_news._LAST_ITEMS)
    rss_news.MF_CONFIG_FILE = TMP
    rss_news.check_wifi = lambda: True
    try:
        pu.save_json(TMP, {"url": "https://feed.example", "token": "k"})
        fn()
    finally:
        (rss_news.MF_CONFIG_FILE, rss_news.check_wifi, rss_news._http_module, rss_news._LAST_ITEMS) = saved
        rm_tmp(TMP)


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
    notes.DATA_FILE = TMP
    notes._NOTES = []
    printed = []
    notes.print = lambda *args, **kw: printed.append(" ".join([str(a) for a in args]))
    try:
        rm_tmp(TMP)
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
        rm_tmp(TMP)


run(globals())
