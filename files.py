"""Files: browse flash (/) and the SD card (/sd), view text, edit with the
firmware's editor (pye), run .py files, make files and folders, delete.
It never asks the SD card for free space: the first statvfs() on a big card
scans the whole FAT (over a minute on 32 GB)."""

import gc
import os

from pico_utils import DISPLAY_WIDTH, PAGE_LINES
from pico_utils import clear_screen as _clear_screen, ensure_screen as _ensure_screen
from pico_utils import format_bytes as _format_bytes, key_bar as _key_bar
from pico_utils import paint as _paint, pick as _pick, read_key as _read_key
from pico_utils import read_line as _read_line, screen_header as _screen_header
from pico_utils import wait_key as _wait_key
from pico_utils import GREY


MODULE_VERSION = "2026-10-04.1"
VIEW_MAX = 32768  # bytes the viewer reads from one file
EDIT_MAX = 32768  # the editor holds the whole file in RAM, several times over
_DIR = 0x4000
_QUIT = ("q", "Q", "esc", "eof")
_FOOTER = (
    (("Enter", "open"), ("←", "up"), ("e", "edit"), ("r", "run"), ("d", "delete")),
    (("n", "new file"), ("m", "new folder"), ("editor", "^S save, Esc quit")),
)
# Control bytes the terminal would act on instead of drawing.
_CONTROL = ("\x00", "\x07", "\x08", "\x0b", "\x0c", "\x1b")


# ── paths and listing ───────────────────────────


def join(folder, name):
    return folder.rstrip("/") + "/" + name


def parent(path):
    return path.rstrip("/").rsplit("/", 1)[0] or "/"


def _ilistdir(folder):
    if hasattr(os, "ilistdir"):
        return os.ilistdir(folder)
    out = []  # CPython: same tuples from stat()
    for name in os.listdir(folder):
        st = os.stat(join(folder, name))
        out.append((name, st[0] & 0xF000, 0, st[6]))
    return out


def entries(folder):
    """[(name, is_dir, size)]: folders first (size 0), then files, by name."""
    out = []
    for item in _ilistdir(folder):
        is_dir = (item[1] & _DIR) != 0
        if is_dir:
            size = 0
        elif len(item) > 3:
            size = item[3]
        else:  # ports whose ilistdir has no size field (unix)
            size = os.stat(join(folder, item[0]))[6]
        out.append((item[0], is_dir, size))
    out.sort(key=lambda entry: (not entry[1], entry[0].lower()))
    return out


# ── text ────────────────────────────────────────


def is_binary(sample):
    """True when bytes don't look like text: a NUL, or over 10% control
    bytes other than tab, newlines and ESC (UTF-8 bytes count as text)."""
    sample = sample[:512]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    bad = 0
    for byte in sample:
        if byte < 9 or (13 < byte < 32 and byte != 27):
            bad += 1
    return bad * 10 > len(sample)


def _utf8_end(data):
    # Length without a multi-byte character cut off by a capped read.
    end = len(data)
    for back in (1, 2, 3):
        if back > end:
            break
        byte = data[end - back]
        if byte & 0xC0 == 0x80:  # continuation byte: look for the lead
            continue
        if byte >= 0xC0 and (2 if byte < 0xE0 else 3 if byte < 0xF0 else 4) > back:
            return end - back
        break
    return end


def decode(data):
    """Bytes to text; a cut multi-byte character at the end is dropped,
    other invalid UTF-8 shows as '?'."""
    try:
        return str(data[: _utf8_end(data)], "utf-8")
    except Exception:
        return "".join(chr(b) if b < 128 else "?" for b in data)


def text_lines(text, width=DISPLAY_WIDTH):
    """Screen lines: hard-wrapped at `width`, indentation and blank lines
    kept, tabs as 4 spaces, terminal control bytes as '?'."""
    for ch in _CONTROL:
        if ch in text:
            text = text.replace(ch, "?")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    if text.endswith("\n"):
        text = text[:-1]
    out = []
    for line in text.split("\n"):
        while len(line) > width:
            out.append(line[:width])
            line = line[width:]
        out.append(line)
    return out


def _short(path, limit=30):
    return path if len(path) <= limit else ".." + path[-(limit - 2) :]


# ── actions ─────────────────────────────────────


def pager(title, lines):
    """Page through `lines`: Space/Enter/arrows move a page, q closes."""
    rows = max(3, PAGE_LINES)
    top = 0
    total = len(lines)
    while True:
        _screen_header("{}  {}-{}/{}".format(title, top + 1 if total else 0, min(top + rows, total), total))
        for line in lines[top : top + rows]:
            print(line)
        print(_paint("─" * DISPLAY_WIDTH, GREY))
        _key_bar((("Space/↓", "page"), ("↑", "back"), ("q", "close")))
        try:
            key = _read_key()
        except KeyboardInterrupt:
            key = "esc"
        if key in _QUIT or key == "left":
            return
        if key in (" ", "enter", "down", "right", "pgdn") and top + rows < total:
            top += rows
        elif key in ("up", "pgup", "b") and top > 0:
            top = max(0, top - rows)


def view(path):
    """Show a text file (its first VIEW_MAX bytes)."""
    try:
        size = os.stat(path)[6]
        gc.collect()
        with open(path, "rb") as f:
            data = f.read(VIEW_MAX)
    except OSError as error:
        print("Can't read {}: {}".format(path, error))
        _wait_key()
        return False
    if is_binary(data):
        _screen_header(_short(path))
        print("Binary file, {}.".format(_format_bytes(size)))
        _wait_key()
        return False
    lines = text_lines(decode(data))
    data = None
    if size > VIEW_MAX:
        lines.append(_paint("[first {} of {}]".format(_format_bytes(VIEW_MAX), _format_bytes(size)), GREY))
    pager(_short(path), lines)
    return True


def edit(path):
    """Open `path` in the firmware's editor; a new name makes a new file.
    Ctrl+S saves, Esc (or Ctrl+Q) quits."""
    try:
        import picocalc

        editor = picocalc.edit
    except (ImportError, AttributeError):
        print("No editor on this firmware.")
        _wait_key()
        return False
    try:
        size = os.stat(path)[6]
    except OSError:
        size = 0  # new file: the editor saves it
    if size > EDIT_MAX:
        print("Too big to edit here ({}): the editor keeps it all in RAM.".format(_format_bytes(size)))
        _wait_key()
        return False
    gc.collect()
    try:
        editor(path)
    finally:
        _ensure_screen()
        _clear_screen()
    return True


def run(path):
    """Run a .py file the way the Apps list does."""
    import apps

    return apps.run_app(path, path.rsplit("/", 1)[-1])


def delete(path, is_dir=False):
    """Remove a file or an empty folder after a y."""
    print("Delete {}{}? y/n".format(_short(path, 40), "/" if is_dir else ""))
    if _read_key() not in ("y", "Y"):
        return False
    try:
        if is_dir:
            os.rmdir(path)
        else:
            os.remove(path)
    except OSError as error:
        print("Can't delete: {}".format(error))
        _wait_key()
        return False
    return True


def _ask_name(prompt):
    name = _read_line(prompt)
    if not name:
        return None
    name = name.strip()
    if not name or "/" in name or name in (".", ".."):
        print("Not a valid name.")
        _wait_key()
        return None
    return name


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _new_file(folder):
    name = _ask_name("New file name, then Enter: ")
    if name is None:
        return None
    path = join(folder, name)
    if _exists(path):
        print("Already there.")
        _wait_key()
        return None
    edit(path)
    return name


def _new_folder(folder):
    name = _ask_name("New folder name, then Enter: ")
    if name is None:
        return None
    try:
        os.mkdir(join(folder, name))
    except OSError as error:
        print("Can't make it: {}".format(error))
        _wait_key()
        return None
    return name


# ── browser ─────────────────────────────────────


def browse(path="/"):
    """File browser from `path`; q goes back."""
    folder = path
    want = None  # name to highlight after a change of folder or a new item
    pos = 0
    while True:
        try:
            items = entries(folder)
        except OSError as error:
            print("Can't list {}: {}".format(folder, error))
            _wait_key()
            if folder == "/":
                return None
            folder, want = parent(folder), None
            continue
        if want is not None:
            pos = 0
            for i, item in enumerate(items):
                if item[0] == want:
                    pos = i
            want = None
        labels = [name + "/" if is_dir else name for name, is_dir, size in items]
        hints = ["folder" if is_dir else _format_bytes(size) for name, is_dir, size in items]
        key, index = _pick(_short(folder), labels, hints, footer=_FOOTER, pos=pos)
        pos = index or 0
        if key in _QUIT:
            _clear_screen()
            return None
        if key in ("left", "backspace"):
            if folder != "/":
                want = folder.rstrip("/").rsplit("/", 1)[-1]
                folder = parent(folder)
            continue
        if key == "n":
            want = _new_file(folder)
            continue
        if key == "m":
            want = _new_folder(folder)
            continue
        if index is None:
            continue
        name, is_dir, size = items[index]
        target = join(folder, name)
        if key in ("enter", "right"):
            if is_dir:
                folder, pos = target, 0
            else:
                view(target)
        elif key == "e" and not is_dir:
            edit(target)
        elif key == "r" and name.endswith(".py"):
            run(target)
        elif key == "d":
            if delete(target, is_dir):
                pos = max(0, index - 1)


# ── standard ────────────────────────────────────


def ver():
    print("files:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Files --")
    print("browse(path='/')  b()  Browser: Enter opens,")
    print("                       <- goes up, e/r/d/n/m")
    print("view(path)        v()  Show a text file")
    print("edit(path)        e()  Firmware editor:")
    print("                       Ctrl+S save, Esc quit")
    print("No SD free space here: statvfs on a big card")
    print("takes a minute.")


def h():
    return help()


b = browse
v = view
e = edit
