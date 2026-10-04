import gc
import os
import time

import pico_utils as _pu
from pico_utils import screen_header, paged_lines, format_bytes, ticks_ms, ticks_diff
from pico_utils import PAGE_LINES, battery as _battery
from pico_utils import paint as _paint, bar as _bar, key_bar as _key_bar
from pico_utils import GREEN, YELLOW, RED, GREY, BCYAN, BWHITE, BLACK


MODULE_VERSION = "2026-10-04.4"
KEY_LOG = "keylog.txt"
_IMPORT_TICKS = ticks_ms()


def ram():
    gc.collect()
    free = gc.mem_free()
    alloc = gc.mem_alloc()
    total = free + alloc
    pct = (alloc * 100) // total if total > 0 else 0
    print("RAM free:", format_bytes(free))
    print("RAM used:", format_bytes(alloc), "({}%)".format(pct))
    print("RAM tot:", format_bytes(total))
    return {"free": free, "used": alloc, "total": total}


def flash():
    try:
        s = os.statvfs("/")
        bs = s[0]
        total = bs * s[2]
        free = bs * s[3]
        used = total - free
        pct = (used * 100) // total if total > 0 else 0
        print("Flash free:", format_bytes(free))
        print("Flash used:", format_bytes(used), "({}%)".format(pct))
        print("Flash tot:", format_bytes(total))
        return {"free": free, "used": used, "total": total}
    except Exception as e:
        print("Flash err:", e)
        return None


def uptime():
    if hasattr(time, "ticks_ms"):
        # MicroPython ticks count from boot; wraps after ~12.4 days
        ms = time.ticks_ms()
    else:
        ms = ticks_diff(ticks_ms(), _IMPORT_TICKS)
    s = ms // 1000
    m = s // 60
    h = m // 60
    print("Up: {}h {}m {}s".format(h, m % 60, s % 60))
    return s


def ip():
    try:
        import network

        w = network.WLAN(network.STA_IF)
        if w.isconnected():
            c = w.ifconfig()
            print("IP:", c[0])
            print("GW:", c[2])
            print("DNS:", c[3])
            return c
        print("WiFi: off")
        return None
    except Exception:
        print("No WiFi module")
        return None


def freq():
    try:
        import machine

        f = machine.freq()
        print("CPU:", f // 1000000, "MHz")
        return f
    except Exception:
        print("N/A")
        return None


def bat():
    info = _battery()
    if info is None:
        print("Battery: n/a")
        return None
    pct, charging = info
    print("Battery: {}%{}".format(pct, " (charging)" if charging else ""))
    return info


def ls(path="/"):
    try:
        items = sorted(os.listdir(path))
        lines = []
        for name in items:
            full = path.rstrip("/") + "/" + name
            try:
                st = os.stat(full)
                if st[0] & 0x4000:
                    lines.append("  {}/".format(name))
                else:
                    lines.append("  {} {}".format(name, format_bytes(st[6])))
            except Exception:
                lines.append("  {}".format(name))
        print("Dir:", path)
        paged_lines(lines, page_lines=PAGE_LINES)
        return len(items)
    except Exception as e:
        print("Err:", e)
        return 0


def df():
    return flash()


def gc_run():
    before = gc.mem_free()
    gc.collect()
    after = gc.mem_free()
    freed = after - before
    if freed < 0:
        freed = 0
    print("Freed:", format_bytes(freed))
    print("Free now:", format_bytes(after))
    return after


def _uptime_text():
    if hasattr(time, "ticks_ms"):
        ms = time.ticks_ms()
    else:
        ms = ticks_diff(ticks_ms(), _IMPORT_TICKS)
    m = ms // 60000
    return "{}h {}m".format(m // 60, m % 60)


def _level_color(fraction):
    # usage bars: green while there is room, then yellow, then red
    if fraction < 0.7:
        return GREEN
    return YELLOW if fraction < 0.9 else RED


def info(header=True):
    """Dashboard: RAM, flash, battery, uptime, network, CPU."""
    if header:
        screen_header("System")
    gc.collect()
    free = gc.mem_free()
    used = gc.mem_alloc()
    frac = used / (free + used) if free + used else 0
    print("RAM    {}  {} free".format(_bar(frac, 16, _level_color(frac)), format_bytes(free)))
    try:
        st = os.statvfs("/")
        total = st[0] * st[2]
        ffree = st[0] * st[3]
        frac = (total - ffree) / total if total else 0
        print("Flash  {}  {} free".format(_bar(frac, 16, _level_color(frac)), format_bytes(ffree)))
    except Exception:
        print("Flash  n/a")
    bat = _battery()
    if bat is not None:
        pct, charging = bat
        colour = GREEN if pct > 40 else (YELLOW if pct > 15 else RED)
        print("Batt   {}  {}%{}".format(_bar(pct / 100, 16, colour), pct, " charging" if charging else ""))
    print("Up     {}".format(_uptime_text()))
    try:
        import network

        w = network.WLAN(network.STA_IF)
        print("WiFi   {}".format(w.ifconfig()[0] if w.isconnected() else "off"))
    except Exception:
        print("WiFi   n/a")
    try:
        import machine

        print("CPU    {} MHz".format(machine.freq() // 1000000))
    except Exception:
        pass
    return True


def keys(log=True):
    """Key test: shows each key's raw bytes and the name read_key() gives it;
    also appends them to keylog.txt. q quits."""
    screen_header("Key test")
    print("Press keys (arrows too). q quits.")
    print(_paint("raw bytes        name", GREY))
    out = None
    if log:
        try:
            out = open(KEY_LOG, "a")
        except OSError:
            out = None
    try:
        while True:
            raw = [_pu._key_byte()]
            while True:
                more = _pu._key_byte(40)
                if more is None:
                    break
                raw.append(more)
            rest = raw[1:]

            def next_byte(timeout_ms=None):
                return rest.pop(0) if rest else None

            try:
                name = _pu._decode_key(raw[0], next_byte)
            except KeyboardInterrupt:
                name = "ctrl-c"
            line = "{:<16} {}".format(" ".join("%02x" % b for b in raw), repr(name))
            print(line)
            if out:
                out.write(line + "\n")
                out.flush()
            if name in ("q", "Q"):
                break
    finally:
        if out:
            out.close()
    return True


def colors():
    """The terminal's 16 colours, numbered (check the palette)."""
    screen_header("Colours")
    for n in range(16):
        print("{:>2} {} {}".format(n, _paint("\u2588" * 10, n), _paint(" sample ", BLACK if n in (7, 15) else BWHITE, n)))
    return True


def ver():
    print("sys_status:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- System Status --")
    print("info()/a()    Show all info")
    print("ram()         RAM usage")
    print("flash()/df()  Flash storage")
    print("uptime()      Time since boot")
    print("ip()          Network info")
    print("bat()         Battery level")
    print("keys()        Key test (keylog.txt)")
    print("colors()      Colour palette")
    print("freq()        CPU frequency")
    print("ls(path)      List directory")
    print("gc_run()      Run GC + stats")
    print("tip: import sys_status as s")


def h():
    return help()


def a():
    return info()
