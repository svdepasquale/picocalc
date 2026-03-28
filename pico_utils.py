import gc
import os

try:
    import ujson as json
except ImportError:
    import json


DISPLAY_WIDTH = 32
PAGE_LINES = 8
MODULE_VERSION = "2026-03-28.1"


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
