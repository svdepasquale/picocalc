import gc
import os
import time

from pico_utils import screen_header, paged_lines, format_bytes, ticks_ms, ticks_diff


MODULE_VERSION = "2026-03-28.2"
PAGE_LINES = 8
_BOOT_TICKS = ticks_ms()


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
    ms = ticks_diff(ticks_ms(), _BOOT_TICKS)
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
    print("Freed:", format_bytes(after - before))
    print("Free now:", format_bytes(after))
    return after


def info():
    screen_header("System Status")
    ram()
    print("---")
    flash()
    print("---")
    uptime()
    print("---")
    ip()
    print("---")
    freq()
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
    print("freq()        CPU frequency")
    print("ls(path)      List directory")
    print("gc_run()      Run GC + stats")
    print("tip: import sys_status as s")


def h():
    return help()


def a():
    return info()
