import gc
import os
import time

try:
    import ujson as json
except ImportError:
    import json


DISPLAY_WIDTH = 32
PAGE_LINES = 8
MODULE_VERSION = "2026-03-28.2"


def clip(text, limit):
    value = str(text)
    if len(value) <= limit:
        return value
    return value[:limit]


def wrap_text(text, width=DISPLAY_WIDTH):
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


def clear_screen():
    print("\x1b[2J\x1b[H", end="")


def screen_header(title):
    clear_screen()
    bar = "=" * DISPLAY_WIDTH
    print(bar)
    print(title.center(DISPLAY_WIDTH))
    print(bar)


def paged_print(text, page_lines=PAGE_LINES):
    lines = wrap_text(text)
    if not lines:
        print("(empty)")
        return

    count = 0
    total = len(lines)
    for index, line in enumerate(lines):
        print(line)
        count += 1
        if count >= page_lines and index < total - 1:
            try:
                answer = input("Enter=next q=stop: ")
            except Exception:
                answer = ""
            cmd = normalize_nav_cmd(answer)
            if cmd == "q":
                print("(stopped)")
                break
            count = 0


def preview_lines(text, width=DISPLAY_WIDTH, max_lines=PAGE_LINES):
    lines = wrap_text(text, width)
    if max_lines is None or max_lines < 1 or len(lines) <= max_lines:
        return lines

    shown = lines[:max_lines]
    if shown:
        tail = shown[-1]
        max_tail = width - 3
        if max_tail < 0:
            max_tail = 0
        if len(tail) > max_tail:
            tail = tail[:max_tail]
        shown[-1] = tail + "..."
    return shown


def preview_print(text, width=DISPLAY_WIDTH, max_lines=PAGE_LINES):
    lines = preview_lines(text, width=width, max_lines=max_lines)
    if not lines:
        print("(empty)")
        return 0
    for line in lines:
        print(line)
    return len(lines)


def paged_lines(lines, page_lines=PAGE_LINES):
    if not lines:
        print("(empty)")
        return 0

    count = 0
    total = len(lines)
    for index, line in enumerate(lines):
        print(line)
        count += 1
        if count >= page_lines and index < total - 1:
            cmd = normalize_nav_cmd(safe_input("Enter=next q=stop: "))
            if cmd == "q":
                print("(stopped)")
                break
            count = 0
    return total


def format_bytes(size):
    try:
        value = int(size)
    except Exception:
        return "?"

    if value < 1024:
        return "{}B".format(value)
    if value < 1024 * 1024:
        return "{}KB".format(value // 1024)
    return "{}MB".format(value // (1024 * 1024))


def ticks_ms():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.time() * 1000)


def ticks_diff(current, start):
    if hasattr(time, "ticks_diff"):
        return time.ticks_diff(current, start)
    return int(current) - int(start)


def sleep_ms(ms):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(ms)
        return
    time.sleep(ms / 1000.0)


def browse_items(
    title,
    items,
    start_index=1,
    render_summary=None,
    render_detail=None,
    nav_prompt="n/p/d/q/#/arrows: ",
):
    if not items:
        print("No items.")
        return None

    try:
        pos = int(start_index) - 1
    except Exception:
        print("Invalid index.")
        return None

    total = len(items)
    if pos < 0 or pos >= total:
        print("Out of range.")
        return None

    while True:
        screen_header(title)
        item = items[pos]

        if render_summary is not None:
            render_summary(item, pos, total)

        print("=" * DISPLAY_WIDTH)
        print("n/p move  d detail")
        print("q quit   # jump")

        cmd = normalize_nav_cmd(safe_input(nav_prompt))
        if cmd == "":
            continue
        if cmd == "q":
            clear_screen()
            return item
        if cmd == "n":
            pos = (pos + 1) % total
            continue
        if cmd == "p":
            pos = (pos - 1) % total
            continue
        if cmd == "d":
            if render_detail is not None:
                clear_screen()
                render_detail(item, pos, total)
                safe_input("Enter=back: ")
            continue

        try:
            jump = int(cmd) - 1
            if 0 <= jump < total:
                pos = jump
            else:
                print("Out of range.")
                safe_input("Enter=back: ")
        except Exception:
            print("Use n/p/d/q/# or arrows")
            safe_input("Enter=back: ")


def normalize_nav_cmd(raw):
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


def load_json(filepath):
    for path in (filepath, filepath + ".tmp", filepath + ".bak"):
        try:
            with open(path, "r") as f:
                data = json.load(f)
                if isinstance(data, (dict, list)):
                    return data
        except Exception:
            pass
    return None


def save_json(filepath, data):
    tmp = filepath + ".tmp"
    bak = filepath + ".bak"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
            try:
                f.flush()
            except Exception:
                pass
    except Exception as e:
        print("Save err:", e)
        return False
    try:
        os.sync()
    except Exception:
        pass
    try:
        os.remove(bak)
    except Exception:
        pass
    try:
        os.rename(filepath, bak)
    except Exception:
        pass
    try:
        os.rename(tmp, filepath)
    except Exception as e:
        print("Rename err:", e)
        try:
            os.rename(bak, filepath)
        except Exception:
            pass
        return False
    try:
        os.remove(bak)
    except Exception:
        pass
    gc.collect()
    return True


def safe_input(prompt):
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""


def http_module():
    try:
        import urequests as requests

        return requests
    except ImportError:
        print("Missing urequests.")
        print("Install: import mip; mip.install('urequests')")
        return None


def check_wifi():
    try:
        import network
    except ImportError:
        print("No network module.")
        return False
    try:
        w = network.WLAN(network.STA_IF)
        if not w.isconnected():
            print("No WiFi. Connect first.")
            return False
    except Exception:
        print("WiFi check error.")
        return False
    return True


def ver():
    print("pico_utils:", MODULE_VERSION)
    return MODULE_VERSION
