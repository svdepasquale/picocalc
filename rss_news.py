import gc
import time

from pico_utils import clip as _clip
from pico_utils import paged_print as _paged_print
from pico_utils import paged_lines as _paged_lines
from pico_utils import preview_print as _preview_print
from pico_utils import browse_items as _browse_items
from pico_utils import load_json, save_json, http_module as _http_module, check_wifi
from pico_utils import ticks_ms as _ticks_ms, ticks_diff as _ticks_diff
from pico_utils import screen_header as _screen_header


CONFIG_FILE = "rss_feeds.json"
MAX_XML_CHARS = 26000
MAX_TITLE_CHARS = 140
MAX_SUMMARY_CHARS = 480
DEFAULT_PREVIEW_CHARS = 110
DEFAULT_ITEMS_PER_FEED = 2
MAX_FEEDS = 12
MODULE_VERSION = "2026-03-28.2"

DEFAULT_FEEDS = [
    {"name": "CNN", "url": "https://rss.cnn.com/rss/edition.rss"},
    {"name": "ANSA", "url": "https://www.ansa.it/sito/notizie/topnews/topnews_rss.xml"},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
]

_LAST_ITEMS = []


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
    print("[{}/{}] {}".format(pos + 1, total, _clip(item.get("source", "?"), 18)))
    if item.get("date"):
        _preview_print(_clip(item.get("date"), 80), max_lines=1)
    print("Title:")
    _preview_print(_clip(item.get("title", "(no title)"), MAX_TITLE_CHARS), max_lines=3)
    print("---")
    preview = _clip(item.get("summary", ""), preview_chars)
    if preview:
        _preview_print(preview, max_lines=4)
    else:
        print("(no preview)")


def _render_news_detail(item, pos, total):
    _screen_header("RSS Detail")
    print("[{}/{}] {}".format(pos + 1, total, _clip(item.get("source", "?"), 18)))
    if item.get("date"):
        print("Date:", item.get("date"))
    print("Title:")
    _paged_print(item.get("title", ""))

    summary = item.get("summary", "")
    if summary:
        print("Summary:")
        _paged_print(summary)

    link = item.get("link", "")
    if link:
        print("Link:")
        _paged_print(link)


def _load_config():
    data = load_json(CONFIG_FILE)
    if isinstance(data, dict):
        return data
    return {}


def _save_config(config):
    return save_json(CONFIG_FILE, config)


def _decode_entities(text):
    value = str(text)
    if "&" not in value:
        return value
    value = value.replace("&amp;", "&")
    value = value.replace("&lt;", "<")
    value = value.replace("&gt;", ">")
    value = value.replace("&quot;", '"')
    value = value.replace("&apos;", "'")
    value = value.replace("&nbsp;", " ")
    if "&#" in value:
        out = []
        i = 0
        while i < len(value):
            if value[i:i + 2] == "&#" and i + 2 < len(value):
                sc = value.find(";", i + 2)
                if sc > 0 and sc - i < 10:
                    ref = value[i + 2:sc]
                    try:
                        if ref and ref[0] in ("x", "X"):
                            cp = int(ref[1:], 16)
                        else:
                            cp = int(ref)
                        if 0 < cp < 0x10000:
                            out.append(chr(cp))
                            i = sc + 1
                            continue
                    except (ValueError, IndexError):
                        pass
            out.append(value[i])
            i += 1
        value = "".join(out)
    return value


def _strip_tags(text):
    source = str(text)
    out = []
    pos = 0
    while pos < len(source):
        lt = source.find("<", pos)
        if lt < 0:
            out.append(source[pos:])
            break
        if lt > pos:
            out.append(source[pos:lt])
        gt = source.find(">", lt)
        if gt < 0:
            break
        pos = gt + 1
    return "".join(out)


def _clean_text(text, limit=MAX_SUMMARY_CHARS):
    value = str(text)

    if "<![CDATA[" in value:
        value = value.replace("<![CDATA[", "")
        value = value.replace("]]>", "")

    value = value.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    value = _strip_tags(value)
    value = _decode_entities(value)
    value = " ".join(value.split())

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


def _find_blocks(xml_lower, xml_orig, tag_name, max_items):
    blocks = []
    open_token = "<" + tag_name
    close_token = "</" + tag_name + ">"
    pos = 0

    while len(blocks) < max_items:
        start = xml_lower.find(open_token, pos)
        if start < 0:
            break

        gt_pos = xml_lower.find(">", start)
        if gt_pos < 0:
            break

        end = xml_lower.find(close_token, gt_pos + 1)
        if end < 0:
            break

        blocks.append(xml_orig[gt_pos + 1 : end])
        pos = end + len(close_token)

    return blocks


def _parse_feed(xml_text, max_items):
    items = []
    gc.collect()
    xml_lower = xml_text.lower()
    item_blocks = _find_blocks(xml_lower, xml_text, "item", max_items)
    mode = "rss"

    if not item_blocks:
        item_blocks = _find_blocks(xml_lower, xml_text, "entry", max_items)
        mode = "atom"

    del xml_lower

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

        clean_title = _clean_text(title, MAX_TITLE_CHARS)
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


def _fetch_feed(name, url, per_feed, requests):
    print("RSS>", _clip(name, 24))
    response = None
    start = _ticks_ms()

    try:
        response = requests.get(url)
        status = response.status_code
        if status != 200:
            print("HTTP:", status)
            return []
        xml_text = response.text
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

    if len(xml_text) > MAX_XML_CHARS:
        xml_text = xml_text[:MAX_XML_CHARS]
    parsed = _parse_feed(xml_text, per_feed)
    elapsed = _ticks_diff(_ticks_ms(), start)
    print("ok", len(parsed), "ms", elapsed)

    for item in parsed:
        item["source"] = name

    del xml_text
    gc.collect()

    return parsed


def latest(feed=None, per_feed=None):
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
    for item in selected:
        source_name = _clean_text(item.get("name", "feed"), 28)
        source_url = item.get("url", "")
        if _normalize_url(source_url) == "":
            print("Bad URL for", source_name)
            continue

        feed_items = _fetch_feed(source_name, source_url, items_per_feed, requests)
        if feed_items:
            collected.extend(feed_items)

    _LAST_ITEMS = collected

    if not collected:
        print("No news.")
        return 0

    print("---")
    for index, item in enumerate(collected, start=1):
        title = _clip(item.get("title", "(no title)"), MAX_TITLE_CHARS)
        summary = _clip(item.get("summary", ""), preview_chars)
        source = _clip(item.get("source", "?"), 18)
        print("[{}] {}".format(index, source))
        _paged_print(title)
        if summary:
            _paged_print("- " + summary)
        else:
            print("- (no preview)")
        print("")

    print("Tip: view(1) browse / read(1)")
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
