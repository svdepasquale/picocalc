import gc
import os
import time


MODULE_VERSION = "2026-03-01.2"


def _clip(text, limit):
    value = str(text)
    if len(value) <= limit:
        return value
    return value[:limit]


def ram():
    gc.collect()
    free = gc.mem_free()
    alloc = gc.mem_alloc()
    total = free + alloc
    pct = (alloc * 100) // total if total > 0 else 0
    print("RAM free:", free)
    print("RAM used:", alloc, "({}%)".format(pct))
    return {"free": free, "used": alloc, "total": total}


def flash():
    try:
        s = os.statvfs("/")
        bs = s[0]
        total = bs * s[2]
        free = bs * s[3]
        used = total - free
        pct = (used * 100) // total if total > 0 else 0
        print("Flash free:", free)
        print("Flash used:", used, "({}%)".format(pct))
        return {"free": free, "used": used, "total": total}
    except Exception as e:
        print("Flash err:", e)
        return None


def uptime():
    ms = time.ticks_ms()
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
        for name in items:
            full = path.rstrip("/") + "/" + name
            try:
                st = os.stat(full)
                if st[0] & 0x4000:
                    print("  {}/".format(name))
                else:
                    print("  {} {}B".format(name, st[6]))
            except Exception:
                print("  {}".format(name))
        return items
    except Exception as e:
        print("Err:", e)
        return []


def df():
    return flash()


def gc_run():
    before = gc.mem_free()
    gc.collect()
    after = gc.mem_free()
    print("Freed:", after - before)
    print("Free now:", after)
    return after


def info():
    print("=== System ===")
    ram()
    print("")
    flash()
    print("")
    uptime()
    print("")
    ip()
    print("")
    freq()


def ver():
    print("sys_status:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("cmd: info ram flash uptime")
    print("cmd: ip freq ls df gc_run")
    print("cmd: ver help h")
    print("tip: import sys_status as s")


def h():
    return help()


def a():
    return info()
