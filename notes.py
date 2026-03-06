import gc
import os
import time

try:
    import ujson as json
except ImportError:
    import json


DATA_FILE = "notes_data.json"
DISPLAY_WIDTH = 32
PAGE_LINES = 8
MAX_NOTES = 50
MAX_NOTE_CHARS = 800
MAX_TITLE_CHARS = 60
MODULE_VERSION = "2026-03-01.2"

_NOTES = None


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
                answer = input("--more-- q=stop: ")
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


def _load_notes():
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save_notes(notes):
    tmp = DATA_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(notes, f)
            try:
                f.flush()
            except Exception:
                pass
    except Exception as e:
        print("Save err:", e)
        return False
    try:
        os.remove(DATA_FILE)
    except Exception:
        pass
    try:
        os.rename(tmp, DATA_FILE)
    except Exception as e:
        print("Rename err:", e)
        return False
    gc.collect()
    return True


def _ensure():
    global _NOTES
    if _NOTES is None:
        _NOTES = _load_notes()
    return _NOTES


def _ts():
    try:
        t = time.gmtime()
        return "{:02d}-{:02d} {:02d}:{:02d}".format(t[1], t[2], t[3], t[4])
    except Exception:
        return ""


def add(text, title=None):
    notes = _ensure()
    if len(notes) >= MAX_NOTES:
        print("Limit:", MAX_NOTES)
        return False

    body = _clip(str(text).strip(), MAX_NOTE_CHARS)
    if body == "":
        print("Empty note.")
        return False

    if title:
        note_title = _clip(str(title).strip(), MAX_TITLE_CHARS)
    else:
        note_title = _clip(body.split("\n")[0], 30)

    note = {"t": note_title, "b": body, "ts": _ts(), "done": False}
    notes.append(note)
    gc.collect()
    _save_notes(notes)
    gc.collect()
    print("Added #{}:".format(len(notes)), _clip(note_title, 26))
    return True


def add_lines(title=None):
    print("Type note. Empty line=done")
    lines = []
    while True:
        try:
            line = input("> ")
        except Exception:
            break
        if line == "":
            break
        lines.append(line)
    if not lines:
        print("Cancelled.")
        return False
    return add("\n".join(lines), title=title)


def add_prompt():
    try:
        title = input("Title: ").strip()
    except Exception:
        title = ""
    try:
        body = input("Note: ").strip()
    except Exception:
        body = ""
    if body == "":
        print("Cancelled.")
        return False
    return add(body, title=title if title else None)


def ls():
    notes = _ensure()
    if not notes:
        print("No notes.")
        return 0

    print("Notes ({}):" .format(len(notes)))
    for i, note in enumerate(notes, 1):
        mark = "[x]" if note.get("done") else "[ ]"
        title = _clip(note.get("t", "?"), 22)
        print("{} {} {}".format(i, mark, title))
    return len(notes)


def show(index):
    notes = _ensure()
    try:
        pos = int(index) - 1
    except Exception:
        print("Invalid index.")
        return None
    if pos < 0 or pos >= len(notes):
        print("Out of range.")
        return None

    note = notes[pos]
    mark = " [DONE]" if note.get("done") else ""
    print("#{}{}".format(pos + 1, mark))
    if note.get("ts"):
        print("Date:", note["ts"])
    _paged_print(note.get("t", ""))
    print("---")
    _paged_print(note.get("b", ""))
    return None


def done(index):
    notes = _ensure()
    try:
        pos = int(index) - 1
    except Exception:
        print("Invalid index.")
        return False
    if pos < 0 or pos >= len(notes):
        print("Out of range.")
        return False
    notes[pos]["done"] = True
    _save_notes(notes)
    print("Done:", _clip(notes[pos].get("t", "?"), 26))
    return True


def undone(index):
    notes = _ensure()
    try:
        pos = int(index) - 1
    except Exception:
        print("Invalid index.")
        return False
    if pos < 0 or pos >= len(notes):
        print("Out of range.")
        return False
    notes[pos]["done"] = False
    _save_notes(notes)
    print("Undone:", _clip(notes[pos].get("t", "?"), 26))
    return True


def rm(index):
    notes = _ensure()
    try:
        pos = int(index) - 1
    except Exception:
        print("Invalid index.")
        return False
    if pos < 0 or pos >= len(notes):
        print("Out of range.")
        return False
    removed = notes.pop(pos)
    _save_notes(notes)
    print("Removed:", _clip(removed.get("t", "?"), 26))
    return True


def clear_done():
    notes = _ensure()
    removed = 0
    i = len(notes) - 1
    while i >= 0:
        if notes[i].get("done"):
            notes.pop(i)
            removed += 1
        i -= 1
    if removed > 0:
        _save_notes(notes)
    print("Cleared:", removed, "done notes")
    return removed


def edit(index, text):
    notes = _ensure()
    try:
        pos = int(index) - 1
    except Exception:
        print("Invalid index.")
        return False
    if pos < 0 or pos >= len(notes):
        print("Out of range.")
        return False
    body = _clip(str(text).strip(), MAX_NOTE_CHARS)
    if body == "":
        print("Empty text.")
        return False
    notes[pos]["b"] = body
    notes[pos]["ts"] = _ts()
    _save_notes(notes)
    print("Updated #{}".format(pos + 1))
    return True


def count():
    notes = _ensure()
    total = len(notes)
    done_count = sum(1 for n in notes if n.get("done"))
    print("Total:", total, "| Done:", done_count, "| Open:", total - done_count)
    return {"total": total, "done": done_count, "open": total - done_count}


def view(index=1):
    notes = _ensure()
    if not notes:
        print("No notes.")
        return None

    try:
        pos = int(index) - 1
    except Exception:
        print("Invalid index.")
        return None

    total = len(notes)
    if pos < 0 or pos >= total:
        print("Out of range.")
        return None

    while True:
        note = notes[pos]
        mark = " [DONE]" if note.get("done") else ""
        print("---")
        print("[{}/{}]{}".format(pos + 1, total, mark))
        if note.get("ts"):
            print(note["ts"])
        _paged_print(_clip(note.get("t", "?"), MAX_TITLE_CHARS))
        print("---")
        preview = _clip(note.get("b", ""), 100)
        _paged_print(preview)

        try:
            cmd = _normalize_nav_cmd(input("n/p/d/q/#/arrows: "))
        except Exception:
            cmd = "q"

        if cmd == "q":
            return note
        if cmd == "n":
            pos = (pos + 1) % total
            continue
        if cmd == "p":
            pos = (pos - 1) % total
            continue
        if cmd == "d":
            show(pos + 1)
            continue

        try:
            jump = int(cmd) - 1
            if 0 <= jump < total:
                pos = jump
            else:
                print("Out of range.")
        except Exception:
            print("Use n/p/d/q/# or arrows")


def ver():
    print("notes:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("cmd: add add_lines add_prompt")
    print("cmd: ls show view edit rm")
    print("cmd: done undone clear_done count")
    print("cmd: ver help h")
    print("tip: import notes as t")


def h():
    return help()


def l():
    return ls()


def s(index):
    return show(index)


def v(index=1):
    return view(index)
