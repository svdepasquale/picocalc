# cal: the month calendar's date arithmetic and screen lines.
try:
    from harness import *
except ImportError:
    pass  # on the device tools/test_device.sh puts harness.py in front

import cal


def test_dates():
    check("leap", [cal.leap(y) for y in (1900, 2000, 2024, 2026, 2100)], [False, True, True, False, False])
    check("february", (cal.days_in_month(2024, 2), cal.days_in_month(2026, 2), cal.days_in_month(2000, 2)), (29, 28, 29))
    check("weekday", (cal.weekday(2026, 10, 11), cal.weekday(2000, 1, 1), cal.weekday(2026, 1, 1)), (6, 5, 3))
    check("day of year", (cal.day_of_year(2026, 10, 11), cal.day_of_year(2024, 12, 31)), (284, 366))


def test_iso_weeks():
    for date, want in (
        ((2026, 10, 11), (2026, 41)),
        ((2026, 12, 31), (2026, 53)),
        ((2027, 1, 1), (2026, 53)),
        ((2027, 1, 4), (2027, 1)),
        ((2024, 12, 30), (2025, 1)),
        ((2021, 1, 3), (2020, 53)),
        ((2026, 1, 1), (2026, 1)),
    ):
        check("week {}".format(date), cal.iso_week(*date), want)


def test_easter_and_holidays():
    for year, want in ((2024, (3, 31)), (2025, (4, 20)), (2026, (4, 5)), (2027, (3, 28)), (2038, (4, 25)), (2285, (3, 22))):
        check("easter {}".format(year), cal.easter(year), want)
    days = cal.holidays(2024)
    check("easter monday across a month", days.get((4, 1)), "Luned\xec dell'Angelo")
    check("twelve days", len(cal.holidays(2026)), 12)
    check("fixed", (days[(4, 25)], days[(12, 26)]), ("Festa della Liberazione", "Santo Stefano"))


def test_grid_and_lines():
    weeks = cal.grid(2026, 10)
    check("weeks", [w for w, _ in weeks], [40, 41, 42, 43, 44])
    check("first row", weeks[0][1], [0, 0, 0, 1, 2, 3, 4])
    check("last row", weeks[-1][1], [26, 27, 28, 29, 30, 31, 0])
    check("february 2021: 4 rows", len(cal.grid(2021, 2)), 4)
    lines = cal.month_lines(2026, 10, (2026, 10, 11))
    plain = []
    for line in lines:
        out = ""
        skip = False
        for ch in line:
            if ch == "\x1b":
                skip = True
            elif skip:
                skip = ch != "m"
            else:
                out += ch
        plain.append(out)
    check("title", plain[0].strip(), "October 2026")
    check("header", plain[2], " Wk Mon Tue Wed Thu Fri Sat Sun")
    check("a week", plain[4], " 41   5   6   7   8   9  10  11")
    check("fits", max(len(p) for p in plain) <= pu.DISPLAY_WIDTH, True)
    check("today marked", "\x1b[38;5;0m\x1b[48;5;14m 11" in lines[4], True)
    check("holidays listed", [n.split(" ", 4)[-1] for n in cal.notes(2026, 11)], ["Ognissanti"])
    check("today's line", cal.notes(2026, 10, (2026, 10, 11))[-1].endswith("Sunday 11 October: day 284, week 41\x1b[0m"), True)


run(globals())
