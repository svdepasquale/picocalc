# transfer: the Wi-Fi file server's request handling, on a fake client
# socket and a scratch folder standing in for the SD card.
try:
    from harness import *
except ImportError:
    pass  # on the device tools/test_device.sh puts harness.py in front

import transfer

ROOT = HERE + "/_tmp_sd"


class FakeClient:
    # a client socket: what it sends, then what the server writes back
    def __init__(self, data, stall=False):
        self.data = data
        self.pos = 0
        self.out = b""
        self.stall = stall

    def readline(self, limit=-1):
        end = self.data.find(b"\n", self.pos)
        end = len(self.data) if end < 0 else end + 1
        if limit >= 0:
            end = min(end, self.pos + limit)
        line = self.data[self.pos : end]
        self.pos = end
        return line

    def readinto(self, buf):
        if self.pos >= len(self.data) and self.stall:
            raise OSError(110)  # the socket timeout
        piece = self.data[self.pos : self.pos + min(len(buf), 1000)]
        buf[: len(piece)] = piece
        self.pos += len(piece)
        return len(piece)

    def write(self, data):
        self.out += bytes(data)
        return len(data)


def fresh_card():
    rm_tree(ROOT)
    os.mkdir(ROOT)
    os.mkdir(ROOT + "/music")
    write_file(ROOT + "/music/a.txt", "hello")
    write_file(ROOT + "/top.txt", "x")


def new_state(pin="123456"):
    return {"pin": pin, "root": ROOT, "strikes": 0, "log": [], "buf": bytearray(256), "stop": None, "dirty": False, "abort": lambda: False}


def ask(state, text, body=b"", stall=False):
    client = FakeClient(text.encode() + body, stall)
    status = transfer.handle(client, state)
    head, _, rest = client.out.partition(b"\r\n\r\n")
    return status, rest


def test_names_and_decoding():
    for name in ("song.wav", "Caff\xe8 1.txt", "a" * 65, "b" * 255):
        check("ok " + name[:10], transfer.valid_name(name), True)
    for name in ("", ".", "..", ".hidden", "a/b", "a\\b", "x\x1b[2J", "a" * 256, None):
        check("bad " + repr(name)[:12], transfer.valid_name(name), False)
    check("percent", transfer.unquote("a%20b+c%C3%A8"), "a b c\xe8")
    check("broken percent kept", transfer.unquote("100%x%4"), "100%x%4")
    check("query", transfer.parse_query("dir=music&name=..%2Fx&pin=1"), {"dir": "music", "name": "../x", "pin": "1"})
    check("pin compare", (transfer.same_pin("123456", "123456"), transfer.same_pin("12345", "123456"), transfer.same_pin("1234567", "123456"), transfer.same_pin(None, "123456")), (True, False, False, False))
    pin = transfer.new_pin()
    check("pin shape", (len(pin), pin.isdigit()), (6, True))


def test_head_limits():
    check("garbage", transfer.read_head(FakeClient(b"hello\r\n\r\n")), None)
    check("no newline", transfer.read_head(FakeClient(b"GET / HTTP/1.1")), None)
    big = b"GET / HTTP/1.1\r\nX: " + b"a" * 5000 + b"\r\n\r\n"
    check("head too long", transfer.read_head(FakeClient(big)), None)
    many = b"GET / HTTP/1.1\r\n" + b"".join(b"H" + str(i).encode() + b": v\r\n" for i in range(40)) + b"\r\n"
    check("too many headers", transfer.read_head(FakeClient(many)), None)
    got = transfer.read_head(FakeClient(b"PUT /api/put?dir=music HTTP/1.1\r\nX-PIN: 1\r\nContent-Length: 5\r\n\r\n"))
    check("parsed", got, ("PUT", "/api/put", "dir=music", {"x-pin": "1", "content-length": "5"}))


def test_page_pin_and_lockout():
    fresh_card()
    state = new_state()
    try:
        status, body = ask(state, "GET / HTTP/1.1\r\n\r\n")
        check("page without a PIN", (status, b"PicoCalc SD" in body), (200, True))
        check("favicon is no strike", ask(state, "GET /favicon.ico HTTP/1.1\r\n\r\n")[0], 404)
        for _ in range(transfer.MAX_STRIKES - 1):
            ask(state, "GET /api/ls?dir= HTTP/1.1\r\nX-PIN: 000000\r\n\r\n")
        check("still open", state["stop"], None)
        check("wrong PIN", ask(state, "GET /api/ls HTTP/1.1\r\n\r\n")[0], 401)
        check("closed after ten", state["stop"] is not None, True)
        check("no PIN in replies", b"123456" in body, False)
    finally:
        rm_tree(ROOT)


def test_list_get_delete():
    fresh_card()
    state = new_state()
    pin = "X-PIN: 123456\r\n"
    try:
        status, body = ask(state, "GET /api/ls?dir= HTTP/1.1\r\n" + pin + "\r\n")
        import json as _json

        data = _json.loads(body)
        check("card root", (status, data["dirs"], data["files"]), (200, ["", "music"], [["top.txt", 1]]))
        status, body = ask(state, "GET /api/get?dir=music&name=a.txt&pin=123456 HTTP/1.1\r\n\r\n")
        check("download by link", (status, body), (200, b"hello"))
        for bad in ("..", "..%2Fmusic", "%2Fetc", ".x"):
            check("folder " + bad, ask(state, "GET /api/ls?dir={} HTTP/1.1\r\n{}\r\n".format(bad, pin))[0], 404)
        check("traversal name", ask(state, "GET /api/get?dir=music&name=..%2Ftop.txt HTTP/1.1\r\n" + pin + "\r\n")[0], 400)
        check("no such file", ask(state, "GET /api/get?dir=music&name=zz HTTP/1.1\r\n" + pin + "\r\n")[0], 404)
        check("a folder is no file", ask(state, "DELETE /api/rm?dir=&name=music HTTP/1.1\r\n" + pin + "\r\n")[0], 404)
        check("delete", ask(state, "DELETE /api/rm?dir=music&name=a.txt HTTP/1.1\r\n" + pin + "\r\n")[0], 200)
        check("gone", os.listdir(ROOT + "/music"), [])
        check("query PIN only for downloads", ask(state, "DELETE /api/rm?dir=&name=top.txt&pin=123456 HTTP/1.1\r\n\r\n")[0], 401)
    finally:
        rm_tree(ROOT)


def test_uploads():
    fresh_card()
    state = new_state()
    pin = "X-PIN: 123456\r\n"
    try:
        data = b"0123456789" * 300
        head = "PUT /api/put?dir=music&name=new.bin HTTP/1.1\r\n{}Content-Length: {}\r\n\r\n".format(pin, len(data))
        check("saved", ask(state, head, data)[0], 200)
        with open(ROOT + "/music/new.bin", "rb") as f:
            check("same bytes", f.read(), data)
        head = "PUT /api/put?dir=music&name=new.bin HTTP/1.1\r\n{}Content-Length: 3\r\n\r\n".format(pin)
        check("replaced", (ask(state, head, b"abc")[0], open(ROOT + "/music/new.bin").read()), (200, "abc"))
        check("no length", ask(state, "PUT /api/put?dir=&name=x HTTP/1.1\r\n" + pin + "\r\n")[0], 411)
        check("chunked", ask(state, "PUT /api/put?dir=&name=x HTTP/1.1\r\n" + pin + "Transfer-Encoding: chunked\r\n\r\n")[0], 411)
        big = "PUT /api/put?dir=&name=x HTTP/1.1\r\n{}Content-Length: {}\r\n\r\n".format(pin, transfer.MAX_UPLOAD + 1)
        check("too large", ask(state, big)[0], 413)
        short = "PUT /api/put?dir=&name=cut.bin HTTP/1.1\r\n{}Content-Length: 100\r\n\r\n".format(pin)
        check("client stalls", ask(state, short, b"only ten..", stall=True)[0], 500)
        check("closed early", ask(state, short.replace("cut", "cut2"), b"short")[0], 500)
        check("no partial files", sorted(os.listdir(ROOT)), ["music", "top.txt"])
        stopped = new_state()
        stopped["abort"] = lambda: True
        check("q stops it", ask(stopped, head.replace("new.bin", "q.bin"), b"abc")[0], 500)
        check("nothing left", "q.bin.part" in os.listdir(ROOT + "/music") or "q.bin" in os.listdir(ROOT + "/music"), False)
        check("logged", state["log"][0].startswith("got new.bin"), True)
    finally:
        rm_tree(ROOT)



class SlowSocket:
    # a non-blocking socket as _Client sees it: readinto() gives what has
    # come (None: nothing yet, as lwIP's EAGAIN), write() takes 7 bytes a call
    def __init__(self, pieces):
        self.pieces = list(pieces)
        self.sent = b""
        self.waits = 0
        self.blocking = True

    def setblocking(self, flag):
        self.blocking = flag

    def readinto(self, view):
        if not self.pieces:
            return 0
        piece = self.pieces.pop(0)
        if piece is None:
            self.waits += 1
            return None
        n = min(len(view), len(piece))
        view[:n] = piece[:n]
        if len(piece) > n:
            self.pieces.insert(0, piece[n:])
        return n

    def write(self, data):
        self.sent += bytes(data[:7])
        return min(7, len(data))


def test_client_waits_and_deadlines():
    state = new_state()
    clock = [0]
    saved = transfer._ticks_ms
    transfer._ticks_ms = lambda: clock[0]
    try:
        sock = SlowSocket([b"GET /api/ls HTTP/1.1\r\nX-PIN: 1", None, None, b"23456\r\n\r\nbody"])
        client = transfer._Client(sock, state)
        check("non-blocking", sock.blocking, False)
        got = transfer.read_head(client)
        check("head across waits", (got[0], got[3]["x-pin"], sock.waits), ("GET", "123456", 2))
        view = memoryview(bytearray(10))
        check("the rest after the head", (client.readinto(view), bytes(view[:4])), (4, b"body"))
        check("write in pieces", (client.write(b"0123456789abcdef"), sock.sent), (16, b"0123456789abcdef"))
        slow = transfer._Client(SlowSocket([b"GET / HT"] + [None] * 100), state)
        clock[0] += transfer.HEAD_MS + 1
        try:
            transfer.read_head(slow)
            got = "read"
        except OSError as error:
            got = str(error)
        check("a trickled head is dropped", got, "request too slow")
        state["abort"] = lambda: True
        quit = transfer._Client(SlowSocket([None, b"x"]), state)
        try:
            quit.readinto(memoryview(bytearray(4)))
            got = "read"
        except OSError as error:
            got = str(error)
        check("q while a client waits", (got, state["stop"]), ("stopped", ""))
    finally:
        transfer._ticks_ms = saved


run(globals())
