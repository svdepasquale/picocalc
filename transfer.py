"""Transfer: files between a computer and the SD card over Wi-Fi. serve()
runs a small web server while its screen is up: the PicoCalc shows an
address and a PIN, and a browser on the same network lists, downloads,
uploads and deletes files on the card (curl works too, see help()). SD card
only, never the flash; every request but the page itself needs the PIN; 10
wrong PINs close it; q or Esc closes it. USB serial manages ~8 KB/s: a song
took an hour that way."""

import gc
import os

from pico_utils import DISPLAY_WIDTH
from pico_utils import check_wifi as _check_wifi, clean as _clean, clip as _clip
from pico_utils import format_bytes as _format_bytes, key_bar as _key_bar
from pico_utils import paint as _paint, poll_key as _poll_key
from pico_utils import screen_header as _screen_header, ticks_diff as _ticks_diff
from pico_utils import ticks_ms as _ticks_ms, wait_key as _wait_key
from pico_utils import BCYAN, BGREEN, BRED, BYELLOW, GREY

try:
    import ujson as json
except ImportError:
    import json


MODULE_VERSION = "2026-10-11.1"
PORT = 80
SD_ROOT = "/sd"
MAX_UPLOAD = 64 * 1024 * 1024
MAX_HEAD = 4096  # request line and headers
MAX_HEADERS = 30
MAX_NAME = 64
MAX_LIST = 400  # files listed per folder
CHUNK = 4096
RECV_TIMEOUT = 10  # seconds a connected client may stay silent
ACCEPT_WAIT = 0.25  # seconds per accept(): q/Esc is read in between
MAX_STRIKES = 10  # wrong PINs before the server closes
SD_FAST = 8000000  # SPI clock while serving: 214 KB/s measured, 89 KB/s at the default
SD_DEFAULT = 1320000
LOG_LINES = 6
_QUIT = ("q", "Q", "esc", "eof")
_HEX = "0123456789abcdefABCDEF"
_REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed", 411: "Length Required", 413: "Too Large", 500: "Server Error"}

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>PicoCalc SD</title><style>body{font:15px system-ui,sans-serif;margin:1em auto;max-width:42em;padding:0 1em}
table{border-collapse:collapse;width:100%}td{padding:.3em;border-bottom:1px solid #ccc}td.n{text-align:right;white-space:nowrap}
progress{width:100%}input,select,button{font:inherit}</style>
<h3>PicoCalc SD card</h3>
<p><input id=pin size=7 placeholder=PIN inputmode=numeric autocomplete=off> <select id=dir></select> <button id=go>Open</button></p>
<p><input type=file id=f multiple> <button id=up>Upload</button></p><progress id=p max=1 value=0></progress>
<p id=msg></p><table id=t></table>
<script>
var $=function(i){return document.getElementById(i)};
function say(t){$('msg').textContent=t}
function api(m,u,b,cb,pr){var x=new XMLHttpRequest();x.open(m,u);x.setRequestHeader('X-PIN',$('pin').value);
if(pr)x.upload.onprogress=pr;x.onload=function(){cb(x)};x.onerror=function(){say('network error')};x.send(b)}
function q(n){return 'dir='+encodeURIComponent($('dir').value)+(n==null?'':'&name='+encodeURIComponent(n))}
function ls(){api('GET','/api/ls?'+q(),null,function(x){if(x.status!=200){say(x.responseText);return}
var d=JSON.parse(x.responseText),s=$('dir'),t=$('t');
if(!s.options.length){d.dirs.forEach(function(n){var o=document.createElement('option');o.value=n;o.textContent=n||'(card)';s.appendChild(o)});s.value=d.dir}
while(t.firstChild)t.removeChild(t.firstChild);
d.files.forEach(function(e){var r=t.insertRow(),a=document.createElement('a'),b=document.createElement('button'),c;
a.textContent=e[0];a.href='/api/get?'+q(e[0])+'&pin='+encodeURIComponent($('pin').value);a.download=e[0];r.insertCell().appendChild(a);
c=r.insertCell();c.className='n';c.textContent=(e[1]/1024).toFixed(1)+' KB';b.textContent='delete';
b.onclick=function(){if(confirm('Delete '+e[0]+'?'))api('DELETE','/api/rm?'+q(e[0]),null,function(x){say(x.responseText);ls()})};
r.insertCell().appendChild(b)});say(d.files.length+' files'+(d.more?' (the first ones)':''))})}
$('go').onclick=ls;$('dir').onchange=ls;
$('up').onclick=function(){var fs=$('f').files,i=0;(function next(){if(i>=fs.length){ls();return}var f=fs[i++];
api('PUT','/api/put?'+q(f.name),f,function(x){say(f.name+': '+x.responseText);$('p').value=0;next()},
function(e){$('p').value=e.total?e.loaded/e.total:0})})()};
</script>
"""


# ── names and requests ──────────────────────────


def new_pin():
    """Six digits from the hardware random source."""
    return "{:06d}".format(int.from_bytes(os.urandom(4), "big") % 1000000)


def same_pin(given, pin):
    """Compared whole: no early exit at the first wrong digit."""
    given = str(given or "")
    diff = len(given) ^ len(pin)
    for i in range(len(pin)):
        diff |= (ord(given[i]) if i < len(given) else 0x100) ^ ord(pin[i])
    return diff == 0


def unquote(text):
    """Percent-decoding of a query value ("+" is a space); UnicodeError when
    the bytes are not UTF-8."""
    if "%" not in text and "+" not in text:
        return text
    parts = text.replace("+", " ").split("%")
    out = bytearray(parts[0].encode())
    for part in parts[1:]:
        if len(part) >= 2 and part[0] in _HEX and part[1] in _HEX:
            out.append(int(part[:2], 16))
            out.extend(part[2:].encode())
        else:
            out.extend(b"%" + part.encode())
    return str(out, "utf-8")


def parse_query(query):
    out = {}
    for pair in query.split("&"):
        if pair:
            key, _, value = _partition(pair, "=")
            try:
                out[unquote(key)] = unquote(value)
            except UnicodeError:
                out[key] = None  # not UTF-8: no valid name
    return out


def _partition(text, sep):
    at = text.find(sep)
    if at < 0:
        return text, "", ""
    return text[:at], sep, text[at + len(sep) :]


def valid_name(name):
    """A plain file or folder name: no path, no hidden files, no control
    characters, 1 to MAX_NAME characters."""
    if not isinstance(name, str) or not 0 < len(name) <= MAX_NAME:
        return False
    if name in (".", "..") or name[0] == "." or "/" in name or "\\" in name:
        return False
    for ch in name:
        if ch < " " or ch == "\x7f":
            return False
    return True


def _is_dir(path):
    try:
        return (os.stat(path)[0] & 0x4000) != 0
    except OSError:
        return False


def _is_file(path):
    try:
        return (os.stat(path)[0] & 0x4000) == 0
    except OSError:
        return False


def folder_path(root, name):
    """The card folder a request means: "" is the card itself, else a folder
    directly in it. None for anything else."""
    if name == "":
        return root
    if not valid_name(name):
        return None
    path = root.rstrip("/") + "/" + name
    return path if _is_dir(path) else None


def read_head(conn):
    """(method, path, query, headers) of a request, headers lowercase; None
    when it is not one (too long, too many headers, garbage, closed)."""
    line = conn.readline(MAX_HEAD)
    used = len(line)
    if not line.endswith(b"\n"):
        return None
    parts = line.split()
    if len(parts) != 3 or not parts[2].startswith(b"HTTP/"):
        return None
    try:
        method = str(parts[0], "ascii")
        target = str(parts[1], "ascii")
    except UnicodeError:
        return None
    headers = {}
    while True:
        line = conn.readline(MAX_HEAD - used + 1)
        used += len(line)
        if used > MAX_HEAD or len(headers) > MAX_HEADERS or not line.endswith(b"\n"):
            return None
        line = line.strip()
        if not line:
            break
        colon = line.find(b":")
        if colon <= 0:
            return None
        try:
            key = str(line[:colon], "ascii").strip().lower()
        except UnicodeError:
            return None
        try:
            headers[key] = str(line[colon + 1 :], "utf-8").strip()
        except UnicodeError:
            headers[key] = ""  # a value this server never reads
    path, _, query = _partition(target, "?")
    return method, path, query, headers


# ── responses ───────────────────────────────────


def _send(conn, status, body=b"", kind="application/json", extra=""):
    if isinstance(body, str):
        body = body.encode()
    head = "HTTP/1.0 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n{}\r\n".format(
        status, _REASONS.get(status, "Error"), kind, len(body), extra
    )
    conn.write(head.encode())
    if body:
        conn.write(body)
    return status


def _error(conn, status, text):
    return _send(conn, status, json.dumps({"error": text}))


def _listing(root, folder, name):
    dirs = [""]
    try:
        for entry in sorted(os.listdir(root)):
            if valid_name(entry) and _is_dir(root.rstrip("/") + "/" + entry):
                dirs.append(entry)
    except OSError:
        pass
    files = []
    more = False
    try:
        for entry in sorted(os.listdir(folder)):
            path = folder.rstrip("/") + "/" + entry
            if entry.startswith(".") or not _is_file(path):
                continue
            if len(files) >= MAX_LIST:
                more = True
                break
            files.append([entry, os.stat(path)[6]])
    except OSError:
        pass
    return json.dumps({"dir": name, "dirs": dirs, "files": files, "more": more})


# ── the server's work ───────────────────────────


def handle(conn, state):
    """Answer one request on conn (socket-like: readline(n), readinto(buf),
    write(data)). state: pin, root, strikes, log (list), buf, stop, abort
    (a function: True stops a transfer). Returns the status sent."""
    req = read_head(conn)
    if req is None:
        return _error(conn, 400, "bad request")
    method, path, query, headers = req
    if path == "/" and method == "GET":
        return _send(
            conn,
            200,
            PAGE,
            "text/html; charset=utf-8",
            "Content-Security-Policy: default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'\r\nReferrer-Policy: no-referrer\r\n",
        )
    if not path.startswith("/api/"):
        return _error(conn, 404, "not found")
    args = parse_query(query)
    pin = headers.get("x-pin")
    if pin is None and method == "GET" and path == "/api/get":
        pin = args.get("pin")  # plain download links
    if not same_pin(pin, state["pin"]):
        state["strikes"] += 1
        if state["strikes"] >= MAX_STRIKES:
            state["stop"] = "{} wrong PINs: closed".format(MAX_STRIKES)
        return _error(conn, 401, "wrong PIN")
    folder = folder_path(state["root"], args.get("dir", ""))
    if folder is None:
        return _error(conn, 404, "no such folder")
    name = args.get("name")
    if path == "/api/ls" and method == "GET":
        return _send(conn, 200, _listing(state["root"], folder, args.get("dir", "")))
    if not valid_name(name):
        return _error(conn, 400, "bad name")
    target = folder.rstrip("/") + "/" + name
    if path == "/api/get" and method == "GET":
        return _download(conn, state, target, name)
    if path == "/api/put" and method == "PUT":
        return _upload(conn, state, headers, target, name)
    if path == "/api/rm" and method == "DELETE":
        if not _is_file(target):
            return _error(conn, 404, "no such file")
        try:
            os.remove(target)
        except OSError as error:
            return _error(conn, 500, str(error))
        _log(state, "deleted " + name)
        return _send(conn, 200, json.dumps({"deleted": name}))
    return _error(conn, 405, "not allowed")


def _download(conn, state, target, name):
    if not _is_file(target):
        return _error(conn, 404, "no such file")
    size = os.stat(target)[6]
    started = _ticks_ms()
    quoted = name.replace("\\", "_").replace('"', "_")
    conn.write(
        "HTTP/1.0 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: {}\r\nContent-Disposition: attachment; filename=\"{}\"\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n".format(
            size, quoted
        ).encode()
    )
    buf = state["buf"]
    view = memoryview(buf)
    sent = 0
    with open(target, "rb") as f:
        while True:
            n = f.readinto(buf)
            if not n:
                break
            conn.write(view[:n])
            sent += n
            if state["abort"]():
                break
    _log(state, "sent {} {}".format(name, _rate(sent, started)))
    return 200


def _upload(conn, state, headers, target, name):
    if "transfer-encoding" in headers:
        return _error(conn, 411, "send a Content-Length, not chunks")
    try:
        length = int(headers.get("content-length", ""))
    except ValueError:
        return _error(conn, 411, "Content-Length needed")
    if length < 0 or length > MAX_UPLOAD:
        return _error(conn, 413, "too large: {} at most".format(_format_bytes(MAX_UPLOAD)))
    part = target + ".part"
    buf = state["buf"]
    view = memoryview(buf)
    left = length
    started = _ticks_ms()
    done = False
    try:
        with open(part, "wb") as f:
            while left > 0:
                n = conn.readinto(view[: min(len(buf), left)])
                if not n:
                    raise OSError("connection closed")
                f.write(view[:n])
                left -= n
                if state["abort"]():
                    raise OSError("stopped")
        try:
            os.remove(target)  # a FAT rename never replaces a file
        except OSError:
            pass
        os.rename(part, target)
        done = True
    except OSError as error:
        _log(state, "failed {}: {}".format(name, error))
        try:
            return _error(conn, 500, "upload failed: {}".format(error))
        except OSError:
            return 500
    finally:
        if not done:
            try:
                os.remove(part)
            except OSError:
                pass
    _log(state, "got {} {}".format(name, _rate(length, started)))
    return _send(conn, 200, json.dumps({"saved": name, "bytes": length}))


def _rate(size, started):
    ms = max(1, _ticks_diff(_ticks_ms(), started))
    return "{} {} KB/s".format(_format_bytes(size), size * 1000 // ms // 1024)


def _log(state, line):
    state["log"].append(_clean(line))
    del state["log"][:-LOG_LINES]
    state["dirty"] = True


# ── screen and loop ─────────────────────────────


def _draw(url, pin, state):
    _screen_header("Transfer")
    print(" On a computer on the same Wi-Fi, open:")
    print("   " + _paint(url, BCYAN))
    print(" PIN   " + _paint("  ".join(pin), BYELLOW))
    print("")
    print(_paint(" SD card only. curl: help() in the REPL.", GREY))
    print(_paint("─" * DISPLAY_WIDTH, GREY))
    _key_bar((("q", "stop"),))
    _draw_log(state)


def _draw_log(state):
    print("\x1b[11;1H", end="")
    for i in range(LOG_LINES):
        line = state["log"][i] if i < len(state["log"]) else ""
        print("\x1b[K " + _clip(line, DISPLAY_WIDTH - 2))
    state["dirty"] = False


def _sd(speed):
    try:
        import picocalc

        picocalc.sd.sd.init_spi(speed)
    except Exception:
        pass


def _quit_pressed():
    return _poll_key() in _QUIT


def serve(port=PORT, root=SD_ROOT):
    """The server, until q/Esc (or MAX_STRIKES wrong PINs)."""
    import socket

    if not _check_wifi():
        _wait_key()
        return None
    if not _is_dir(root):
        _screen_header("Transfer")
        print("No SD card.")
        _wait_key()
        return None
    import network

    ip = network.WLAN(network.STA_IF).ifconfig()[0]
    url = "http://{}/".format(ip) if port == 80 else "http://{}:{}/".format(ip, port)
    gc.collect()
    state = {"pin": new_pin(), "root": root, "strikes": 0, "log": [], "buf": bytearray(CHUNK), "stop": None, "dirty": False}
    polled = [_ticks_ms()]

    def abort():
        # during a transfer: q/Esc, at most every 250 ms (an I2C read each)
        now = _ticks_ms()
        if _ticks_diff(now, polled[0]) < 250:
            return False
        polled[0] = now
        return _quit_pressed()

    state["abort"] = abort
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _sd(SD_FAST)
    try:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(socket.getaddrinfo("0.0.0.0", port)[0][-1])
        server.listen(1)
        server.settimeout(ACCEPT_WAIT)
        _draw(url, state["pin"], state)
        while state["stop"] is None:
            if _quit_pressed():
                break
            try:
                conn, _ = server.accept()
            except OSError:  # the accept wait ran out
                continue
            try:
                conn.settimeout(RECV_TIMEOUT)
                handle(conn, state)
            except OSError as error:
                _log(state, "dropped: {}".format(error))
            finally:
                conn.close()
                gc.collect()
            if state["dirty"]:
                _draw_log(state)
    finally:
        server.close()
        _sd(SD_DEFAULT)
        state["buf"] = None
    if state["stop"]:
        print(_paint(state["stop"], BRED))
        _wait_key()
    return True


# ── standard ────────────────────────────────────


def ver():
    print("transfer:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Transfer (SD card over Wi-Fi) --")
    print("serve()  s()   Server until q; shows address, PIN")
    print("In a browser: the address shown, then the PIN.")
    print("curl -T song.wav -H 'X-PIN: 123456' \\")
    print(" 'http://IP/api/put?dir=music&name=song.wav'")
    print("Also /api/ls?dir=D  /api/get  /api/rm (DELETE)")


def h():
    return help()


s = serve
