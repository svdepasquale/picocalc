# Host-side checks for the pure logic (parsers, formatters, helpers).
# Run from the repo root with either interpreter:
#   python3 tests/test_logic.py
#   micropython tests/test_logic.py
# No hardware needed: machine/network/picocalc are stubbed where used.

import io
import os
import sys

_HERE = __file__.rsplit("/", 1)[0] if "/" in __file__ else "."
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


def test_nav_input_interrupt():
    import builtins

    real = builtins.input

    def boom(prompt=""):
        raise KeyboardInterrupt

    builtins.input = boom
    try:
        check("ctrl-c quits pager", pu._nav_input("> "), "q")
    finally:
        builtins.input = real


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
    check("one paged call", len(pages), 1)
    check("item count", count, len(rss_news.DEFAULT_FEEDS))
    check("first line", pages[0][0], "[1] BBC World")


# ── scientific_calc ─────────────────────────────

import scientific_calc as sc


def test_format_result():
    check("small int", sc._format_result(42), "42")
    check("integral float", sc._format_result(3.0), "3")
    check("2**100 fits 30", sc._format_result(2 ** 100), "1.2676506002282294014967032e30")
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
