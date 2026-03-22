import time

from pico_utils import load_json, save_json


CONFIG_FILE = "clock_config.json"
MODULE_VERSION = "2026-03-01.2"
NTP_HOST = "pool.ntp.org"
DEFAULT_UTC_OFFSET = 0
MAX_NTP_RETRIES = 2

_timer_start = None


def _load_config():
    data = load_json(CONFIG_FILE)
    if isinstance(data, dict):
        return data
    return {}


def _save_config(config):
    return save_json(CONFIG_FILE, config)


def _get_offset():
    config = _load_config()
    return int(config.get("utc_offset", DEFAULT_UTC_OFFSET))


def _fmt(t):
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
        t[0], t[1], t[2], t[3], t[4], t[5]
    )


def _fmt_short(t):
    return "{:02d}:{:02d}:{:02d}".format(t[3], t[4], t[5])


def _local_time():
    offset = _get_offset()
    utc_secs = time.time()
    local_secs = utc_secs + offset * 3600
    return time.gmtime(local_secs), offset


def set_utc_offset(hours):
    try:
        val = int(hours)
    except Exception:
        print("Invalid offset.")
        return False
    if val < -12 or val > 14:
        print("Range: -12..+14")
        return False
    config = _load_config()
    config["utc_offset"] = val
    _save_config(config)
    print("UTC offset:", val)
    return True


def sync():
    try:
        import ntptime
    except ImportError:
        print("Missing ntptime.")
        print("Try: import mip; mip.install('ntptime')")
        return False

    try:
        import network
        w = network.WLAN(network.STA_IF)
        if not w.isconnected():
            print("No WiFi. Connect first.")
            return False
    except Exception:
        pass

    ntptime.host = NTP_HOST
    ntptime.timeout = 5
    print("NTP sync...")
    for attempt in range(MAX_NTP_RETRIES):
        try:
            ntptime.settime()
            print("OK. UTC:", _fmt(time.gmtime()))
            return True
        except Exception as e:
            print("Sync err:", e)
            if attempt < MAX_NTP_RETRIES - 1:
                print("Retrying...")
    return False


def now():
    lt, offset = _local_time()
    label = "UTC" + ("{:+d}".format(offset) if offset else "")
    print(_fmt(lt), label)
    return lt


def utc():
    t = time.gmtime()
    print("UTC:", _fmt(t))
    return t


def date():
    lt, offset = _local_time()
    days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    d = days[lt[6]] if lt[6] < 7 else "?"
    label = "UTC" + ("{:+d}".format(offset) if offset else "")
    print("{} {:04d}-{:02d}-{:02d}".format(d, lt[0], lt[1], lt[2]))
    print(_fmt_short(lt), label)
    return lt


def epoch():
    e = time.time()
    print("Epoch:", e)
    return e


def timer_start():
    global _timer_start
    _timer_start = time.ticks_ms()
    print("Timer started.")
    return True


def timer_stop():
    global _timer_start
    if _timer_start is None:
        print("No timer running.")
        return None
    elapsed = time.ticks_diff(time.ticks_ms(), _timer_start)
    _timer_start = None
    s = elapsed // 1000
    ms = elapsed % 1000
    m = s // 60
    print("Elapsed: {}m {}s {}ms".format(m, s % 60, ms))
    return elapsed


def timer_check():
    if _timer_start is None:
        print("No timer running.")
        return None
    elapsed = time.ticks_diff(time.ticks_ms(), _timer_start)
    s = elapsed // 1000
    ms = elapsed % 1000
    m = s // 60
    print("Running: {}m {}s {}ms".format(m, s % 60, ms))
    return elapsed


def countdown(secs):
    try:
        total = int(secs)
    except Exception:
        print("Invalid.")
        return False
    if total < 1 or total > 86400:
        print("Range: 1..86400")
        return False
    print("Countdown: {}s (Ctrl+C)".format(total))
    try:
        remaining = total
        while remaining > 0:
            m = remaining // 60
            s = remaining % 60
            print("  {}m {:02d}s".format(m, s))
            step = min(remaining, 10)
            time.sleep(step)
            remaining -= step
    except KeyboardInterrupt:
        print("Cancelled.")
        return False
    print("TIME!")
    return True


def ver():
    print("clock_ntp:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("cmd: sync now utc date epoch")
    print("cmd: set_utc_offset")
    print("cmd: timer_start timer_stop")
    print("cmd: timer_check countdown")
    print("cmd: ver help h")
    print("tip: import clock_ntp as c")


def h():
    return help()


def n():
    return now()


def d():
    return date()


def ts():
    return timer_start()


def tp():
    return timer_stop()


def tc():
    return timer_check()


def cd(secs):
    return countdown(secs)
