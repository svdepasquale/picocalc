import gc
import time

from pico_utils import clip as _clip
from pico_utils import paged_lines as _paged_lines
from pico_utils import preview_print as _preview_print
from pico_utils import browse_items as _browse_items
from pico_utils import load_json, save_json, http_module as _http_module, check_wifi
from pico_utils import http_request as _http_request, wrap_text as _wrap_text
from pico_utils import ticks_ms as _ticks_ms, ticks_diff as _ticks_diff
from pico_utils import poll_key as _poll_key
from pico_utils import screen_header as _screen_header
from pico_utils import paint as _paint, BCYAN, BWHITE, GREY

try:
    import ujson as json
except ImportError:
    import json


CONFIG_FILE = "rss_feeds.json"
MF_CONFIG_FILE = "miniflux_config.json"
MF_LIMIT = 3
MF_MAX_BYTES = 49152
MF_MIN_BYTES = 16384  # retry size when no 48 KB block is free
MF_CONTENT_CHARS = 6000  # of an entry's HTML, cleaned for its summary
MAX_XML_BYTES = 26000
MIN_XML_BYTES = 16384  # retry size when no 26 KB block is free
MAX_TITLE_CHARS = 140
MAX_SUMMARY_CHARS = 480
DEFAULT_PREVIEW_CHARS = 110
DEFAULT_ITEMS_PER_FEED = 2
MAX_FEEDS = 12
MODULE_VERSION = "2026-10-07.1"

DEFAULT_FEEDS = [
    {"name": "BBC World", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "ANSA", "url": "https://www.ansa.it/sito/notizie/topnews/topnews_rss.xml"},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
]

_LAST_ITEMS = []
# Named entities decoded in feed text (others stay as written). Signs the
# screen font lacks are written in ASCII; letters it lacks fold on screen.
_ENTITIES = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    "nbsp": " ",
    "hellip": "...",
    "lsquo": "'",
    "rsquo": "'",
    "ldquo": '"',
    "rdquo": '"',
    "ndash": "-",
    "mdash": "-",
    "laquo": "\xab",
    "raquo": "\xbb",
    "deg": "\xb0",
    "middot": "\xb7",
    "euro": "EUR",
    "copy": "(c)",
    "reg": "(R)",
    "times": "x",
    "agrave": "\xe0",
    "egrave": "\xe8",
    "eacute": "\xe9",
    "igrave": "\xec",
    "ograve": "\xf2",
    "ugrave": "\xf9",
    "Agrave": "\xc0",
    "Egrave": "\xc8",
    "Eacute": "\xc9",
    "Igrave": "\xcc",
    "Ograve": "\xd2",
    "Ugrave": "\xd9",
}


def _resolve_cached_item(index):
    try:
        pos = int(index) - 1
    except Exception:
        print("Invalid index.")
        return None, None
    if pos < 0 or pos >= len(_LAST_ITEMS):
        print("Out of range.")
        return None, None
    return _LAST_ITEMS[pos], pos


def _render_news_summary(item, pos, total, preview_chars=DEFAULT_PREVIEW_CHARS):
    print(_paint(_clip(item.get("source", "?"), 18), BCYAN) + "  " + _paint(_clip(item.get("date", ""), 30), GREY))
    print("")
    _preview_print(_clip(item.get("title", "(no title)"), MAX_TITLE_CHARS), max_lines=3, fg=BWHITE)
    print("")
    preview = _clip(item.get("summary", ""), preview_chars)
    if preview:
        _preview_print(preview, max_lines=4)
    else:
        print("(no preview)")


def _render_news_detail(item, pos, total):
    # one list, one pager: a pager per field scrolled the lines above a
    # long summary off the screen before the first prompt
    _screen_header("RSS Detail")
    lines = ["[{}/{}] {}".format(pos + 1, total, _clip(item.get("source", "?"), 18))]
    if item.get("date"):
        lines.extend(_wrap_text("Date: {}".format(item["date"])))
    lines.append("Title:")
    lines.extend(_wrap_text(item.get("title", "")))

    summary = item.get("summary", "")
    if summary:
        lines.append("Summary:")
        lines.extend(_wrap_text(summary))

    link = item.get("link", "")
    if link:
        lines.append("Link:")
        lines.extend(_wrap_text(link))
    _paged_lines(lines)


def _load_config():
    data = load_json(CONFIG_FILE)
    if isinstance(data, dict):
        return data
    return {}


def _save_config(config):
    return save_json(CONFIG_FILE, config)


def _decode_entities(text):
    # "&amp;" first, as before: escaped HTML writes its own entities as
    # "&amp;#39;". Then one pass over the pieces after each "&": the old
    # loop indexed the str per character, and on MicroPython every index
    # rescans a str from its start.
    parts = iter(text.replace("&amp;", "&").split("&"))
    out = [next(parts)]
    for part in parts:
        semi = part.find(";", 1, 10)
        rep = None
        if semi > 0:
            name = part[:semi]
            rep = _ENTITIES.get(name)
            if rep is None and name[0] == "#":
                try:
                    if name[1] in "xX":
                        code = int(name[2:], 16)
                    else:
                        code = int(name[1:])
                except (ValueError, IndexError):
                    code = 0
                if 0 < code < 0x110000:
                    rep = chr(code)
        if rep is None:
            out.append("&")
            out.append(part)
        else:
            out.append(rep)
            out.append(part[semi + 1 :])
    return "".join(out)


def _strip_tags(text):
    # Each tag becomes a space ("</p><p>", "<br/>" end words; _clean_text
    # collapses the doubles). Works on the pieces between "<"s: a find()
    # from a position in a long str rescans it from its start on MicroPython.
    if "<" not in text:
        return text
    parts = iter(text.split("<"))
    out = [next(parts)]
    tag = None  # pieces of a tag with a "<" inside (an attribute), to its ">"
    for part in parts:
        if tag is None:
            nxt = part[:1]
            if not (nxt.isalpha() or nxt in ("/", "!", "?")):
                # a bare "<" in text (e.g. "a < b") is not a tag
                out.append("<")
                out.append(part)
                continue
            tag = []
        gt = part.find(">")
        if gt < 0:
            tag.append(part)
            continue
        tag = None
        out.append(" ")
        out.append(part[gt + 1 :])
    if tag:
        out.append("<" + "<".join(tag))  # unterminated: text, not a tag
    return "".join(out)


def _clean_text(text, limit=MAX_SUMMARY_CHARS, html=True):
    # html=False for titles, which are plain text: entities decoded, angle
    # brackets kept (the strips took "<T>" from "Option&lt;T&gt;" and "<u8>"
    # from a CDATA "Vec<u8>")
    value = text if isinstance(text, str) else str(text)  # str() copies a str

    if "<![CDATA[" in value:
        value = value.replace("<![CDATA[", "")
        value = value.replace("]]>", "")

    if html:
        value = _strip_tags(value)
    if "&" in value:
        value = _decode_entities(value)
        if html and "<" in value:
            # entity-escaped HTML (Atom type="html", many RSS descriptions)
            value = _strip_tags(value)
    value = " ".join(value.split())  # newlines and tabs too

    if limit and len(value) > limit:
        return value[:limit]
    return value


def _extract_first_tag(block, tag_names, block_lower=None):
    if isinstance(tag_names, str):
        tag_names = [tag_names]

    lower = block_lower if block_lower is not None else block.lower()
    for name in tag_names:
        tag = name.lower()

        open_tag = "<" + tag + ">"
        close_tag = "</" + tag + ">"
        start = lower.find(open_tag)
        if start >= 0:
            start += len(open_tag)
            end = lower.find(close_tag, start)
            if end > start:
                return block[start:end]

        open_prefix = "<" + tag + " "
        start = lower.find(open_prefix)
        if start >= 0:
            gt_pos = lower.find(">", start)
            if gt_pos >= 0:
                end = lower.find(close_tag, gt_pos + 1)
                if end > gt_pos:
                    return block[gt_pos + 1 : end]

    return ""


def _find_blocks(xml, tag_name, max_items):
    # Searches the feed text itself: item and entry tags are lowercase in any
    # valid feed, and a lower() copy of it cost 26 KB.
    blocks = []
    open_token = "<" + tag_name
    close_token = "</" + tag_name + ">"
    pos = 0

    while len(blocks) < max_items:
        start = xml.find(open_token, pos)
        if start < 0:
            break

        pos = start + len(open_token)
        after = xml[pos : pos + 1]
        if after != ">" and not after.isspace():
            continue  # "<items>" (RSS 1.0's list of links) is not an "<item>"

        gt_pos = xml.find(">", pos)
        if gt_pos < 0:
            break

        end = xml.find(close_token, gt_pos + 1)
        if end < 0:
            break

        blocks.append(xml[gt_pos + 1 : end])
        pos = end + len(close_token)

    return blocks


def _parse_feed(xml_text, max_items):
    items = []
    gc.collect()
    item_blocks = _find_blocks(xml_text, "item", max_items)
    mode = "rss"

    if not item_blocks:
        item_blocks = _find_blocks(xml_text, "entry", max_items)
        mode = "atom"

    for block in item_blocks:
        block_lower = block.lower()
        if mode == "rss":
            title = _extract_first_tag(block, ["title"], block_lower)
            summary = _extract_first_tag(block, ["description", "content:encoded", "summary"], block_lower)
            link = _extract_first_tag(block, ["link"], block_lower)
            date = _extract_first_tag(block, ["pubdate", "updated", "dc:date"], block_lower)
        else:
            title = _extract_first_tag(block, ["title"], block_lower)
            summary = _extract_first_tag(block, ["summary", "content"], block_lower)
            date = _extract_first_tag(block, ["updated", "published"], block_lower)
            link = _extract_first_tag(block, ["link"], block_lower)

            if link == "":
                marker = "<link"
                idx = block_lower.find(marker)
                if idx >= 0:
                    end = block_lower.find(">", idx)
                    if end > idx:
                        tag_text = block[idx:end]
                        href_pos = block_lower[idx:end].find('href="')
                        if href_pos >= 0:
                            href_start = href_pos + 6
                            href_end = tag_text.find('"', href_start)
                            if href_end > href_start:
                                link = tag_text[href_start:href_end]

        clean_title = _clean_text(title, MAX_TITLE_CHARS, html=False)
        clean_summary = _clean_text(summary, MAX_SUMMARY_CHARS)
        clean_link = _clean_text(link, 220)
        clean_date = _clean_text(date, 80)

        if clean_title == "" and clean_summary == "":
            continue

        items.append(
            {
                "title": clean_title,
                "summary": clean_summary,
                "link": clean_link,
                "date": clean_date,
            }
        )

    return items


def _normalize_url(url):
    value = str(url).strip()
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return ""


def _normalize_name(name):
    return _clean_text(str(name), 28)


def _default_config():
    return {
        "feeds": [{"name": item["name"], "url": item["url"]} for item in DEFAULT_FEEDS],
        "preview_chars": DEFAULT_PREVIEW_CHARS,
        "items_per_feed": DEFAULT_ITEMS_PER_FEED,
    }


def _ensure_config(persist=True):
    config = _load_config()
    changed = False

    if not isinstance(config.get("feeds"), list) or not config.get("feeds"):
        config["feeds"] = [{"name": item["name"], "url": item["url"]} for item in DEFAULT_FEEDS]
        changed = True
    else:
        normalized = []
        seen_urls = set()
        for item in config["feeds"]:
            if not isinstance(item, dict):
                changed = True
                continue

            name = _normalize_name(item.get("name", ""))
            url = _normalize_url(item.get("url", ""))
            if name == "" or url == "":
                changed = True
                continue
            if url in seen_urls:
                changed = True
                continue

            seen_urls.add(url)
            if item.get("name") != name or item.get("url") != url or len(item) != 2:
                changed = True
            normalized.append({"name": name, "url": url})
            if len(normalized) >= MAX_FEEDS:
                changed = True
                break

        if not normalized:
            normalized = [{"name": item["name"], "url": item["url"]} for item in DEFAULT_FEEDS]
            changed = True

        config["feeds"] = normalized

    if not isinstance(config.get("preview_chars"), int):
        config["preview_chars"] = DEFAULT_PREVIEW_CHARS
        changed = True

    if not isinstance(config.get("items_per_feed"), int):
        config["items_per_feed"] = DEFAULT_ITEMS_PER_FEED
        changed = True

    preview_value = max(48, min(320, int(config["preview_chars"])))
    items_value = max(1, min(4, int(config["items_per_feed"])))
    if preview_value != config["preview_chars"] or items_value != config["items_per_feed"]:
        changed = True
    config["preview_chars"] = preview_value
    config["items_per_feed"] = items_value

    if changed and persist:
        _save_config(config)

    return config


def reset_feeds():
    config = _default_config()
    _save_config(config)
    print("Feeds reset.")
    return True


def feeds():
    config = _ensure_config(persist=False)
    feed_list = config.get("feeds", [])

    if not feed_list:
        print("No feeds configured.")
        return []

    print("Feeds:")
    lines = []
    for index, item in enumerate(feed_list, start=1):
        name = _clean_text(item.get("name", "?"), 28)
        url = _clean_text(item.get("url", ""), 120)
        lines.append("{}: {}".format(index, name))
        lines.append("   {}".format(_clip(url, 58)))

    _paged_lines(lines)
    return feed_list


def add_feed(name, url):
    feed_name = _normalize_name(name)
    feed_url = _normalize_url(url)

    if feed_name == "":
        print("Empty name.")
        return False
    if feed_url == "":
        print("Invalid URL.")
        return False

    config = _ensure_config(persist=False)
    feed_list = config.get("feeds", [])

    for item in feed_list:
        if item.get("url", "") == feed_url:
            print("Feed URL already exists.")
            return False

    if len(feed_list) >= MAX_FEEDS:
        print("Feed limit:", MAX_FEEDS)
        return False

    feed_list.append({"name": feed_name, "url": feed_url})
    config["feeds"] = feed_list
    _save_config(config)
    print("Added:", feed_name)
    return True


def rm_feed(index_or_name):
    config = _ensure_config(persist=False)
    feed_list = config.get("feeds", [])
    if not feed_list:
        print("No feeds.")
        return False

    removed = None

    try:
        idx = int(index_or_name) - 1
    except Exception:
        idx = None

    if idx is not None and 0 <= idx < len(feed_list):
        removed = feed_list.pop(idx)
    else:
        key = str(index_or_name).strip().lower()
        for pos, item in enumerate(feed_list):
            if str(item.get("name", "")).lower() == key:
                removed = feed_list.pop(pos)
                break

    if removed is None:
        print("Feed not found.")
        return False

    config["feeds"] = feed_list
    _save_config(config)
    print("Removed:", removed.get("name", "?"))
    return True


def set_preview(chars):
    try:
        value = int(chars)
    except Exception:
        print("Invalid number.")
        return False

    value = max(48, min(320, value))
    config = _ensure_config(persist=False)
    config["preview_chars"] = value
    _save_config(config)
    print("Preview chars:", value)
    return value


def set_items_per_feed(count):
    try:
        value = int(count)
    except Exception:
        print("Invalid number.")
        return False

    value = max(1, min(4, value))
    config = _ensure_config(persist=False)
    config["items_per_feed"] = value
    _save_config(config)
    print("Items/feed:", value)
    return value


def _buffer(limit):
    # Allocated before the request: on a fragmented heap the MemoryError then
    # costs nothing, where after it the request (a TLS handshake) was wasted.
    gc.collect()  # one contiguous block: compact the heap first
    return bytearray(limit)


def _read_capped(response, buf):
    # At most len(buf) bytes straight from the socket: response.text would
    # buffer the whole body (130 KB for some feeds) before truncating.
    limit = len(buf)
    view = memoryview(buf)
    got = 0
    while got < limit:
        n = response.raw.readinto(view[got:])
        if not n:
            break
        got += n
    return buf, got


def _decode_text(buf, got):
    view = memoryview(buf)
    for cut in range(4):  # the cap can split a UTF-8 sequence
        try:
            return str(view[: max(0, got - cut)], "utf-8")
        except Exception:
            pass
    for i in range(got):  # not UTF-8 at all: keep the ASCII part
        if buf[i] > 127:
            buf[i] = 63
    return str(view[:got], "utf-8")


def _read_body(response, buf):
    buf, got = _read_capped(response, buf)
    return _decode_text(buf, got)


def _fetch_feed(name, url, per_feed, requests):
    print("RSS>", _clip(name, 24))
    response = None
    start = _ticks_ms()

    try:
        try:
            buf = _buffer(MAX_XML_BYTES)
        except MemoryError:
            # fragmented heap: the head of the feed holds the newest items,
            # and no request has gone out yet
            print("Low memory, reading", MIN_XML_BYTES // 1024, "KB")
            buf = _buffer(MIN_XML_BYTES)
        response = _http_request(requests, "GET", url)
        status = response.status_code
        if status != 200:
            print("HTTP:", status)
            return []
        xml_text = _read_body(response, buf)
        del buf
    except Exception as error:
        print("Fetch err:", _clip(error, 24))
        return []
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    if not xml_text:
        print("Empty feed.")
        return []

    try:
        parsed = _parse_feed(xml_text, per_feed)
    except MemoryError:  # this feed only: the ones before it stay
        print("Out of memory.")
        return []
    elapsed = _ticks_diff(_ticks_ms(), start)
    print("ok", len(parsed), "ms", elapsed)

    for item in parsed:
        item["source"] = name

    return parsed


def latest(feed=None, per_feed=None, show=True):
    global _LAST_ITEMS

    config = _ensure_config(persist=False)
    feed_list = config.get("feeds", [])
    preview_chars = int(config.get("preview_chars", DEFAULT_PREVIEW_CHARS))
    items_per_feed = int(config.get("items_per_feed", DEFAULT_ITEMS_PER_FEED))

    if per_feed is not None:
        try:
            items_per_feed = int(per_feed)
        except Exception:
            pass
    items_per_feed = max(1, min(4, items_per_feed))

    selected = []
    if feed is None:
        selected = feed_list
    else:
        try:
            idx = int(feed) - 1
            if 0 <= idx < len(feed_list):
                selected = [feed_list[idx]]
        except Exception:
            key = str(feed).strip().lower()
            for item in feed_list:
                if str(item.get("name", "")).lower() == key:
                    selected = [item]
                    break

    if not selected:
        print("No feed selected.")
        return 0

    if not check_wifi():
        return 0

    requests = _http_module()
    if requests is None:
        return 0

    collected = []
    # the cache from the start: the old list no longer holds memory while
    # the buffers are allocated, and a stop (q, Ctrl+C) keeps what came
    _LAST_ITEMS = collected
    for item in selected:
        if _poll_key() in ("q", "Q", "esc"):
            print("Stopped.")
            break
        source_name = _clean_text(item.get("name", "feed"), 28)
        source_url = item.get("url", "")
        if _normalize_url(source_url) == "":
            print("Bad URL for", source_name)
            continue

        feed_items = _fetch_feed(source_name, source_url, items_per_feed, requests)
        if feed_items:
            collected.extend(feed_items)

    if not collected:
        print("No news.")
        return 0
    if not show:
        return len(collected)

    # one paged list: per-item paging let the first items scroll off screen
    lines = []
    for index, item in enumerate(collected, start=1):
        title = _clip(item.get("title", "(no title)"), MAX_TITLE_CHARS)
        summary = _clip(item.get("summary", ""), preview_chars)
        source = _clip(item.get("source", "?"), 18)
        lines.append("[{}] {}".format(index, source))
        lines.extend(_wrap_text(title))
        if summary:
            lines.extend(_wrap_text("- " + summary))
        else:
            lines.append("- (no preview)")
        lines.append("")
    lines.append("Tip: view(1) browse / read(1)")

    print("---")
    _paged_lines(lines)
    return len(collected)


def read(index):
    if not _LAST_ITEMS:
        print("No cached news. Run latest().")
        return None

    item, pos = _resolve_cached_item(index)
    if item is None:
        return None

    _render_news_detail(item, pos, len(_LAST_ITEMS))
    return None


def view(index=1):
    if not _LAST_ITEMS:
        print("No cached news. Run latest().")
        return None

    config = _ensure_config(persist=False)
    preview_chars = int(config.get("preview_chars", DEFAULT_PREVIEW_CHARS))
    return _browse_items(
        "RSS News",
        _LAST_ITEMS,
        index,
        lambda item, pos, total: _render_news_summary(item, pos, total, preview_chars),
        _render_news_detail,
    )


# ── Miniflux ────────────────────────────────────
# Unread entries from a Miniflux server, shown in the same viewer.


def mf_setup(url, token):
    """mf_setup('https://feed.example.com', 'API-KEY'): use a dedicated key."""
    base = _normalize_url(url).rstrip("/")
    key = str(token).strip()
    if base == "" or key == "":
        print("Need http(s) URL and API key.")
        return False
    if not save_json(MF_CONFIG_FILE, {"url": base, "token": key}):
        return False
    print("Miniflux:", _clip(base, 40))
    return True


def _mf_config():
    data = load_json(MF_CONFIG_FILE)
    if isinstance(data, dict) and data.get("url") and data.get("token"):
        return data
    print("No Miniflux. Use mf_setup(url, key)")
    return None


def _mf_items(data):
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    items = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        feed = entry.get("feed")
        source = feed.get("title", "") if isinstance(feed, dict) else ""
        content = entry.get("content")  # up to ~45 KB: str() would copy it
        if isinstance(content, str):
            content = content[:MF_CONTENT_CHARS]
            lt = content.rfind("<")
            if lt > content.rfind(">"):
                # a tag cut in two: '<img srcset="...' is not text
                content = content[:lt]
        else:
            content = ""
        items.append(
            {
                "title": _clean_text(entry.get("title", ""), MAX_TITLE_CHARS, html=False),
                "summary": _clean_text(content, MAX_SUMMARY_CHARS),
                "link": str(entry.get("url", "")),
                "date": str(entry.get("published_at", ""))[:16].replace("T", " "),
                "source": _clean_text(source or "Miniflux", 28),
                "mf_id": entry.get("id"),
            }
        )
    return items


def _mf_fetch(config, requests, limit, cap=MF_MAX_BYTES):
    # (status, parsed JSON); data None when the body exceeded `cap`
    url = "{}/v1/entries?status=unread&order=published_at&direction=desc&limit={}".format(
        config["url"], limit
    )
    response = None
    buf = _buffer(cap)  # a MemoryError here comes before any request
    try:
        response = _http_request(
            requests, "GET", url, headers={"X-Auth-Token": config["token"]}
        )
        status = response.status_code
        if status != 200:
            return status, None
        buf, got = _read_capped(response, buf)
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
    if got >= cap:
        return status, None
    view = memoryview(buf)[:got]
    try:
        data = json.loads(view)
    except TypeError:  # CPython wants bytes
        data = json.loads(bytes(view))
    del view, buf
    gc.collect()
    return status, data


def mf(limit=MF_LIMIT):
    """Fetch unread Miniflux entries (newest first), then view()/read()."""
    global _LAST_ITEMS
    config = _mf_config()
    if config is None:
        return 0
    if not check_wifi():
        return 0
    requests = _http_module()
    if requests is None:
        return 0
    try:
        limit = max(1, min(10, int(limit)))
    except Exception:
        limit = MF_LIMIT

    print("Miniflux> unread")
    _LAST_ITEMS = []  # a failed fetch must not leave older RSS items behind
    cap = MF_MAX_BYTES
    while True:
        try:
            status, data = _mf_fetch(config, requests, limit, cap)
        except MemoryError:
            if cap > MF_MIN_BYTES:
                # fragmented heap: one entry in a smaller buffer
                cap = MF_MIN_BYTES
                limit = 1
                gc.collect()
                print("Low memory, fetching 1")
                continue
            print("Out of memory.")
            return 0
        except Exception as error:
            print("Fetch err:", _clip(error, 24))
            return 0
        if status == 401:
            print("Miniflux: bad API key (not retrying).")
            return 0
        if status != 200:
            print("HTTP:", status)
            return 0
        if data is None and limit > 1:
            limit = 1  # long articles: one entry fits where three did not
            print("Large entries, fetching 1")
            continue
        break
    if data is None:
        print("Entry too large.")
        return 0

    try:
        items = _mf_items(data)
    except MemoryError:
        print("Out of memory.")
        return 0
    total = data.get("total", 0) if isinstance(data, dict) else 0
    del data
    gc.collect()
    if not items:
        print("No unread entries.")
        _LAST_ITEMS = []
        return 0
    _LAST_ITEMS = items
    print("Unread: {} (showing {})".format(total, len(items)))
    print("Tip: view(1), then mf_done()")
    return len(items)


def mf_done():
    """Mark the entries fetched by mf() as read on the server."""
    ids = [item["mf_id"] for item in _LAST_ITEMS if item.get("mf_id")]
    if not ids:
        print("No Miniflux entries loaded.")
        return False
    config = _mf_config()
    if config is None or not check_wifi():
        return False
    requests = _http_module()
    if requests is None:
        return False
    response = None
    try:
        response = _http_request(
            requests,
            "PUT",
            config["url"] + "/v1/entries",
            headers={"X-Auth-Token": config["token"], "Content-Type": "application/json"},
            data=json.dumps({"entry_ids": ids, "status": "read"}),
        )
        status = response.status_code
    except Exception as error:
        print("Err:", _clip(error, 24))
        return False
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
    if status not in (200, 204):
        print("HTTP:", status)
        return False
    for item in _LAST_ITEMS:
        item.pop("mf_id", None)
    print("Marked read:", len(ids))
    return True


def add_feed_prompt():
    try:
        name = input("Feed name: ").strip()
    except Exception:
        name = ""
    try:
        url = input("Feed URL: ").strip()
    except Exception:
        url = ""

    if name == "" or url == "":
        print("Cancelled.")
        return False
    return add_feed(name, url)


def setup():
    config = _ensure_config(persist=False)
    print("RSS setup")
    print("Preview:", config.get("preview_chars"))
    print("Items/feed:", config.get("items_per_feed"))
    feeds()


def ver():
    print("rss_news:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- RSS News --")
    print("latest()/l()  Fetch news")
    print("view(#)/v(#)  Browse articles")
    print("read(#)/r(#)  Read full article")
    print("feeds()/f()   List feeds")
    print("add_feed(n,u) Add feed by url")
    print("add_feed_prompt()  Add guided")
    print("rm_feed(#)    Remove feed")
    print("reset_feeds() Restore defaults")
    print("set_preview(n)  Preview chars")
    print("set_items_per_feed(n)  Per feed")
    print("setup()       Show settings")
    print("mf()          Miniflux unread")
    print("mf_done()     Mark them read")
    print("mf_setup(u,k) Miniflux server")
    print("tip: import rss_news as n")


def h():
    return help()


def l(feed=None):
    return latest(feed=feed)


def r(index):
    return read(index)


def v(index=1):
    return view(index)


def f():
    return feeds()


if __name__ == "__main__":
    latest()
