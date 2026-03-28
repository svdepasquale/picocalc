import gc
import time

from pico_utils import clip as _clip
from pico_utils import paged_print as _paged_print
from pico_utils import paged_lines as _paged_lines
from pico_utils import preview_print as _preview_print
from pico_utils import browse_items as _browse_items
from pico_utils import load_json, save_json
from pico_utils import screen_header as _screen_header


DATA_FILE = "notes_data.json"
MAX_NOTES = 50
MAX_NOTE_CHARS = 800
MAX_TITLE_CHARS = 60
MODULE_VERSION = "2026-03-28.2"

_NOTES = None


def _load_notes():
    data = load_json(DATA_FILE)
    if isinstance(data, list):
        return data
    return []


def _save_notes(notes):
    return save_json(DATA_FILE, notes)


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
    if not _save_notes(notes):
        notes.pop()
        print("Save failed.")
        return False
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


def _resolve_note(index):
    notes = _ensure()
    try:
        pos = int(index) - 1
    except Exception:
        print("Invalid index.")
        return notes, None
    if pos < 0 or pos >= len(notes):
        print("Out of range.")
        return notes, None
    return notes, pos


def _render_note_summary(note, pos, total):
    state = "DONE" if note.get("done") else "OPEN"
    print("[{}/{}] {}".format(pos + 1, total, state))
    if note.get("ts"):
        print(note["ts"])
    print("Title:")
    _preview_print(_clip(note.get("t", "?"), MAX_TITLE_CHARS), max_lines=2)
    print("---")
    _preview_print(_clip(note.get("b", ""), 180), max_lines=4)


def _render_note_detail(note, pos, total):
    _screen_header("Note Detail")
    mark = " [DONE]" if note.get("done") else ""
    print("#{}/{}{}".format(pos + 1, total, mark))
    if note.get("ts"):
        print("Date:", note["ts"])
    print("Title:")
    _paged_print(note.get("t", ""))
    print("---")
    print("Body:")
    _paged_print(note.get("b", ""))


def ls():
    notes = _ensure()
    if not notes:
        print("No notes.")
        return 0

    print("Notes ({}):" .format(len(notes)))
    lines = []
    for i, note in enumerate(notes, 1):
        mark = "[x]" if note.get("done") else "[ ]"
        title = _clip(note.get("t", "?"), 22)
        lines.append("{} {} {}".format(i, mark, title))
    _paged_lines(lines)
    return len(notes)


def show(index):
    notes, pos = _resolve_note(index)
    if pos is None:
        return None
    note = notes[pos]
    _render_note_detail(note, pos, len(notes))
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
    if not _save_notes(notes):
        notes[pos]["done"] = False
        print("Save failed.")
        return False
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
    if not _save_notes(notes):
        notes[pos]["done"] = True
        print("Save failed.")
        return False
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
    if not _save_notes(notes):
        notes.insert(pos, removed)
        print("Save failed.")
        return False
    print("Removed:", _clip(removed.get("t", "?"), 26))
    return True


def clear_done():
    notes = _ensure()
    remaining = [n for n in notes if not n.get("done")]
    removed = len(notes) - len(remaining)
    if removed > 0:
        if not _save_notes(remaining):
            print("Save failed.")
            return 0
        notes[:] = remaining
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
    old_body = notes[pos]["b"]
    old_ts = notes[pos].get("ts", "")
    notes[pos]["b"] = body
    notes[pos]["ts"] = _ts()
    if not _save_notes(notes):
        notes[pos]["b"] = old_body
        notes[pos]["ts"] = old_ts
        print("Save failed.")
        return False
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
    return _browse_items("Notes", notes, index, _render_note_summary, _render_note_detail)


def ver():
    print("notes:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Notes --")
    print("add(text)     Add a note")
    print("add_lines()   Multi-line note")
    print("add_prompt()  Guided add")
    print("ls()/l()      List all notes")
    print("show(#)/s(#)  Show full note")
    print("view(#)/v(#)  Browse notes")
    print("edit(#,text)  Edit note body")
    print("rm(#)         Delete note")
    print("done(#)       Mark as done")
    print("undone(#)     Mark as open")
    print("clear_done()  Remove done notes")
    print("count()       Show stats")
    print("tip: import notes as t")


def h():
    return help()


def l():
    return ls()


def s(index):
    return show(index)


def v(index=1):
    return view(index)
