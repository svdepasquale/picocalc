"""Settings: the screen and keyboard lights, when the screen dims and goes
off, Wi-Fi at start and in standby, and switching the PicoCalc off. Values
live in settings.json (pico_utils.settings()); the launcher applies the
lights at start, since the keyboard MCU forgets them at power-off."""

from pico_utils import backlight as _backlight, keyboard_light as _keyboard_light
from pico_utils import battery as _battery, flash_write as _flash_write
from pico_utils import paint as _paint, pick as _pick, power_off as _power_off
from pico_utils import read_key as _read_key, screen_header as _screen_header
from pico_utils import screen_off as _screen_off, set_setting as _set_setting
from pico_utils import settings as _settings, wait_key as _wait_key
from pico_utils import BRED, BYELLOW, GREY


MODULE_VERSION = "2026-10-11.1"
_QUIT = ("q", "Q", "esc", "eof")
DIM_CHOICES = (15, 30, 60, 120, 300, 0)  # seconds, 0 (never) last
SLEEP_CHOICES = (60, 120, 300, 600, 1800, 0)
POWER_OFF_DELAY_S = 6  # the keyboard MCU's minimum
_ROWS = (
    ("Screen brightness", "light"),
    ("Keyboard light", "keys"),
    ("Dim after", "dim_s"),
    ("Screen off after", "sleep_s"),
    ("Wi-Fi at start", "autoconnect"),
    ("Wi-Fi off in standby", "wifi_sleep"),
    ("Screen off now", "off"),
    ("Switch off", "power"),
)
_FOOTER = (
    (("←→", "change"), ("Enter", "change/do"), ("q", "back")),
    (("Alt+, Alt+.", "brightness"), ("Alt+Space", "keys light")),
)


# ── values ──────────────────────────────────────


def duration(seconds):
    if not seconds:
        return "never"
    if seconds < 60:
        return "{} s".format(seconds)
    return "{} min".format(seconds // 60)


def step(choices, value, direction, wrap=False):
    """The choice next to value (direction +1/-1); an unknown value starts
    from the first. wrap: past the end comes the other end."""
    try:
        i = list(choices).index(value)
    except ValueError:
        i = 0
        direction = 0
    i += direction
    if wrap:
        i %= len(choices)
    return choices[max(0, min(len(choices) - 1, i))]


def _hints(state):
    current = _settings()
    light, keys = state
    return [
        "{} of 15".format(light) if light else "n/a",
        ("{} of 7".format(keys) if keys else "off") if keys is not None else "n/a",
        duration(current["dim_s"]),
        duration(current["sleep_s"]),
        "on" if current["autoconnect"] else "off",
        "on" if current["wifi_sleep"] else "off",
        "",
        "",
    ]


def _change(name, state, direction, wrap):
    current = _settings()
    light, keys = state
    if name == "light" and light:
        light = step(tuple(range(1, 16)), light, direction, wrap)
        _backlight(light)
        _set_setting("brightness", light)
    elif name == "keys" and keys is not None:
        keys = step(tuple(range(8)), keys, direction, wrap)
        _keyboard_light(keys)
        _set_setting("keyboard", keys)
    elif name == "dim_s":
        _set_setting(name, step(DIM_CHOICES, current[name], direction, wrap))
    elif name == "sleep_s":
        _set_setting(name, step(SLEEP_CHOICES, current[name], direction, wrap))
    elif name in ("autoconnect", "wifi_sleep"):
        _set_setting(name, not current[name])
    return light, keys


# ── switching off ───────────────────────────────


def _usb_powered():
    # Pico 2 W: VBUS sense is the wireless chip's GPIO 2
    try:
        import machine

        return bool(machine.Pin("WL_GPIO2", machine.Pin.IN).value())
    except Exception:
        return False


def switch_off():
    """Ask, then have the keyboard MCU switch the PicoCalc off. On USB power
    the Pico stays on: it waits for the PicoCalc to come back on and then
    restarts, so the screen is set up again."""
    _screen_header("Switch off")
    print("Switch the PicoCalc off? " + _paint("y", BYELLOW) + "/n")
    try:
        key = _read_key()
    except KeyboardInterrupt:
        key = "esc"
    if key not in ("y", "Y"):
        return False
    if not _power_off(POWER_OFF_DELAY_S):
        print(_paint("The keyboard MCU did not answer.", BRED))
        _wait_key()
        return False
    print("Switching off in {} s.".format(POWER_OFF_DELAY_S))
    try:
        import network

        network.WLAN(network.STA_IF).active(False)
    except Exception:
        pass
    try:
        import os

        with _flash_write():
            os.sync()
    except Exception:
        pass
    if _usb_powered():
        print(_paint("USB power: the Pico stays on. Switch the", GREY))
        print(_paint("PicoCalc on again and it restarts.", GREY))
        wait_power_cycle()
    return True


def wait_power_cycle(answers=None, restart=None, wait=None):
    """Wait for the keyboard MCU to stop answering (the PicoCalc is off) and
    then to answer again, then restart (the panel lost its setup with the
    power). The other arguments are for the tests."""
    answers = answers or (lambda: _battery() is not None)
    wait = wait or (lambda: _read_key(500))  # HostTakeover still gets through
    if restart is None:
        import machine

        restart = machine.reset
    seen_off = False
    while True:
        if not answers():
            seen_off = True
        elif seen_off:
            restart()
            return True
        wait()


# ── screen ──────────────────────────────────────


def show():
    """The settings list: arrows choose, left/right or Enter change, q back."""
    state = (_backlight(), _keyboard_light())
    pos = 0
    labels = [label for label, _ in _ROWS]
    while True:
        key, pos = _pick("Settings", labels, _hints(state), _FOOTER, pos)
        if key in _QUIT or pos is None:
            return None
        name = _ROWS[pos][1]
        if key == "enter":
            if name == "off":
                _screen_off()
                continue
            if name == "power":
                switch_off()
                continue
            state = _change(name, state, 1, True)
        elif key in ("left", "right"):
            state = _change(name, state, 1 if key == "right" else -1, False)


# ── standard ────────────────────────────────────


def ver():
    print("settings:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Settings --")
    print("show()  s()   The settings list")
    print("switch_off()  Switch the PicoCalc off")
    print("Lights also: Alt+, Alt+. (screen), Alt+Space (keys)")


def h():
    return help()


s = show
