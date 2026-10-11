"""The toolkit's HTTP/1.0 client (pico_utils.http_module() returns it): the
call shape of micropython-lib requests, minus what made that one unsafe here.
requests 0.10.2 (frozen in the firmware) wraps TLS with CERT_NONE, and on a
redirect asks again with the same headers (API keys included, to any host,
even over http) and no timeout. Here:

- TLS is verified against the roots below whenever a request carries a
  credential (Authorization, X-Auth-Token) to a public host: the OpenRouter
  and Miniflux keys. Public content (feeds, weather) stays unverified: each
  verified handshake costs 1.4-2.6 s and 10-15 KB of heap on the device
  (measured 2026-10-11). The check needs the clock: it reads 2021 after
  power-on until NTP, and certificates are then "not yet valid".
- Redirects: three at most, the same timeout each hop, credential headers
  dropped when the scheme, host or port changes.
- Bodies read through .content/.text/.json() are capped (max_bytes).

Upkeep: if a provider moves to a CA whose root is not below, its verified
requests fail with "certificate check failed" until the root is added."""

import gc

try:
    import ujson as json
except ImportError:
    import json


MODULE_VERSION = "2026-10-11.1"
USER_AGENT = "PicoCalc"
TIMEOUT = 15
MAX_BYTES = 65536  # what .content reads at most
MAX_REDIRECTS = 3
MAX_HEAD = 8192  # status line and headers
CREDENTIAL_HEADERS = ("authorization", "x-auth-token", "proxy-authorization", "cookie")
_VERIFY_HEADERS = ("authorization", "x-auth-token", "proxy-authorization")
_PRIVATE_SUFFIXES = (".local", ".lan", ".home", ".internal", ".home.arpa")

# Trust anchors, DER in base64, decoded only for a verified request.
_ROOTS = (
    # Google Trust Services LLC, GTS Root R1 (RSA 4096), until 2036-06-22
    (
    "MIIFVzCCAz+gAwIBAgINAgPlk28xsBNJiGuiFzANBgkqhkiG9w0BAQwFADBHMQswCQYDVQQG"
    "EwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEUMBIGA1UEAxMLR1RT"
    "IFJvb3QgUjEwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAwMDAwWjBHMQswCQYDVQQGEwJV"
    "UzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEUMBIGA1UEAxMLR1RTIFJv"
    "b3QgUjEwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQC2EQKLHuOhd5s73L+UPreV"
    "p0A8of2C+X0yBoJx9vaMf/vo27xqLpeXo4xL+Sv2sfnOhB2x+cWX3u+58qPpvBKJXqeqUqv4"
    "IyfLpLGcY9vXmX7wCl7raKb0xlpHDU0QM+NOsROjyBhsS+z8CZDfnWQpJSMHobTSPS5g4M/S"
    "CYe7zUjwTcLCeoiKu7rPWRnWr4+wB7CeMfGCwcDfLqZtbBkOtdh+JhpFAz2weaSUKK0Pfybl"
    "qAj+lug8aJRT7oM6iCsVlgmy4HqMLnXWnOunVmSPlk9orj2XwoSPwLxAwAtcvfaHszVsrBhQ"
    "f4TgTM2S0yDpM7xSma8ytSmzJSq0SPly4cpk9+aCEI3oncKKiPo4Zor8Y/kB+Xj9e1x3+naH"
    "+uzfsQ55lVe0vSbv1gHR6xYKu44LtcXFilWr06zqkUspzBmkMiVOKvFlRNACzqrOSbTqn3yD"
    "sEB750Orp2yjj32JgfpMpf/VjsPOS+C12LOORc92wO1AK/1TD7Cn1TsNsYqiA94xrcx36m97"
    "PtbfkSIS5r762DL8EGMUUXLeXdYWk70paDPvOmbsB4om3xPXV2V4J95eSRQAogB/mqghtqmx"
    "lbCluQ0WEdrHbEg8QOB+DVrNVjzRlwW5y0vtOUucxD/SVRNuJLDWcfr0wbrM7Rv1/oFB2ACY"
    "PTrIrnqYNxgFlQIDAQABo0IwQDAOBgNVHQ8BAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAd"
    "BgNVHQ4EFgQU5K8rJnEaK0gnhS9SZizv8IkTcT4wDQYJKoZIhvcNAQEMBQADggIBAJ+qQibb"
    "C5u+/x6Wki4+omVKapi6Ist9wTrYggoGxval3sBOh2Z5ofmmWJyq+bXmYOfg6LEeQkEzCzc9"
    "zolwFcq1JKjPa7XSQCGYzyI0zzvFIoTgxQ6KfF2I5DUkzps+GlQebtuyh6f88/qBVRRiClmp"
    "IgUxPoLW7ttXNLwzldMXG+gnoot7TiYaelpkttGsN/H9oPM47HLwEXWdyzRSjeZ2axfG34ar"
    "J45JK3VmgRAhpuo+9K4l/3wV3s6MJT/KYnAK9y8JZgfIPxz88NtFMN9iiMG1D53Dn0reWVlH"
    "xYciNuaCp+0KueIHoI17eko8cdLiA6EfMgfdG+RCzgwARWGAtQsgWSl4vflVy2PFPEz0tv/b"
    "al8xa5meLMFrUKTX5hgUvYU/Z6tGn6D/Qqc6f1zLXbBwHSs09dR2CQzreExZBfMzQsNhFRAb"
    "d03OIozUhfJFfbdT6u9AWpQKXCBfTkBdYiJ23//OYb2MI3jSNwLgjt7RETeJ9r/tSQdirpLs"
    "QBqvFAnZ0E6yove+7u7Y/9waLd64NnHi/Hm3lCXRSHNboTXns5lndcEZOitHTtNCjv0xyBZm"
    "2tIMPNuzjsmhDYAPexZ3FL//2wmUspO8IFgV6dtxQ/PeEMMA3KgqlbbC1j+Qa3bbbP6MvPJw"
    "NQzcmRk13NfIRmPVNnGuV/u3gm3c"
    ),
    # Google Trust Services LLC, GTS Root R4 (ECDSA P-384): openrouter.ai, leaf <- WE1 <- R4, until 2036-06-22
    (
    "MIICCTCCAY6gAwIBAgINAgPlwGjvYxqccpBQUjAKBggqhkjOPQQDAzBHMQswCQYDVQQGEwJV"
    "UzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEUMBIGA1UEAxMLR1RTIFJv"
    "b3QgUjQwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAwMDAwWjBHMQswCQYDVQQGEwJVUzEi"
    "MCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEUMBIGA1UEAxMLR1RTIFJvb3Qg"
    "UjQwdjAQBgcqhkjOPQIBBgUrgQQAIgNiAATzdHOnaItgrkO4NcWBMHtLSZ37wWHO5t5GvWvV"
    "YRg1rkDdc/eJkTBa6zzuhXyiQHY7qca4R9gq55KRanPpsXI5nymfopjTX15YhmUPoYRlBtHc"
    "i8nHc8iMai/lxKvRHYqjQjBAMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MB0G"
    "A1UdDgQWBBSATNbrdP9JNqPV2Py1PsVq8JQdjDAKBggqhkjOPQQDAwNpADBmAjEA6ED/g94D"
    "9J+uHXqnLrmvT/aDHQ4thQEd0dlq7A/Cr8deVl5c1RxYIigL9zC2L7F8AjEA8GE8p/SgguMh"
    "1YQdc4acLa/KNJvxn7kjNuK8YAOdgLOaVsjh4rsUecrNIdSUtUlD"
    ),
    # Internet Security Research Group, ISRG Root X1 (RSA 4096): Let's Encrypt, until 2035-06-04
    (
    "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"
    ),
    # Internet Security Research Group, ISRG Root X2 (ECDSA P-384): Let's Encrypt, until 2040-09-17
    (
    "MIICGzCCAaGgAwIBAgIQQdKd0XLq7qeAwSxs6S+HUjAKBggqhkjOPQQDAzBPMQswCQYDVQQG"
    "EwJVUzEpMCcGA1UEChMgSW50ZXJuZXQgU2VjdXJpdHkgUmVzZWFyY2ggR3JvdXAxFTATBgNV"
    "BAMTDElTUkcgUm9vdCBYMjAeFw0yMDA5MDQwMDAwMDBaFw00MDA5MTcxNjAwMDBaME8xCzAJ"
    "BgNVBAYTAlVTMSkwJwYDVQQKEyBJbnRlcm5ldCBTZWN1cml0eSBSZXNlYXJjaCBHcm91cDEV"
    "MBMGA1UEAxMMSVNSRyBSb290IFgyMHYwEAYHKoZIzj0CAQYFK4EEACIDYgAEzZvVn4CDCuwJ"
    "SvMWSj5cz3es3mcFDR0HttwW+1qLFNvicWDEukWVEYmO6gbf9yoWHKS5xcUy4APgHoIYOIvX"
    "RdgKam7mAHf7AlF9ItgKbppbd9/w+kHsOdx1ymgHDB/qo0IwQDAOBgNVHQ8BAf8EBAMCAQYw"
    "DwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUfEKWrt5LSDv6kviejM9ti6lyN5UwCgYIKoZI"
    "zj0EAwMDaAAwZQIwe3lORlCEwkSHRhtFcP9Ymd70/aTSVaYgLXTWNLxBo1BfASdWtL4ndQav"
    "Ei51mI38AjEAi/V3bNTIZargCyzuFJ0nN6T5U6VR5CmD1/iQMVtCnwr1/q4AaOeMSQ+2b1tb"
    "FfLn"
    ),
)


# ── URLs and policy ─────────────────────────────


def split_url(url):
    """(scheme, host, port, path) of an http(s) URL; path without its "/"."""
    if "://" not in url:
        raise ValueError("not a URL: " + url[:40])
    scheme, rest = url.split("://", 1)
    scheme = scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("unsupported scheme: " + scheme)
    slash = rest.find("/")
    hostport, path = (rest, "") if slash < 0 else (rest[:slash], rest[slash + 1 :])
    port = 443 if scheme == "https" else 80
    if ":" in hostport:
        hostport, number = hostport.rsplit(":", 1)
        port = int(number)
    if not hostport:
        raise ValueError("no host in URL")
    return scheme, hostport.lower(), port, path


def join_url(base, location):
    """Where a redirect's Location points, from the URL that answered."""
    if "://" in location:
        return location
    scheme, host, port, path = split_url(base)
    default = 443 if scheme == "https" else 80
    origin = "{}://{}{}".format(scheme, host, "" if port == default else ":{}".format(port))
    if location.startswith("//"):
        return scheme + ":" + location
    if location.startswith("/"):
        return origin + location
    folder = path.split("?", 1)[0].rsplit("/", 1)[0] if "/" in path else ""
    return origin + "/" + (folder + "/" if folder else "") + location


def is_private(host):
    """A host on the local network (no public CA vouches for it)."""
    host = host.lower()
    if host == "localhost" or "." not in host:
        return True
    for suffix in _PRIVATE_SUFFIXES:
        if host.endswith(suffix):
            return True
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        a, b = int(parts[0]), int(parts[1])
        return a in (10, 127) or (a == 172 and 16 <= b <= 31) or (a, b) in ((192, 168), (169, 254))
    return False


def must_verify(host, headers, verify=None):
    """verify=None: True when a credential goes to a public host."""
    if verify is not None:
        return bool(verify)
    if is_private(host):
        return False
    for name in headers:
        if name.lower() in _VERIFY_HEADERS:
            return True
    return False


def redirect_headers(headers, old_url, new_url):
    """The headers for the next hop: credentials stay only when scheme, host
    and port are the same (https -> http never keeps them)."""
    if split_url(old_url)[:3] == split_url(new_url)[:3]:
        return headers
    return dict((k, v) for k, v in headers.items() if k.lower() not in CREDENTIAL_HEADERS)


# ── connection ──────────────────────────────────


def _roots():
    import binascii

    return [binascii.a2b_base64(text) for text in _ROOTS]


def _clock_ready():
    # mbedtls checks validity dates: the clock must be set (one quiet NTP try)
    import pico_utils

    if pico_utils.clock_synced():
        return True
    try:
        import clock_ntp

        clock_ntp.sync_quiet(3)
    except Exception:
        pass
    return pico_utils.clock_synced()


def _wrap(sock, host, verify):
    import tls

    ctx = tls.SSLContext(tls.PROTOCOL_TLS_CLIENT)
    if verify:
        ctx.verify_mode = tls.CERT_REQUIRED
        for der in _roots():
            ctx.load_verify_locations(der)  # positional: the device rejects cadata=
    else:
        ctx.verify_mode = tls.CERT_NONE
    try:
        return ctx.wrap_socket(sock, server_hostname=host)
    except ValueError as error:  # mbedtls: "The certificate is not ..."
        text = " ".join(str(error).split())
        if "certif" in text.lower():
            raise ValueError("certificate check failed: " + text[:60])
        raise


def _connect(scheme, host, port, timeout, verify):
    # A stream with write/readline/read/readinto/close. Tests replace it.
    import socket

    if verify and not _clock_ready():
        raise OSError("clock not set: can't check the certificate")
    ai = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0]
    sock = socket.socket(ai[0], socket.SOCK_STREAM, ai[2])
    try:
        sock.settimeout(timeout)
        sock.connect(ai[-1])
        if scheme == "https":
            gc.collect()  # mbedtls wants ~20 KB of buffers
            sock = _wrap(sock, host, verify)
        return sock
    except BaseException:
        sock.close()
        raise


# ── responses ───────────────────────────────────


def _text(data):
    try:
        return str(data, "utf-8")
    except UnicodeError:
        return "".join(chr(b) if b < 128 else "?" for b in data)


class Response:
    def __init__(self, raw, status, reason, headers, max_bytes):
        self.raw = raw
        self.status_code = status
        self.reason = reason
        self.headers = headers
        self.encoding = "utf-8"
        self._max = max_bytes
        self._content = None

    def close(self):
        if self.raw is not None:
            try:
                self.raw.close()
            except Exception:
                pass
            self.raw = None

    @property
    def content(self):
        if self._content is None:
            try:
                self._content = read_body(self.raw, self.headers.get("content-length"), self._max)
            finally:
                self.close()
        return self._content

    @property
    def text(self):
        return _text(self.content)

    def json(self):
        return json.loads(self.content)


def read_body(raw, length, cap):
    """The body, cap bytes at most (ValueError past it)."""
    try:
        length = int(length) if length is not None else None
    except ValueError:
        length = None
    if length is not None and length > cap:
        raise ValueError("response too big")
    out = bytearray()
    while raw is not None and (length is None or len(out) < length):
        want = 1024 if length is None else min(1024, length - len(out))
        chunk = raw.read(want)
        if not chunk:
            break
        out.extend(chunk)
        if len(out) > cap:
            raise ValueError("response too big")
    return bytes(out)


def read_head(raw):
    """(status, reason, headers) from the stream; header names lowercase."""
    line = raw.readline()
    used = len(line)
    parts = line.split(None, 2)
    if len(parts) < 2 or not parts[0].startswith(b"HTTP/"):
        raise ValueError("not an HTTP response")
    try:
        status = int(parts[1])
    except ValueError:
        raise ValueError("bad status line")
    reason = _text(parts[2].strip()) if len(parts) > 2 else ""
    headers = {}
    while True:
        line = raw.readline()
        used += len(line)
        if used > MAX_HEAD or len(headers) > 60:
            raise ValueError("response head too big")
        if not line or line in (b"\r\n", b"\n"):
            break
        colon = line.find(b":")
        if colon > 0:
            headers[_text(line[:colon]).strip().lower()] = _text(line[colon + 1 :]).strip()
    if "chunked" in headers.get("transfer-encoding", "").lower():
        raise ValueError("chunked responses not supported")
    return status, reason, headers


# ── requests ────────────────────────────────────


def _body(data, json_data):
    if json_data is not None:
        data = json.dumps(json_data)
    if isinstance(data, str):
        data = data.encode()  # bytes: Content-Length counts bytes, not characters
    return data


def _send(raw, method, host, port, scheme, path, headers, body):
    default = 443 if scheme == "https" else 80
    lines = ["{} /{} HTTP/1.0".format(method, path)]
    names = [k.lower() for k in headers]
    if "host" not in names:
        lines.append("Host: " + (host if port == default else "{}:{}".format(host, port)))
    if "user-agent" not in names:
        lines.append("User-Agent: " + USER_AGENT)
    lines.append("Connection: close")
    for name, value in headers.items():
        if name.lower() not in ("connection", "content-length"):
            lines.append("{}: {}".format(name, value))
    if body is not None:
        lines.append("Content-Length: {}".format(len(body)))
    raw.write(("\r\n".join(lines) + "\r\n\r\n").encode())
    if body:
        raw.write(body)


def request(method, url, data=None, json=None, headers=None, timeout=TIMEOUT, verify=None, max_bytes=MAX_BYTES, stream=None, **kwargs):
    """One HTTP request (following up to MAX_REDIRECTS redirects): a Response
    whose .raw streams the body, or .content/.text/.json() up to max_bytes.
    verify: None = verify TLS when a credential goes to a public host."""
    method = method.upper()
    headers = dict(headers or {})
    body = _body(data, json)
    if json is not None and not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = "application/json"
    for hop in range(MAX_REDIRECTS + 1):
        scheme, host, port, path = split_url(url)
        checked = scheme == "https" and must_verify(host, headers, verify)
        if not checked and verify is None and scheme == "http" and must_verify(host, headers, None):
            raise ValueError("refused: a key over plain http to " + host)
        raw = _connect(scheme, host, port, timeout, checked)
        try:
            _send(raw, method, host, port, scheme, path, headers, body)
            status, reason, got = read_head(raw)
        except BaseException:
            raw.close()
            raise
        location = got.get("location")
        if status not in (301, 302, 303, 307, 308) or not location:
            return Response(raw, status, reason, got, max_bytes)
        raw.close()
        if hop == MAX_REDIRECTS:
            break
        new_url = join_url(url, location)
        headers = redirect_headers(headers, url, new_url)
        if status == 303 or (status in (301, 302) and method not in ("GET", "HEAD")):
            method, body = "GET", None
            headers = dict((k, v) for k, v in headers.items() if k.lower() != "content-type")
        url = new_url
    raise ValueError("too many redirects")


def get(url, **kwargs):
    return request("GET", url, **kwargs)


def head(url, **kwargs):
    return request("HEAD", url, **kwargs)


def post(url, **kwargs):
    return request("POST", url, **kwargs)


def put(url, **kwargs):
    return request("PUT", url, **kwargs)


def delete(url, **kwargs):
    return request("DELETE", url, **kwargs)


# ── standard ────────────────────────────────────


def ver():
    print("pico_net:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- pico_net (HTTP client) --")
    print("get(url, headers=..., timeout=15)")
    print("post(url, json=... or data=...)")
    print("verify=None: TLS checked when a key goes to a public host")
    print("r.status_code r.json() r.text r.raw r.close()")


def h():
    return help()
