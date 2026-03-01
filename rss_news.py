import gc
import os
import time

try:
    import ujson as json
except ImportError:
    import json


CONFIG_FILE = "rss_feeds.json"
DISPLAY_WIDTH = 32
PAGE_LINES = 8
MAX_XML_CHARS = 26000
MAX_TITLE_CHARS = 140
MAX_SUMMARY_CHARS = 480
DEFAULT_PREVIEW_CHARS = 110
DEFAULT_ITEMS_PER_FEED = 2
MAX_FEEDS = 12
MODULE_VERSION = "2026-03-01.9"

DEFAULT_FEEDS = [
    {"name": "CNN", "url": "https://rss.cnn.com/rss/edition.rss"},
    {"name": "ANSA", "url": "https://www.ansa.it/sito/notizie/topnews/topnews_rss.xml"},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
]

_LAST_ITEMS = []


def _clip(text, limit):
    value = str(text)
    if len(value) <= limit:
        return value
    return value[:limit]


def _wrap_text(text, width=DISPLAY_WIDTH):
    source = str(text).replace("\r\n", "\n").replace("\r", "\n")
    wrapped = []
    for paragraph in source.split("\n"):
        paragraph = paragraph.strip()
        if paragraph == "":
            wrapped.append("")
            continue

        line = ""
        for word in paragraph.split(" "):
            if word == "":
                continue
            if line == "":
                if len(word) <= width:
                    line = word
                else:
                    start = 0
                    while start < len(word):
                        wrapped.append(word[start : start + width])
                        start += width
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                wrapped.append(line)
                if len(word) <= width:
                    line = word
                else:
                    start = 0
                    while start < len(word):
                        wrapped.append(word[start : start + width])
                        start += width
                    line = ""
        if line:
            wrapped.append(line)
    return wrapped


def _paged_print(text):
    lines = _wrap_text(text)
    if not lines:
        print("(empty)")
        return

    count = 0
    total = len(lines)
    for index, line in enumerate(lines):
        print(line)
        count += 1
        if count >= PAGE_LINES and index < total - 1:
            try:
                answer = input("--more-- Enter/n/→ next, q/↓ stop: ")
            except Exception:
                answer = ""
            cmd = _normalize_nav_cmd(answer)
            if cmd == "q":
                print("(stopped)")
                break
            count = 0


def _normalize_nav_cmd(raw):
    cmd = str(raw).strip().lower()
    if cmd == "":
        return ""

    if cmd in ("\x1b[c", "\x1boc", "right"):
        return "n"
    if cmd in ("\x1b[d", "\x1bod", "left"):
        return "p"
    if cmd in ("\x1b[a", "\x1boa", "up"):
        return "d"
    if cmd in ("\x1b[b", "\x1bob", "down"):
        return "q"

    if cmd.endswith("[c"):
        return "n"
    if cmd.endswith("[d"):
        return "p"
    if cmd.endswith("[a"):
        return "d"
    if cmd.endswith("[b"):
        return "q"

    return cmd


def _load_config():
    try:
        with open(CONFIG_FILE, "r") as file:
            data = json.load(file)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_config(config):
    tmp_file = CONFIG_FILE + ".tmp"
    try:
        with open(tmp_file, "w") as file:
            json.dump(config, file)
            try:
                file.flush()
            except Exception:
                pass
    except Exception as error:
        print("Save err:", error)
        return False

    try:
        os.sync()
    except Exception:
        pass

    try:
        os.remove(CONFIG_FILE)
    except Exception:
        pass

    try:
        os.rename(tmp_file, CONFIG_FILE)
    except Exception as error:
        print("Rename err:", error)
        return False

    gc.collect()
    return True


def _http_module():
    try:
        import urequests as requests

        return requests
    except ImportError:
        print("Missing urequests.")
        print("Install: import mip; mip.install('urequests')")
        return None


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
    for index, item in enumerate(feed_list, start=1):
        name = _clean_text(item.get("name", "?"), 28)
        url = _clean_text(item.get("url", ""), 120)
        print("{}: {}".format(index, name))
        print("   {}".format(_clip(url, 58)))

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
    start = time.ticks_ms()

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
    elapsed = time.ticks_diff(time.ticks_ms(), start)
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
        return []

    requests = _http_module()
    if requests is None:
        return []

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
        return []

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
    return collected


def read(index):
    if not _LAST_ITEMS:
        print("No cached news. Run latest().")
        return None

    try:
        pos = int(index) - 1
    except Exception:
        print("Invalid index.")
        return None

    if pos < 0 or pos >= len(_LAST_ITEMS):
        print("Out of range.")
        return None

    item = _LAST_ITEMS[pos]

    print("Source:", item.get("source", "?"))
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

    return item


def view(index=1):
    if not _LAST_ITEMS:
        print("No cached news. Run latest().")
        return None

    try:
        pos = int(index) - 1
    except Exception:
        print("Invalid index.")
        return None

    total = len(_LAST_ITEMS)
    if pos < 0 or pos >= total:
        print("Out of range.")
        return None

    config = _ensure_config(persist=False)
    preview_chars = int(config.get("preview_chars", DEFAULT_PREVIEW_CHARS))

    while True:
        item = _LAST_ITEMS[pos]
        print("---")
        print("[{}/{}] {}".format(pos + 1, total, _clip(item.get("source", "?"), 18)))
        if item.get("date"):
            print("Date:", _clip(item.get("date"), 80))
        print("Title:")
        _paged_print(_clip(item.get("title", "(no title)"), MAX_TITLE_CHARS))

        preview = _clip(item.get("summary", ""), preview_chars)
        if preview:
            print("Preview:")
            _paged_print(preview)
        else:
            print("Preview: (none)")

        try:
            cmd = _normalize_nav_cmd(input("n/p/d/q/#/arrows: "))
        except Exception:
            cmd = "q"

        if cmd == "q":
            return item
        if cmd == "n":
            if total > 0:
                pos = (pos + 1) % total
            continue
        if cmd == "p":
            if total > 0:
                pos = (pos - 1) % total
            continue
        if cmd == "d":
            read(pos + 1)
            continue

        try:
            jump = int(cmd) - 1
            if 0 <= jump < total:
                pos = jump
            else:
                print("Out of range.")
        except Exception:
            print("Use n/p/d/q/# or arrows")


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
    print("cmd: latest view read")
    print("cmd: feeds add_feed rm_feed")
    print("cmd: add_feed_prompt reset_feeds")
    print("cmd: set_preview set_items_per_feed")
    print("cmd: setup ver help h")
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
