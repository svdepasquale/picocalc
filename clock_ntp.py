import time

from pico_utils import load_json, save_json, ticks_ms as _ticks_ms, ticks_diff as _ticks_diff
from pico_utils import CLOCK_CONFIG_FILE as CONFIG_FILE
from pico_utils import utc_offset_hours as _utc_offset_hours, local_time as _local_time_at
from pico_utils import clock_synced as _clock_synced
from pico_utils import poll_key as _poll_key, read_key as _read_key, sleep_ms as _sleep_ms
from pico_utils import ticks_add as _ticks_add, screen_header as _screen_header
from pico_utils import paint as _paint, bar as _bar, block_rows as _block_rows, DISPLAY_WIDTH
from pico_utils import BCYAN, BYELLOW, GREY, GREEN


MODULE_VERSION = "2026-10-06.1"
NTP_HOST = "pool.ntp.org"
NTP_TIMEOUT = 5
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
    return _utc_offset_hours()


def _fmt(t):
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
        t[0], t[1], t[2], t[3], t[4], t[5]
    )


def _fmt_short(t):
    return "{:02d}:{:02d}:{:02d}".format(t[3], t[4], t[5])


def _local_time():
    offset = _get_offset()
    return _local_time_at(offset), offset


def _sync_hint():
    if not _clock_synced():
        print("Clock not set: run sync()")


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


def set_dst(on=True):
    """EU summer time on top of the UTC offset (Italy: set_utc_offset(1))."""
    config = _load_config()
    if on:
        config["dst"] = "eu"
    elif "dst" in config:
        del config["dst"]
    _save_config(config)
    print("EU DST:", "on" if on else "off")
    return bool(on)


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
    ntptime.timeout = NTP_TIMEOUT
    print("NTP sync (up to {}s)...".format(NTP_TIMEOUT * MAX_NTP_RETRIES))
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
    _sync_hint()
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
    _sync_hint()
    return lt


def epoch():
    e = time.time()
    print("Epoch:", e)
    return e


def timer_start():
    global _timer_start
    _timer_start = _ticks_ms()
    print("Timer started.")
    return True


def timer_stop():
    global _timer_start
    if _timer_start is None:
        print("No timer running.")
        return None
    elapsed = _ticks_diff(_ticks_ms(), _timer_start)
    _timer_start = None
    s = elapsed // 1000
    ms = elapsed % 1000
    m = s // 60
    print("Elapsed: {}m {}s {}ms".format(m, s % 60, ms))
    return elapsed


def timer_toggle():
    if _timer_start is None:
        return timer_start()
    return timer_stop()


def timer_check():
    if _timer_start is None:
        print("No timer running.")
        return None
    elapsed = _ticks_diff(_ticks_ms(), _timer_start)
    s = elapsed // 1000
    ms = elapsed % 1000
    m = s // 60
    print("Running: {}m {}s {}ms".format(m, s % 60, ms))
    return elapsed


def _beep():
    try:
        import synthesizer

        synthesizer.beep(3)
    except Exception:
        pass


def countdown(secs):
    try:
        total = int(secs)
    except Exception:
        print("Invalid.")
        return False
    if total < 1 or total > 86400:
        print("Range: 1..86400")
        return False
    print("Countdown {}  ".format(_fmt_secs(total)) + _paint("q", BYELLOW) + " stop")
    deadline = _ticks_add(_ticks_ms(), total * 1000)
    shown = None
    width = max(10, DISPLAY_WIDTH - 12)
    try:
        while True:
            left_ms = _ticks_diff(deadline, _ticks_ms())
            if left_ms <= 0:
                break
            left = (left_ms + 999) // 1000
            if left != shown:
                shown = left
                done = (total - left) / total
                print("\r\x1b[K" + _bar(done, width, GREEN) + " " + _fmt_secs(left), end="")
            if _poll_key() in ("q", "Q", "esc"):
                print()
                print("Cancelled.")
                return False
            _sleep_ms(100)
    except KeyboardInterrupt:
        print()
        print("Cancelled.")
        return False
    print("\r\x1b[K" + _bar(1, width, GREEN) + " " + _fmt_secs(0))
    print(_paint("TIME!", BYELLOW))
    _beep()
    return True


def _fmt_secs(secs):
    if secs >= 3600:
        return "{}:{:02d}:{:02d}".format(secs // 3600, secs // 60 % 60, secs % 60)
    return "{}:{:02d}".format(secs // 60, secs % 60)


# 3x5 digits, drawn two characters wide with full blocks
_BIG = {
    "0": ("###", "# #", "# #", "# #", "###"),
    "1": ("  #", "  #", "  #", "  #", "  #"),
    "2": ("###", "  #", "###", "#  ", "###"),
    "3": ("###", "  #", "###", "  #", "###"),
    "4": ("# #", "# #", "###", "  #", "  #"),
    "5": ("###", "#  ", "###", "  #", "###"),
    "6": ("###", "#  ", "###", "# #", "###"),
    "7": ("###", "  #", "  #", "  #", "  #"),
    "8": ("###", "# #", "###", "# #", "###"),
    "9": ("###", "# #", "###", "  #", "###"),
    ":": (" ", "#", " ", "#", " "),
}


def big_lines(text):
    """Text of digits and ':' as 5 lines of block characters."""
    rows = []
    for r in range(5):
        parts = []
        for ch in text:
            glyph = _BIG.get(ch)
            if glyph:
                parts.append(glyph[r].replace("#", "\u2588\u2588").replace(" ", "  "))
        rows.append(" ".join(parts))
    return rows


def live():
    """Big clock that updates every second; any key returns."""
    _screen_header("Clock")
    days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    last = None
    try:
        while True:
            lt, offset = _local_time()
            if lt[5] != last:
                last = lt[5]
                label = "UTC" + ("{:+d}".format(offset) if offset else "")
                print("\x1b[3;1H\x1b[K {} {:04d}-{:02d}-{:02d}  {}".format(
                    days[lt[6] % 7], lt[0], lt[1], lt[2], _paint(label, GREY)), end="")
                rows = big_lines("{:02d}:{:02d}:{:02d}".format(lt[3], lt[4], lt[5]))
                _block_rows(rows, 5, 1 + max(0, (DISPLAY_WIDTH - len(rows[0])) // 2), BCYAN)
                print("\x1b[11;1H\x1b[K " + _paint("any key", BYELLOW) + " back", end="")
            if _read_key(200) is not None:
                break
    except KeyboardInterrupt:
        pass
    print()
    return True


def ver():
    print("clock_ntp:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Clock NTP --")
    print("sync()        Sync time via NTP")
    print("now()/n()     Local time")
    print("utc()         UTC time")
    print("date()/d()    Date with day name")
    print("epoch()       Unix timestamp")
    print("set_utc_offset(h)  Timezone")
    print("timer_start()/ts() Start timer")
    print("timer_stop()/tp()  Stop timer")
    print("timer_check()/tc() Check timer")
    print("timer_toggle() Start/stop timer")
    print("countdown(s)/cd(s) Countdown")
    print("live()        Live clock")
    print("set_dst(True) EU summer time")
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
