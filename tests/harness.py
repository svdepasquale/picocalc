# Shared part of the host-side checks. One file per area imports it:
#   test_core.py    pico_utils, gfx and its console, menu, go
#   test_net.py     wifi_manager, openrouter_ai, weather, pico_utils' network helpers
#   test_news.py    rss_news, Miniflux, notes
#   test_tools.py   files, apps, scientific_calc
#   test_hw.py      music, synthesizer, snake, clock_ntp, sys_status
# Run one from the repo root with either interpreter:
#   python3 tests/test_core.py
#   micropython tests/test_core.py
# python3 tests/run_all.py runs them all. On the device tools/test_device.sh puts
# this file in front of each area file and runs the result (the device compiles
# a script in RAM: one 110 KB file raised MemoryError), so nothing here may rely
# on being imported. The area files take its names with `import *`, which skips
# the ones that start with an underscore: whatever they use here is public.
# No hardware needed: machine/network/picocalc are stubbed where used.

# io, os, sys and pu reach the area files through import *
import io
import os
import sys

try:
    HERE = __file__.rsplit("/", 1)[0] if "/" in __file__ else "."
except NameError:  # mpremote run: executed on the device, modules in /
    HERE = "."
sys.path.insert(0, HERE + "/..")

import pico_utils as pu

TMP = HERE + "/_tmp_test.json"
FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(name)
        print("FAIL", name, "\n  got: ", repr(got), "\n  want:", repr(want))


# scratch files and folders, all tests/_tmp_* (git ignores them)


def rm_tmp(path):
    for p in (path, path + ".tmp", path + ".bak"):
        try:
            os.remove(p)
        except OSError:
            pass


def write_file(path, text):
    with open(path, "w") as f:
        f.write(text)


def rm_tree(path):
    try:
        names = os.listdir(path)
    except OSError:
        return
    for name in names:
        full = path + "/" + name
        try:
            os.remove(full)
        except OSError:
            rm_tree(full)
    os.rmdir(path)


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


REAL_KEY_BYTE = pu._key_byte


# a terminal that records what is drawn on it


class FakeVt:
    def __init__(self):
        self.drawn = []

    def wr(self, text):
        self.drawn.append(text)
        return len(text)


# the HTTP side of rss_news, openrouter_ai and weather: a socket-like body, a
# response over it and a requests module that answers from a script


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


# gfx drawing on a display that records the calls: the gfx and console tests of
# test_core.py and the apps test of test_tools.py use it


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


def with_display(body):
    import gfx

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


def run(namespace):
    # every test_* callable of an area file, in name order
    tests = [(k, v) for k, v in sorted(namespace.items()) if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        try:
            fn()
        except Exception as error:
            FAILS.append(name)
            print("ERROR", name, repr(error))
    print("{} tests, {} failed".format(len(tests), len(FAILS)))
    if FAILS:
        sys.exit(1)
