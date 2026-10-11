# pico_net: the toolkit's HTTP client, against a scripted server (no sockets).
try:
    from harness import *
except ImportError:
    pass  # on the device tools/test_device.sh puts harness.py in front

import pico_net


class FakeConn:
    # one server answer per connection, read the way pico_net reads sockets
    def __init__(self, answer):
        self.data = answer
        self.pos = 0
        self.sent = b""
        self.closed = False

    def write(self, data):
        self.sent += bytes(data)
        return len(data)

    def readline(self):
        nl = self.data.find(b"\n", self.pos)
        end = len(self.data) if nl < 0 else nl + 1
        line = self.data[self.pos : end]
        self.pos = end
        return line

    def read(self, n):
        chunk = self.data[self.pos : self.pos + n]
        self.pos += len(chunk)
        return chunk

    def close(self):
        self.closed = True


def serve(answers, body):
    # body(conns) with pico_net._connect answering from `answers` in turn
    conns = []
    saved = pico_net._connect

    def connect(scheme, host, port, timeout, verify):
        conn = FakeConn(answers.pop(0))
        conn.where = (scheme, host, port, timeout, verify)
        conns.append(conn)
        return conn

    pico_net._connect = connect
    try:
        body(conns)
    finally:
        pico_net._connect = saved


def answer(status, headers=(), body=b""):
    head = "HTTP/1.1 {} X\r\n".format(status) + "".join("{}: {}\r\n".format(k, v) for k, v in headers)
    return head.encode() + b"\r\n" + body


def test_get_and_json():
    def body(conns):
        r = pico_net.get("https://api.example.com/v1/x?a=1", timeout=7)
        check("status", r.status_code, 200)
        check("headers lowercase", r.headers.get("content-type"), "application/json")
        check("json", r.json(), {"t": 1})
        check("closed after reading", conns[0].closed, True)
        sent = conns[0].sent.decode()
        check("request line", sent.split("\r\n")[0], "GET /v1/x?a=1 HTTP/1.0")
        check("host, agent, close", ("Host: api.example.com" in sent, "User-Agent: PicoCalc" in sent, "Connection: close" in sent), (True, True, True))
        check("timeout, no check", conns[0].where, ("https", "api.example.com", 443, 7, False))

    serve([answer(200, [("Content-Type", "application/json"), ("Content-Length", "7")], b'{"t":1}')], body)


def test_post_json_bytes():
    def body(conns):
        pico_net.post("http://10.0.0.5:8080/chat", json={"q": "caff\xe8"})
        sent = conns[0].sent
        payload = sent.split(b"\r\n\r\n", 1)[1]
        import json as _json

        check("json body", _json.loads(payload), {"q": "caff\xe8"})
        check("length counts bytes", ("Content-Length: {}".format(len(payload))).encode() in sent, True)
        check("json type", b"Content-Type: application/json" in sent, True)
        check("port in host", b"Host: 10.0.0.5:8080" in sent, True)

    serve([answer(200)], body)


def test_redirects():
    def same_origin(conns):
        r = pico_net.get("https://a.example.com/old", headers={"Authorization": "Bearer k"}, timeout=5)
        check("followed", r.status_code, 200)
        check("same origin keeps the key", b"Authorization: Bearer k" in conns[1].sent, True)
        check("first closed", conns[0].closed, True)
        check("relative path", conns[1].sent.split(b"\r\n")[0], b"GET /new HTTP/1.0")
        check("same timeout", conns[1].where[3], 5)

    serve([answer(301, [("Location", "/new")]), answer(200)], same_origin)

    def other_host(conns):
        pico_net.post("https://a.example.com/x", data=b"body", headers={"X-Auth-Token": "k", "Accept": "*/*"})
        check("303 becomes GET", conns[1].sent.split(b"\r\n")[0], b"GET /y HTTP/1.0")
        check("no key to another host", b"X-Auth-Token" in conns[1].sent, False)
        check("other headers kept", b"Accept: */*" in conns[1].sent, True)
        check("no body", conns[1].sent.endswith(b"\r\n\r\n"), True)
        check("host", conns[1].where[1], "b.example.com")

    serve([answer(303, [("Location", "https://b.example.com/y")]), answer(200)], other_host)

    def downgrade(conns):
        pico_net.get("https://a.example.com/x", headers={"Authorization": "Bearer k"})
        check("https -> http drops it", b"Authorization" in conns[1].sent, False)
        check("plain http", conns[1].where[0], "http")

    serve([answer(302, [("Location", "http://a.example.com/x")]), answer(200)], downgrade)

    def loop(conns):
        try:
            pico_net.get("http://a.example.com/x")
            got = "no error"
        except ValueError as error:
            got = str(error)
        check("too many", (got, len(conns)), ("too many redirects", 4))

    serve([answer(302, [("Location", "/x")])] * 4, loop)


def test_join_url():
    base = "https://h.example.com:8443/a/b?c=1"
    check("absolute", pico_net.join_url(base, "http://x.org/y"), "http://x.org/y")
    check("root", pico_net.join_url(base, "/z"), "https://h.example.com:8443/z")
    check("scheme relative", pico_net.join_url(base, "//cdn.org/f"), "https://cdn.org/f")
    check("relative", pico_net.join_url(base, "d"), "https://h.example.com:8443/a/d")
    check("default port", pico_net.join_url("http://h.org/x", "/y"), "http://h.org/y")
    check("split", pico_net.split_url("https://H.Org:444/p"), ("https", "h.org", 444, "p"))


def test_caps_and_refusals():
    def chunked(conns):
        try:
            pico_net.get("http://a.org/")
            got = "read"
        except ValueError as error:
            got = str(error)
        check("chunked refused", got, "chunked responses not supported")

    serve([answer(200, [("Transfer-Encoding", "chunked")], b"5\r\nhello\r\n0\r\n\r\n")], chunked)

    def big(conns):
        r = pico_net.get("http://a.org/", max_bytes=10)
        try:
            r.content
            got = "read"
        except ValueError as error:
            got = str(error)
        check("declared too big", got, "response too big")
        r = pico_net.get("http://a.org/", max_bytes=10)
        try:
            r.content
            got = "read"
        except ValueError as error:
            got = str(error)
        check("streamed too big", got, "response too big")
        check("raw left for streaming", pico_net.get("http://a.org/").raw.read(5), b"hello")

    serve(
        [answer(200, [("Content-Length", "11")], b"hello world"), answer(200, (), b"x" * 50), answer(200, (), b"hello")],
        big,
    )
    try:
        pico_net.get("http://feed.example.com/", headers={"X-Auth-Token": "k"})
        got = "sent"
    except ValueError as error:
        got = str(error)
    check("no key over plain http to a public host", got, "refused: a key over plain http to feed.example.com")


def test_verify_policy():
    key = {"Authorization": "Bearer k"}
    table = (
        ("openrouter.ai", key, None, True),
        ("openrouter.ai", {}, None, False),
        ("192.168.1.20", key, None, False),
        ("172.20.0.1", key, None, False),
        ("172.40.0.1", key, None, True),
        ("evo-x2", key, None, False),
        ("nas.local", key, None, False),
        ("router.home.arpa", key, None, False),
        ("api.example.com", {"x-auth-token": "k"}, None, True),
        ("example.com", {}, True, True),
        ("example.com", key, False, False),
    )
    for host, headers, verify, want in table:
        check("verify " + host, pico_net.must_verify(host, headers, verify), want)

    def checked(conns):
        pico_net.get("https://openrouter.ai/api", headers=key)
        pico_net.get("https://feeds.bbci.co.uk/news.xml")
        check("credential checked, feed not", [c.where[4] for c in conns], [True, False])

    serve([answer(200), answer(200)], checked)


class FakeTls:
    PROTOCOL_TLS_CLIENT = 2
    CERT_NONE = 0
    CERT_REQUIRED = 2

    def __init__(self, error=None):
        self.error = error
        self.contexts = []

    def SSLContext(self, protocol):
        ctx = FakeContext(self)
        self.contexts.append(ctx)
        return ctx


class FakeContext:
    def __init__(self, tls):
        self.tls = tls
        self.verify_mode = None
        self.loaded = []

    def load_verify_locations(self, *args, **kwargs):
        if kwargs:
            raise TypeError("function doesn't take keyword arguments")  # as on the device
        self.loaded.append(len(args[0]))

    def wrap_socket(self, sock, server_hostname=None):
        if self.tls.error:
            raise self.tls.error
        return ("wrapped", sock, server_hostname)


def test_tls_contexts():
    saved = sys.modules.get("tls")
    fake = FakeTls()
    sys.modules["tls"] = fake
    try:
        check("checked", pico_net._wrap("s", "openrouter.ai", True), ("wrapped", "s", "openrouter.ai"))
        check("required, 4 roots", (fake.contexts[0].verify_mode, fake.contexts[0].loaded), (2, [1371, 525, 1391, 543]))
        pico_net._wrap("s", "feeds.example.com", False)
        check("unchecked", (fake.contexts[1].verify_mode, fake.contexts[1].loaded), (0, []))
        fake.error = ValueError("\nThe certificate is not correctly signed by the trusted CA\n")
        try:
            pico_net._wrap("s", "x.org", True)
            got = "ok"
        except ValueError as error:
            got = str(error)
        check("named", got.startswith("certificate check failed: The certificate is not"), True)
    finally:
        if saved is None:
            sys.modules.pop("tls", None)
        else:
            sys.modules["tls"] = saved


def test_clock_rule():
    import pico_utils

    class FakeNtp:
        tries = 0

        def sync_quiet(self, timeout=2):
            FakeNtp.tries += 1
            return False

    saved = (pico_utils.clock_synced, sys.modules.get("clock_ntp"))
    pico_utils.clock_synced = lambda: False
    sys.modules["clock_ntp"] = FakeNtp()
    try:
        try:
            pico_net._connect("https", "openrouter.ai", 443, 5, True)
            got = "connected"
        except OSError as error:
            got = str(error)
        check("never unchecked", got, "clock not set: can't check the certificate")
        check("NTP tried", FakeNtp.tries, 1)
    finally:
        pico_utils.clock_synced = saved[0]
        if saved[1] is None:
            sys.modules.pop("clock_ntp", None)
        else:
            sys.modules["clock_ntp"] = saved[1]


run(globals())
