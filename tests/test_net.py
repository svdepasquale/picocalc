try:
    from harness import *  # noqa
except ImportError:  # on the device tools/test_device.sh puts harness.py in front
    pass

# Host checks for wifi_manager, openrouter_ai, weather and pico_utils' network helpers.
# Run from the repo root with either interpreter:
#   python3 tests/test_net.py
#   micropython tests/test_net.py


# ── pico_utils: network helpers ─────────────────


class FakeRequests:
    def __init__(self, reject_timeout=False):
        self.calls = []
        self.reject_timeout = reject_timeout

    def request(self, method, url, **kw):
        if self.reject_timeout and "timeout" in kw:
            raise TypeError("unexpected keyword argument 'timeout'")
        self.calls.append((method, url, kw))
        return "resp"


def test_http_request_timeout():
    req = FakeRequests()
    pu.http_request(req, "GET", "http://x", headers={"a": "b"})
    check("timeout passed", req.calls[0][2].get("timeout"), pu.HTTP_TIMEOUT)
    old = FakeRequests(reject_timeout=True)
    check("old urequests fallback", pu.http_request(old, "GET", "http://x"), "resp")
    check("fallback drops timeout", "timeout" in old.calls[0][2], False)


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


def test_stream_printer_keeps_indent():
    out = []
    printer = ai._StreamPrinter(width=20, out=out.append)
    printer("def f(x):\n  ")  # an indent split across two deltas
    printer("  if x:\n        return  1")
    printer.close()
    check("indentation streamed", "".join(out), "def f(x):\n    if x:\n        return 1\n")


_SSE_OK = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'


def _with_ai(config, body):
    # ask() against a scripted server; returns what openrouter_ai printed
    saved = (ai.CONFIG_FILE, ai.check_wifi, ai._http_module)
    out = []
    ai.CONFIG_FILE = TMP
    ai.check_wifi = lambda: True
    ai.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
    try:
        pu.save_json(TMP, config)
        body()
    finally:
        ai.CONFIG_FILE, ai.check_wifi, ai._http_module = saved
        del ai.print
        ai._HISTORY[:] = []
        ai._LAST_RESPONSES[:] = []
        rm_tmp(TMP)
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
        weather.CONFIG_FILE = TMP
        check("saved", weather.set_location(45.5, 9.25, "Milano"), True)
        check("stored", weather._get_location(), (45.5, 9.25, "Milano"))
        weather.CONFIG_FILE = HERE + "/no_such_dir/weather.json"
        check("failed save is no success", weather.set_location(41.75, 12.5, "Roma"), False)
        check("said so", "Location not saved." in out, True)
    finally:
        weather.CONFIG_FILE = saved
        del weather.print
        rm_tmp(TMP)


_WEATHER_STUBS = ("CONFIG_FILE", "check_wifi", "_http_module", "_ticks_ms")


def _with_weather(body, online=True):
    # body() against a scripted server; returns what weather printed
    saved = [getattr(weather, name) for name in _WEATHER_STUBS]
    out = []
    weather.CONFIG_FILE = TMP
    weather.check_wifi = lambda: online
    weather.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
    weather._LAST_FETCH[:] = [None, 0, None]
    try:
        body()
    finally:
        for name, value in zip(_WEATHER_STUBS, saved):
            setattr(weather, name, value)
        del weather.print
        weather._LAST_FETCH[:] = [None, 0, None]
        rm_tmp(TMP)
    return out


def test_weather_network_error_in_words():
    def run():
        weather._http_module = lambda: FailingRequests(OSError(-2))
        check("no weather", weather.now(), None)

    check("said in words", "Err: DNS failed" in _with_weather(run), True)


_WEATHER_BODY = (
    b'{"current_weather": {"temperature": 18.5, "windspeed": 7.2, "winddirection": 200,'
    b' "weathercode": 2, "time": "2026-10-07T10:00"},'
    b' "daily": {"time": ["2026-10-07", "2026-10-08", "2026-10-09"],'
    b' "temperature_2m_max": [21.5, 19.5, 17.0], "temperature_2m_min": [12.0, 11.5, 9.0],'
    b' "weathercode": [2, 61, 3]}}'
)


def test_weather_one_request():
    # the menu's screen calls now() then forecast(3): one TLS handshake
    clock = [0]
    req = ScriptedRequests([HttpResponse(200, _WEATHER_BODY) for _ in range(6)])

    def run():
        weather._ticks_ms = lambda: clock[0]
        weather._http_module = lambda: req
        check("now", weather.now()["temp"], 18.5)
        check("forecast", [d["max"] for d in weather.forecast(3)], [21.5, 19.5, 17.0])
        check("one request", len(req.calls), 1)
        query = req.calls[0][1]
        check("one query for both", ("current_weather=true" in query, "forecast_days=3" in query), (True, True))
        weather.now()  # the screen's "r": always a new request
        check("refresh asks again", len(req.calls), 2)
        weather.forecast(5)
        check("more days ask again", len(req.calls), 3)
        clock[0] += weather.REUSE_MS
        weather.forecast(3)
        check("stale asks again", len(req.calls), 4)
        weather.set_location(45.5, 9.25, "Milano")
        weather.forecast(3)
        check("new place asks again", len(req.calls), 5)
        both = weather.report()
        check("report: both, one request", (both["now"]["desc"], len(both["days"]), len(req.calls)), ("Partly cloudy", 3, 6))

    _with_weather(run)


def test_weather_offline_once():
    checks = []

    def run():
        weather._http_module = lambda: object()
        weather.check_wifi = lambda: checks.append(1)  # None: offline
        check("now offline", weather.now(), None)
        check("forecast offline", weather.forecast(3), None)

    _with_weather(run)
    check("one No WiFi", len(checks), 1)


# ── wifi_manager: saved / forget ────────────────

import wifi_manager


def test_wifi_saved_forget():
    saved = wifi_manager.CREDENTIALS_FILE
    wifi_manager.CREDENTIALS_FILE = TMP
    try:
        wifi_manager.save_credentials({"home": "p1", "office": "p2"})
        check("saved sorted", wifi_manager.saved(), ["home", "office"])
        check("forget by index", wifi_manager.forget(2), True)
        check("left", wifi_manager.saved(), ["home"])
        check("forget by name", wifi_manager.forget("home"), True)
        check("unknown", wifi_manager.forget("nope"), False)
    finally:
        wifi_manager.CREDENTIALS_FILE = saved
        rm_tmp(TMP)


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
    w.CREDENTIALS_FILE = TMP
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
        rm_tmp(TMP)
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
        wifi_manager.CREDENTIALS_FILE = HERE + "/no_such_dir/wifi.json"
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


run(globals())
