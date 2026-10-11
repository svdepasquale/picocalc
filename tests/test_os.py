# The OS layer of pico_utils: settings, the flash-write hold, clean() and the
# terminal guard, the keyboard MCU's registers, dimming, the screen off and
# background services.
try:
    from harness import *
except ImportError:
    pass  # on the device tools/test_device.sh puts harness.py in front


class FakePanel:
    # picocalc.display's refresh switch
    def __init__(self):
        self.log = []

    def stopRefresh(self):
        self.log.append("stop")

    def recoverRefresh(self):
        self.log.append("go")


class FakeMcu:
    # the keyboard MCU's registers behind a fake I2C: a read is "write the
    # register, read (register, value)", a write is (register | 0x80, value)
    def __init__(self):
        self.regs = {0x05: 0x50, 0x0A: 0x40, 0x0B: 0x55, 0x0E: 1}
        self.reg = None
        self.writes = []
        self.address = 0x1F
        self.i2c = self

    def writeto(self, addr, data):
        data = bytes(data)
        if len(data) == 1:
            self.reg = data[0]
        else:
            self.writes.append((data[0] & 0x7F, data[1]))
            self.regs[data[0] & 0x7F] = data[1]

    def readfrom(self, addr, n):
        return bytes((self.reg, self.regs.get(self.reg, 0)))


class FakeLut:
    # picocalcdisplay's palette calls
    def __init__(self):
        self.lut = bytearray(range(64)) + bytearray(448)
        self.sizes = []

    def getLUTview(self):
        return memoryview(self.lut)

    def setLUT(self, buf):
        data = bytes(buf)
        self.sizes.append(len(data))
        self.lut[: len(data)] = data


class FakePico:
    pass


def with_pico(body, display=None, keyboard=None, lut=None):
    fake = FakePico()
    fake.display = display
    fake.keyboard = keyboard
    saved = (sys.modules.get("picocalc"), sys.modules.get("picocalcdisplay"))
    saved_state = (pu._SETTINGS[0], pu._IDLE[0], pu._DIM[0], pu._OFF[0], pu._HOLDS[0], list(pu._HOOKS))
    sys.modules["picocalc"] = fake
    if lut is not None:
        sys.modules["picocalcdisplay"] = lut
    try:
        body(fake)
    finally:
        for name, module in (("picocalc", saved[0]), ("picocalcdisplay", saved[1])):
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        pu._SETTINGS[0], pu._IDLE[0], pu._DIM[0], pu._OFF[0], pu._HOLDS[0] = saved_state[:5]
        pu._HOOKS[:] = saved_state[5]


def test_clean_drops_controls():
    check("esc", pu.clean("a\x1b[40;1Hb"), "a[40;1Hb")
    check("tab and newline kept", pu.clean("a\tb\nc"), "a\tb\nc")
    check("cr", pu.clean("a\r\nb\rc"), "a\nb\nc")
    check("del and bel", pu.clean("x\x7fy\x07"), "xy")
    check("accents kept", pu.clean("caff\xe8 — ok"), "caff\xe8 — ok")
    check("not a str", pu.clean(42), "42")


def test_safe_csi_guards_the_terminal():
    last = pu._TERM_ROWS - 1
    check("margins dropped", pu._safe_csi("a\x1b[rb\x1b[1;40rc"), "abc")
    check("row clamped", pu._safe_csi("\x1b[99;5H"), "\x1b[{};5H".format(last))
    check("f clamped", pu._safe_csi("\x1b[99f"), "\x1b[{}f".format(last))
    check("vpa clamped", pu._safe_csi("x\x1b[99dy"), "x\x1b[{}dy".format(last))
    check("home kept", pu._safe_csi("\x1b[2J\x1b[H"), "\x1b[2J\x1b[H")
    check("colours kept", pu._safe_csi("\x1b[38;5;11mX\x1b[0m"), "\x1b[38;5;11mX\x1b[0m")
    check("private kept", pu._safe_csi("\x1b[?25l"), "\x1b[?25l")
    check("cut sequence kept", pu._safe_csi("ab\x1b[12"), "ab\x1b[12")
    check("safe row kept", pu._safe_csi("\x1b[3;1Hx"), "\x1b[3;1Hx")
    check("terminal reset dropped", pu._safe_csi("a\x1bcb"), "ab")
    vt = FakeVt()
    pu._ScreenTerm(vt).write(b"T\x1b[1;99r\x1b[99;1H\n")
    check("writer guarded", vt.drawn, ["T\x1b[{};1H\n".format(last)])


def test_flash_write_holds_the_refresh():
    panel = FakePanel()

    def body(fake):
        with pu.flash_write("settings.json"):
            with pu.flash_write("notes_data.json"):
                panel.log.append("write")
        with pu.flash_write("/sd/music/a.wav"):
            panel.log.append("sd")
        try:
            with pu.flash_write("x.json"):
                raise ValueError("boom")
        except ValueError:
            pass
        check("holds back to 0", pu._HOLDS[0], 0)
        check("saved", pu.save_json(TMP, {"a": 1}), True)

    with_pico(body, display=panel)
    rm_tmp(TMP)
    check("nested, sd and error", panel.log, ["stop", "write", "go", "sd", "stop", "go", "stop", "go"])
    check("on_sd", (pu.on_sd("/sd"), pu.on_sd("/sd/a"), pu.on_sd("/sdx"), pu.on_sd("a.json")), (True, True, False, False))


def test_settings_defaults_and_types():
    saved = (pu.SETTINGS_FILE, pu._SETTINGS[0])
    pu.SETTINGS_FILE = TMP
    try:
        write_file(TMP, '{"dim_s": "x", "sleep_s": 120, "other": 5, "wifi_sleep": 1}')
        pu._SETTINGS[0] = None
        current = pu.settings()
        check("wrong type: default", current["dim_s"], 60)
        check("kept", current["sleep_s"], 120)
        check("unknown key kept", current["other"], 5)
        check("int for a bool: default", current["wifi_sleep"], True)
        check("saved", pu.set_setting("dim_s", 30), True)
        pu._SETTINGS[0] = None
        check("read back", pu.settings()["dim_s"], 30)
        check("other kept on save", pu.settings()["other"], 5)
    finally:
        rm_tmp(TMP)
        pu.SETTINGS_FILE, pu._SETTINGS[0] = saved


def test_mcu_registers():
    mcu = FakeMcu()

    def body(fake):
        check("battery", pu.battery(), (85, False))
        mcu.regs[0x0B] = 0xD5
        check("charging", pu.battery(), (85, True))
        check("backlight read", pu.backlight(), 5)
        check("backlight set", pu.backlight(20), 15)
        check("backlight floor", pu.backlight(0), 1)
        check("keyboard read", pu.keyboard_light(), 2)
        check("keyboard off", pu.keyboard_light(0), 0)
        check("power off asked", pu.power_off(1), True)
        pu._SETTINGS[0] = dict(pu.SETTINGS_DEFAULTS, brightness=7, keyboard=3)
        pu.apply_settings()

    with_pico(body, keyboard=mcu)
    check(
        "writes",
        mcu.writes,
        [(0x05, 240), (0x05, 16), (0x0A, 0), (0x0E, 6), (0x05, 112), (0x0A, 96)],
    )
    check("no MCU", (pu.battery(), pu.backlight(), pu.power_off()), (None, None, False))


def test_dim_and_wake():
    mcu = FakeMcu()

    def body(fake):
        pu._SETTINGS[0] = dict(pu.SETTINGS_DEFAULTS, dim_s=1, sleep_s=0)
        now = pu.ticks_ms()
        pu._IDLE[0] = pu.ticks_add(now, -2000)
        pu._services(now, True)
        check("dimmed", mcu.regs[0x05], 16)
        check("level kept", pu._DIM[0], 5)
        pu._services(now, True)
        check("dims once", len(mcu.writes), 1)
        check("the key goes on", pu._awake(ord("a"), now), True)
        check("level back", mcu.regs[0x05], 0x50)
        check("not dimmed", pu._DIM[0], None)

    with_pico(body, keyboard=mcu)


def test_screen_off_and_on():
    mcu = FakeMcu()
    panel = FakePanel()
    lut = FakeLut()

    def body(fake):
        pu._SETTINGS[0] = dict(pu.SETTINGS_DEFAULTS, dim_s=0, sleep_s=1, wifi_sleep=False)
        original = bytes(lut.lut[:32])
        now = pu.ticks_ms()
        pu._IDLE[0] = pu.ticks_add(now, -1500)
        pu._services(now, False)  # a polling app (music): the screen goes off too
        check("off", pu.screen_is_off(), True)
        check("black", bytes(lut.lut[:32]), bytes(32))
        check("32 of 64 bytes", lut.sizes, [32])
        check("backlight min", mcu.regs[0x05], 16)
        check("keyboard light off", mcu.regs[0x0A], 0)
        check("no hold before a frame", panel.log, [])
        pu._services(pu.ticks_add(pu._OFF[0][3], 200), True)
        check("held", panel.log, ["stop"])
        check("the key only wakes", pu._awake(ord("x"), now), False)
        check("on", pu.screen_is_off(), False)
        check("palette back", bytes(lut.lut[:32]), original)
        check("released", panel.log, ["stop", "go"])
        check("backlight back", mcu.regs[0x05], 0x50)
        check("keyboard light back", mcu.regs[0x0A], 0x40)
        check("power key: off", pu._awake(pu.POWER_KEY, now), False)
        check("off again", pu.screen_is_off(), True)
        pu.screen_on()
        check("on again", pu.screen_is_off(), False)
        check("holds back to 0", pu._HOLDS[0], 0)

    with_pico(body, display=panel, keyboard=mcu, lut=lut)


def test_services_run_and_drop():
    calls = []

    def hook(now, waiting):
        calls.append(waiting)
        return len(calls) < 2

    def boom(now, waiting):
        raise ValueError("boom")

    def body(fake):
        pu._SETTINGS[0] = dict(pu.SETTINGS_DEFAULTS, dim_s=0, sleep_s=0)
        pu.add_service(hook)
        pu.add_service(hook)
        pu.add_service(boom)
        now = pu.ticks_ms()
        for waiting in (True, False, True):
            pu._services(now, waiting)
        check("calls", calls, [True, False])
        check("dropped", (hook in pu._HOOKS, boom in pu._HOOKS), (False, False))

    with_pico(body)



def test_settings_app_values():
    import settings as app

    check("never", app.duration(0), "never")
    check("seconds", app.duration(30), "30 s")
    check("minutes", app.duration(300), "5 min")
    check("next", app.step(app.DIM_CHOICES, 60, 1), 120)
    check("last stays", app.step(app.DIM_CHOICES, 0, 1), 0)
    check("first stays", app.step(app.DIM_CHOICES, 15, -1), 15)
    check("wraps", app.step(app.SLEEP_CHOICES, 0, 1, True), 60)
    check("unknown: first", app.step(app.SLEEP_CHOICES, 7, 1), 60)
    check("levels", app.step(tuple(range(1, 16)), 15, 1, True), 1)


def test_settings_app_power_cycle():
    import settings as app

    answers = [True, True, False, False, True]
    restarts = []
    app.wait_power_cycle(lambda: answers.pop(0), lambda: restarts.append(1), lambda: None)
    check("restart once back on", (restarts, answers), ([1], []))


run(globals())
