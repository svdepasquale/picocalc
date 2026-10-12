try:
    from harness import *  # noqa
except ImportError:  # on the device tools/test_device.sh puts harness.py in front
    pass

# Host checks for synthesizer, clock_ntp and sys_status.
# Run from the repo root with either interpreter:
#   python3 tests/test_hw.py
#   micropython tests/test_hw.py


# ── synthesizer ─────────────────────────────────

import synthesizer as synth


def test_rtttl_parse():
    notes = synth._rtttl_parse("t:d=4,o=5,b=120:8e6,p,4c#.5,a")
    check("count", len(notes), 4)
    check("e6 eighth", (int(notes[0][0]), notes[0][1]), (1318, 250))
    check("rest", notes[1], (0, 500))
    check("c#5 dotted quarter", (int(notes[2][0]), notes[2][1]), (554, 750))
    check("a5 default octave", int(notes[3][0]), 880)
    check("bad song", synth._rtttl_parse("nope"), None)


class _RecPWM:
    # machine.PWM stand-in that records what is done to the speaker pins
    log = []

    def __init__(self, pin):
        self.pin = pin

    def freq(self, hz):
        _RecPWM.log.append(("freq", self.pin))

    def duty_u16(self, duty):
        _RecPWM.log.append(("duty", self.pin, duty))

    def deinit(self):
        _RecPWM.log.append(("deinit", self.pin))


class _RecMachine:
    PWM = _RecPWM


_RecMachine.Pin = lambda n: n


class _InterruptedI2S:
    def write(self, data):
        raise KeyboardInterrupt


def test_synth_ctrl_c_stops_the_tune():
    def interrupted(ms):
        raise KeyboardInterrupt

    out = []
    saved = (sys.modules.get("machine"), synth._sleep_ms, synth._poll_key, synth._init_audio, synth._use_pwm)
    sys.modules["machine"] = _RecMachine
    synth._sleep_ms = interrupted
    synth._poll_key = lambda: None
    synth.print = lambda *args, **kw: out.append(" ".join(str(a) for a in args))
    del _RecPWM.log[:]
    try:
        try:
            synth._play_freq_pwm(440, 50)
            check("pwm: Ctrl+C reaches the caller", "returned", "KeyboardInterrupt")
        except KeyboardInterrupt:
            pass
        silent = [("duty", 26, 0), ("duty", 27, 0), ("deinit", 26), ("deinit", 27)]
        check("pwm: silenced and released first", _RecPWM.log[-4:], silent)
        del _RecPWM.log[:]
        check("seq stops", synth.seq("C D E F"), False)
        check("on its first note", (_RecPWM.log.count(("deinit", 26)), out[-1]), (1, "Stopped."))
        synth._init_audio = lambda: _InterruptedI2S()
        try:
            synth._play_freq_i2s(440, 50)
            check("i2s: Ctrl+C reaches the caller", "returned", "KeyboardInterrupt")
        except KeyboardInterrupt:
            pass
        synth._use_pwm = False
        synth._sleep_ms = lambda ms: None
        check("rtttl stops", synth.rtttl("t:d=4,o=5,b=120:c,d,e"), False)
    finally:
        if saved[0] is None:
            sys.modules.pop("machine", None)
        else:
            sys.modules["machine"] = saved[0]
        synth._sleep_ms, synth._poll_key, synth._init_audio, synth._use_pwm = saved[1:]
        del synth.print


class _PlayingMusic:
    # the music module while a track plays in the background
    def playing(self):
        return "a"


def test_tones_leave_background_music_alone():
    # The music's DMA owns the speakers' PWM slice: a beep set up there
    # silenced the track for the rest of it, the ► mark still shown (the
    # games' Sound checks the same, test_games.py)
    saved = (sys.modules.get("machine"), sys.modules.get("music"))
    sys.modules["machine"] = _RecMachine
    del _RecPWM.log[:]
    try:
        sys.modules["music"] = _PlayingMusic()
        check("no beep over the music", (synth._play_freq_pwm(880, 10), synth.beep(2, ms=1)), (False, True))
        check("the slice untouched", _RecPWM.log, [])
    finally:
        for name, module in (("machine", saved[0]), ("music", saved[1])):
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


# clock


class _ClockTime:
    # clock_ntp's time module in virtual milliseconds
    ms = 0

    def time(self):
        return _ClockTime.ms // 1000

    def gmtime(self, secs):
        import time

        return time.gmtime(secs)


def test_clock_live():
    # Across the end of EU summer time (2026-10-25 01:00 UTC) and two
    # minute changes.
    import time
    import clock_ntp

    start = 1792889998 * 1000 + 500  # 00:59:58.5 UTC
    switch = 1792890000 * 1000
    stop = switch + 60200  # a key at 01:01:00.2
    Clock = _ClockTime
    Clock.ms = start

    def sleep(ms):
        Clock.ms += ms

    def poll():
        return "q" if Clock.ms >= stop else None

    def read_key(timeout_ms=None):  # the old loop's wait
        sleep(timeout_ms or 0)
        return poll()

    reads = []

    def offset():
        reads.append(Clock.ms)
        return 2 if Clock.ms < switch else 1

    drawn = []
    out = []
    saved = swap(
        clock_ntp,
        {
            "time": Clock(),
            "_get_offset": offset,
            "_sleep_ms": sleep,
            "_poll_key": poll,
            "_read_key": read_key,
            "_block_rows": lambda rows, row, col, fg: drawn.append((Clock.ms, rows)),
            "_screen_header": lambda title: out.append("<header>"),
            "_title_bar": lambda title: "<" + title + " bar>",
            "print": lambda *args, **kw: out.append("".join(str(a) for a in args)),
        },
    )
    try:
        check("live returns", clock_ntp.live(), True)
    finally:
        unswap(clock_ntp, saved)
    text = "".join(out)

    def digits(secs):
        t = time.gmtime(secs)
        return clock_ntp.big_lines("{:02d}:{:02d}:{:02d}".format(t[3], t[4], t[5]))

    check("zone read once a minute", len(reads), 3)
    check("one draw a second", len(drawn), 63)
    check("each within 50 ms of its second", max(ms % 1000 for ms, rows in drawn[1:]) < 50, True)
    check("summer time to its last second", drawn[1][1], digits(switch // 1000 - 1 + 7200))
    check("winter time from the switch", drawn[2][1], digits(switch // 1000 + 3600))
    check("title bar repainted each minute", text.count("\x1b[1;1H<Clock bar>"), 2)
    check("date line only when it changes", text.count("\x1b[3;1H"), 2)
    check("hint printed once", text.count("any key"), 1)


def test_clock_zone_saves():
    import clock_ntp

    saves = []
    ok = [True]
    out = []

    def save(data):
        saves.append(dict(data))
        return ok[0]

    saved = swap(
        clock_ntp,
        {
            "_load_config": lambda: {"utc_offset": 1, "dst": "eu"},
            "_save_config": save,
            "print": lambda *args, **kw: out.append(" ".join(str(a) for a in args)),
        },
    )
    try:
        check("same offset: no flash write", (clock_ntp.set_utc_offset(1), saves), (True, []))
        check("same summer time: no flash write", (clock_ntp.set_dst(True), saves), (True, []))
        ok[0] = False
        check("failed save reported", (clock_ntp.set_utc_offset(0), out[-1]), (False, "UTC offset not saved."))
        check("failed DST save reported", (clock_ntp.set_dst(False), out[-1]), (None, "EU DST not saved."))
        ok[0] = True
        check("new offset saved", (clock_ntp.set_utc_offset(0), saves[-1]), (True, {"utc_offset": 0, "dst": "eu"}))
        check("summer time off saved", (clock_ntp.set_dst(False), saves[-1]), (False, {"utc_offset": 1}))
    finally:
        unswap(clock_ntp, saved)


# system


class _KeyPresses:
    # _key_byte() for the key test: a press's bytes come together, then a
    # pause (a read with a timeout gets None)
    def __init__(self, presses):
        self.presses = list(presses)
        self.tail = []

    def __call__(self, timeout_ms=None):
        if timeout_ms is not None:
            return self.tail.pop(0) if self.tail else None
        press = self.presses.pop(0)
        self.tail = list(press[1:])
        return press[0]


class _LogFile:
    writes = []

    def __init__(self, path, mode):
        self.path = path
        self.mode = mode

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, text):
        _LogFile.writes.append((self.path, self.mode, text))
        return len(text)

    def flush(self):
        pass

    def close(self):
        pass


def test_status_key_log_batches():
    import sys_status

    out = []
    saved = swap(
        sys_status,
        {
            "open": _LogFile,
            "print": lambda *args, **kw: out.append(" ".join(str(a) for a in args)),
            "screen_header": lambda title: None,
        },
    )
    del _LogFile.writes[:]
    try:
        pu._key_byte = _KeyPresses([b"a", b"\x1b[A", b"q"])
        sys_status.keys()
        log = "61               'a'\n1b 5b 41         'up'\n71               'q'\n"
        check("one write at the end", _LogFile.writes, [("keylog.txt", "a", log)])
        del _LogFile.writes[:]
        pu._key_byte = _KeyPresses([b"x"] * 44 + [b"q"])
        sys_status.keys()
        check("a write per 20 keys", [w[2].count("\n") for w in _LogFile.writes], [20, 20, 5])
        del _LogFile.writes[:]
        pu._key_byte = _KeyPresses([b"q"])
        sys_status.keys(log=False)
        check("no log, no write", _LogFile.writes, [])
    finally:
        pu._key_byte = REAL_KEY_BYTE
        unswap(sys_status, saved)


def test_status_colour_samples():
    import sys_status

    out = []
    saved = swap(
        sys_status,
        {
            "print": lambda *args, **kw: out.append(" ".join(str(a) for a in args)),
            "screen_header": lambda title: None,
        },
    )
    try:
        sys_status.colors()
    finally:
        unswap(sys_status, saved)
    black = [n for n in range(16) if pu.paint(" sample ", pu.BLACK, n) in out[n]]
    white = [n for n in range(16) if pu.paint(" sample ", pu.BWHITE, n) in out[n]]
    check("black text on the light colours", black, [7, 10, 11, 13, 14, 15])
    check("white on the rest", white, [0, 1, 2, 3, 4, 5, 6, 8, 9, 12])


def test_status_text_battery():
    saved = (pu.battery, pu.wifi_connected, pu.clock_synced, pu.utc_offset_hours)
    try:
        pu.battery = lambda: (87, True)
        pu.wifi_connected = lambda: True
        pu.clock_synced = lambda: False
        pu.utc_offset_hours = lambda: 0
        pu.refresh_status()
        check("battery labelled", pu.status_text(), "WiFi  Bat 87%+")
        pu.battery = lambda: None
        pu.refresh_status()
        check("no reading, no battery", pu.status_text(), "WiFi")
    finally:
        pu.battery, pu.wifi_connected, pu.clock_synced, pu.utc_offset_hours = saved
        pu.refresh_status()

run(globals())
