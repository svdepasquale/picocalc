# Boot hook: opens the launcher at power-on.
#
# The firmware freezes boot.py and main.py, and MicroPython runs a frozen
# main.py before one on flash (shared/runtime/pyexec.c), so a main.py here
# never starts the launcher. boot.py does import this module (the pye
# editor's colours), and a file on flash shadows a frozen module of the same
# name in normal imports. So this file:
#   1. re-exports the frozen module's syntax_style (literal copy as fallback),
#   2. on a cold boot, with go.py installed and the keyboard answering, puts
#      "import go" + Enter in the keyboard buffer: the REPL runs it as if typed.
# Soft resets (Ctrl+D, mpremote) come later than COLD_BOOT_MS and are left
# alone. Delete this file for the stock behaviour:
#   mpremote resume rm :default_style.py
import sys

COLD_BOOT_MS = 5000  # boot.py imports this ~150 ms after power-on
BOOT_TICKS = None  # ticks_ms when boot.py imported this module

syntax_style = {
    "break": "\x1b[37;45m",
    "raise": "\x1b[37;46m",
    "or": "\x1b[37;46m",
    "not": "\x1b[37;46m",
    "from": "\x1b[37;44m",
    "as": "\x1b[37;44m",
    "elif": "\x1b[37;46m",
    "False": "\x1b[31m",
    "pass": "\x1b[37;45m",
    "in": "\x1b[37;46m",
    "try": "\x1b[37;46m",
    "#": "\x1b[32m",
    "string": "\x1b[33m",
    "multiline": "\x1b[32m",
    "and": "\x1b[37;46m",
    "else": "\x1b[37;46m",
    "while": "\x1b[37;46m",
    "if": "\x1b[37;46m",
    "for": "\x1b[37;46m",
    "True": "\x1b[36m",
    "import": "\x1b[37;44m",
    "class": "\x1b[37;45m",
    "return": "\x1b[37;45m",
    "def": "\x1b[37;44m",
    "except": "\x1b[37;46m",
}


def _load_frozen():
    global syntax_style
    me = sys.modules.pop("default_style", None)
    path = sys.path[:]
    try:
        sys.path[:] = [".frozen"]
        import default_style as frozen

        syntax_style = frozen.syntax_style
    finally:
        sys.path[:] = path
        if me is not None:
            sys.modules["default_style"] = me


def _type_ahead():
    global BOOT_TICKS
    import os
    import time

    BOOT_TICKS = time.ticks_ms()
    if BOOT_TICKS > COLD_BOOT_MS:
        return
    os.stat("go.py")  # OSError (caught below) when the toolkit isn't installed
    import picocalc

    init = picocalc.PicoKeyboard.__init__

    def __init__(self, *args, **kwargs):
        picocalc.PicoKeyboard.__init__ = init  # one shot
        init(self, *args, **kwargs)
        try:
            self.keyCount()  # EIO while the PicoCalc is off: no screen, skip
            self.hardwarekeyBuf.extend(b"import go\r")
        except Exception:
            pass

    picocalc.PicoKeyboard.__init__ = __init__


for _step in (_load_frozen, _type_ahead):
    try:
        _step()
    except Exception:
        pass
