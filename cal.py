"""Calendar: a month at a time, Monday first, ISO week numbers, today
highlighted, Sundays and Italian public holidays in red (Easter computed).
Left/right change the month, up/down the year, t comes back to today."""

from pico_utils import _weekday
from pico_utils import clock_synced as _clock_synced, key_bar as _key_bar
from pico_utils import local_time as _local_time, paint as _paint
from pico_utils import read_key as _read_key, screen_header as _screen_header
from pico_utils import BCYAN, BLACK, BRED, GREY, WHITE, DISPLAY_WIDTH, SCREEN_ROWS


MODULE_VERSION = "2026-10-11.1"
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_FIXED = {
    (1, 1): "Capodanno",
    (1, 6): "Epifania",
    (4, 25): "Festa della Liberazione",
    (5, 1): "Festa del Lavoro",
    (6, 2): "Festa della Repubblica",
    (8, 15): "Ferragosto",
    (11, 1): "Ognissanti",
    (12, 8): "Immacolata Concezione",
    (12, 25): "Natale",
    (12, 26): "Santo Stefano",
}
_QUIT = ("q", "Q", "esc", "eof")


# ── dates ───────────────────────────────────────


def leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def days_in_month(year, month):
    if month == 2:
        return 29 if leap(year) else 28
    return 30 if month in (4, 6, 9, 11) else 31


def weekday(year, month, day):
    """Monday 0 ... Sunday 6."""
    return _weekday(year, month, day)


def day_of_year(year, month, day):
    return sum(days_in_month(year, m) for m in range(1, month)) + day


def weeks_in_year(year):
    jan1 = weekday(year, 1, 1)
    return 53 if jan1 == 3 or (jan1 == 2 and leap(year)) else 52


def iso_week(year, month, day):
    """(ISO year, ISO week) of a date: Dec 29-31 can be week 1 of the next
    year, Jan 1-3 week 52/53 of the last."""
    week = (day_of_year(year, month, day) - weekday(year, month, day) + 9) // 7
    if week < 1:
        return year - 1, weeks_in_year(year - 1)
    if week > weeks_in_year(year):
        return year + 1, 1
    return year, week


def easter(year):
    """(month, day) of Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    n = h + l - 7 * m + 114
    return n // 31, n % 31 + 1


def holidays(year):
    """{(month, day): name} of the Italian public holidays of a year."""
    out = dict(_FIXED)
    month, day = easter(year)
    out[(month, day)] = "Pasqua"
    if day == days_in_month(year, month):
        month, day = month + 1, 1
    else:
        day += 1
    out[(month, day)] = "Luned\xec dell'Angelo"
    return out


def grid(year, month):
    """[(week, [day or 0] * 7)] for the weeks of a month, Monday first."""
    first = weekday(year, month, 1)
    total = days_in_month(year, month)
    out = []
    day = 1 - first
    while day <= total:
        week = iso_week(year, month, max(day, 1))[1]
        out.append((week, [d if 1 <= d <= total else 0 for d in range(day, day + 7)]))
        day += 7
    return out


# ── screen ──────────────────────────────────────


def _cell(day, column, holiday, today):
    text = "{:>4}".format(day) if day else "    "
    if not day:
        return text
    if today:
        return " " + _paint(text[1:], BLACK, BCYAN)
    if holiday or column == 6:
        return _paint(text, BRED)
    return _paint(text, GREY) if column == 5 else text


def month_lines(year, month, today=None):
    """The month as screen lines (colours included); today: (y, m, d) or None."""
    marked = holidays(year)
    lines = [" " + _paint("{} {}".format(MONTHS[month - 1], year), BCYAN), ""]
    lines.append(_paint(" Wk", GREY) + " Mon Tue Wed Thu Fri " + _paint("Sat", GREY) + " " + _paint("Sun", BRED))
    for week, days in grid(year, month):
        row = _paint("{:>3}".format(week), GREY)
        for column, day in enumerate(days):
            row += _cell(day, column, (month, day) in marked, today == (year, month, day))
        lines.append(row)
    return lines


def notes(year, month, today=None):
    """Lines under the grid: the month's holidays, then today's date."""
    out = []
    for (m, d), name in sorted(holidays(year).items()):
        if m == month:
            out.append(" " + _paint("{:>2}".format(d), BRED) + " " + name)
    if today and today[:2] == (year, month):
        y, m, d = today
        week = iso_week(y, m, d)[1]
        out.append("")
        out.append(_paint(" {} {} {}: day {}, week {}".format(DAYS[weekday(y, m, d)], d, MONTHS[m - 1], day_of_year(y, m, d), week), WHITE))
    return out


def _today():
    t = _local_time()
    return t[0], t[1], t[2]


def show():
    """Month view from today; arrows move, t today, q back."""
    today = _today()
    year, month = today[0], today[1]
    while True:
        title = "Calendar" if _clock_synced() else "Calendar (clock not set)"
        _screen_header(title)
        lines = month_lines(year, month, today)
        room = SCREEN_ROWS - len(lines) - 6
        extra = notes(year, month, today)
        if len(extra) <= room:
            lines += [""] + extra
        print("\n".join(lines))
        print(_paint("─" * DISPLAY_WIDTH, GREY))
        _key_bar((("←→", "month"), ("↑↓", "year"), ("t", "today"), ("q", "back")))
        key = _read_key()
        if key in _QUIT:
            return None
        if key in ("left", "right"):
            month += 1 if key == "right" else -1
            if month < 1:
                year, month = year - 1, 12
            elif month > 12:
                year, month = year + 1, 1
        elif key in ("up", "down"):
            year += 1 if key == "down" else -1
        elif key == "t":
            today = _today()
            year, month = today[0], today[1]


# ── standard ────────────────────────────────────


def ver():
    print("cal:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Calendar --")
    print("show()  s()   Month view: arrows move, t today")
    print("easter(year)  holidays(year)  iso_week(y, m, d)")


def h():
    return help()


s = show
