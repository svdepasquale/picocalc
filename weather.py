import gc
import time

from pico_utils import clip as _clip
from pico_utils import load_json, save_json, http_module as _http_module, check_wifi
from pico_utils import http_request as _http_request, net_error as _net_error
from pico_utils import paint as _paint, GREY, BWHITE, BBLUE, BCYAN, BGREEN, BYELLOW, BRED
from pico_utils import BMAGENTA, WHITE
from pico_utils import ticks_ms as _ticks_ms, ticks_diff as _ticks_diff


CONFIG_FILE = "weather_config.json"
MODULE_VERSION = "2026-10-07.1"
API_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
DEFAULT_LAT = 41.9
DEFAULT_LON = 12.5
_GEO_CACHE = {}

WMO_CODES = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Frz drizzle",
    57: "Heavy frz drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Heavy frz rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Heavy snow shwrs",
    95: "Thunderstorm",
    96: "T-storm+hail",
    99: "T-storm+hail",
}


def _load_config():
    data = load_json(CONFIG_FILE)
    if isinstance(data, dict):
        return data
    return {}


def _save_config(config):
    return save_json(CONFIG_FILE, config)


def _temp_color(value):
    try:
        t = float(value)
    except Exception:
        return WHITE
    if t <= 0:
        return BBLUE
    if t <= 10:
        return BCYAN
    if t <= 20:
        return BGREEN
    if t <= 28:
        return BYELLOW
    return BRED


def _sky_color(code):
    if code in (0, 1):
        return BYELLOW  # clear
    if code in (2, 3):
        return WHITE  # clouds
    if code in (45, 48):
        return GREY  # fog
    if code in (71, 73, 75, 77, 85, 86):
        return BWHITE  # snow
    if code in (95, 96, 99):
        return BMAGENTA  # storms
    return BCYAN  # drizzle, rain, showers


def _temp(value):
    return _paint("{}\u00b0C".format(value), _temp_color(value))


def _get_location():
    config = _load_config()
    lat = config.get("lat", DEFAULT_LAT)
    lon = config.get("lon", DEFAULT_LON)
    name = config.get("name", "")
    return lat, lon, name


def _location_label(name, lat, lon):
    if name:
        return _clip(name, 20)
    return "{},{}".format(lat, lon)


def set_location(lat, lon, name=""):
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except Exception:
        print("Invalid coordinates.")
        return False
    if lat_f < -90 or lat_f > 90:
        print("Lat range: -90..90")
        return False
    if lon_f < -180 or lon_f > 180:
        print("Lon range: -180..180")
        return False
    config = _load_config()
    config["lat"] = lat_f
    config["lon"] = lon_f
    config["name"] = _clip(str(name).strip(), 28) if name else ""
    if not _save_config(config):
        print("Location not saved.")  # after save_json's own error line
        return False
    _LAST_FETCH[2] = None  # its weather was for the old place
    label = _location_label(config["name"], lat_f, lon_f)
    print("Location:", label)
    return True


def _url_encode(text):
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    out = []
    for ch in str(text):
        if ch in safe:
            out.append(ch)
        elif ch == " ":
            out.append("+")
        else:
            for b in ch.encode("utf-8"):
                out.append("%{:02X}".format(b))
    return "".join(out)


def set_city(name):
    city = str(name).strip()
    if city == "":
        print("Empty city name.")
        return False

    cache_key = city.lower()
    if cache_key in _GEO_CACHE:
        cached = _GEO_CACHE[cache_key]
        print("Cached:", _clip(cached["name"], 24))
        return set_location(cached["lat"], cached["lon"], cached["name"])

    requests = _http_module()
    if requests is None:
        return False

    if not check_wifi():
        return False

    url = "{}?name={}&count=1&language=en&format=json".format(
        GEOCODING_URL, _url_encode(city)
    )

    print("Looking up:", _clip(city, 24))
    response = None
    try:
        response = _http_request(requests, "GET", url)
        status = response.status_code
        if status != 200:
            print("HTTP:", status)
            return False
        data = response.json()
    except Exception as e:
        print("Err:", _net_error(e))
        return False
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    results = data.get("results")
    if not isinstance(results, list) or not results:
        print("City not found:", _clip(city, 20))
        del data
        gc.collect()
        return False

    match = results[0]
    lat = match.get("latitude")
    lon = match.get("longitude")
    if lat is None or lon is None:
        print("No coordinates for:", _clip(city, 18))
        del data
        gc.collect()
        return False
    found_name = match.get("name", city)
    country = match.get("country", "")
    label = found_name
    if country:
        label = "{}, {}".format(found_name, country)

    del data
    gc.collect()

    clipped_label = _clip(label, 28)
    _GEO_CACHE[cache_key] = {"lat": lat, "lon": lon, "name": clipped_label}
    return set_location(lat, lon, clipped_label)


def show_location():
    lat, lon, name = _get_location()
    print("Lat:", lat)
    print("Lon:", lon)
    if name:
        print("Name:", name)
    return {"lat": lat, "lon": lon, "name": name}


# ── one request ─────────────────────────────────
# The current weather and the forecast come in one request: one TLS
# handshake (~20 KB of mbedtls buffers) per screen. The menu's screen calls
# now() then forecast(3): forecast() reuses a request made in the last
# REUSE_MS, a failed one too (one "No WiFi", not two).

REPORT_DAYS = 3  # what now() fetches: the menu's forecast(3) then needs no request
REUSE_MS = 5000
_LAST_FETCH = [None, 0, None]  # data (None: failed), days, ticks (None: no fetch yet)


def _days(days):
    try:
        return max(1, min(7, int(days)))
    except Exception:
        return 3


def _fetch(days):
    requests = _http_module()
    if requests is None or not check_wifi():
        return None

    lat, lon, name = _get_location()
    url = (
        "{}?latitude={}&longitude={}&current_weather=true"
        "&daily=temperature_2m_max,temperature_2m_min,weathercode"
        "&forecast_days={}&timezone=auto"
    ).format(API_URL, lat, lon, days)

    print(_paint("updating...", GREY))
    response = None
    try:
        response = _http_request(requests, "GET", url)
        status = response.status_code
        if status != 200:
            print("HTTP:", status)
            return None
        data = response.json()
    except Exception as e:
        print("Err:", _net_error(e))
        return None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    if not isinstance(data, dict):
        print("Bad response.")
        return None
    return {
        "current": data.get("current_weather"),
        "daily": data.get("daily"),
        "name": name,
        "label": _location_label(name, lat, lon),
    }


def _weather(days, reuse=False):
    # A new request; with reuse, the last one if it is recent and has the days.
    last = _LAST_FETCH
    if reuse and last[2] is not None and _ticks_diff(_ticks_ms(), last[2]) < REUSE_MS:
        if last[0] is None or last[1] >= days:
            return last[0]
    data = _fetch(days)
    last[0], last[1], last[2] = data, days, _ticks_ms()
    gc.collect()  # the whole parsed response, of which data keeps a little
    return data


def _show_now(data, elapsed):
    cw = data["current"]
    if not isinstance(cw, dict):
        print("Bad response.")
        return None

    temp = cw.get("temperature", "?")
    wind = cw.get("windspeed", "?")
    wdir = cw.get("winddirection", "?")
    code = cw.get("weathercode", -1)
    wtime = cw.get("time", "")

    desc = WMO_CODES.get(code, "Code:{}".format(code))

    print(_paint(data["label"], BWHITE) + "  " + _paint(desc, _sky_color(code)))
    if not data["name"]:
        print("Tip: set_location(lat,lon,'CityName')")
    print("Now {}   wind {} km/h {}\u00b0".format(_temp(temp), wind, wdir))
    if wtime:
        print(_paint("at " + _clip(wtime, 20).replace("T", " ") + "  " + str(elapsed) + " ms", GREY))

    return {"temp": temp, "wind": wind, "wind_dir": wdir, "desc": desc, "time": wtime}


def now():
    start = _ticks_ms()
    data = _weather(REPORT_DAYS)
    if data is None:
        return None
    return _show_now(data, _ticks_diff(_ticks_ms(), start))


def forecast(days=3):
    num_days = _days(days)
    data = _weather(num_days, reuse=True)
    if data is None:
        return None
    return _show_days(data, num_days)


def report(days=REPORT_DAYS):
    """Current weather and the forecast, from one request."""
    num_days = _days(days)
    start = _ticks_ms()
    data = _weather(num_days)
    if data is None:
        return None
    current = _show_now(data, _ticks_diff(_ticks_ms(), start))
    print("")
    return {"now": current, "days": _show_days(data, num_days)}


def _show_days(data, num_days):
    daily = data["daily"]
    if not isinstance(daily, dict):
        print("Bad response.")
        return None

    dates = daily.get("time", [])
    tmax = daily.get("temperature_2m_max", [])
    tmin = daily.get("temperature_2m_min", [])
    codes = daily.get("weathercode", [])

    print(_paint("next {} days".format(num_days), GREY))
    forecast_data = []
    for i in range(min(num_days, len(dates))):
        d = str(dates[i]) if i < len(dates) else "?"
        short_d = d[5:] if len(d) >= 10 else d
        hi = tmax[i] if i < len(tmax) else "?"
        lo = tmin[i] if i < len(tmin) else "?"
        c = codes[i] if i < len(codes) else -1
        desc = WMO_CODES.get(c, "?")
        print("{}  {} .. {}  {}".format(short_d, _temp(lo), _temp(hi), _paint(desc, _sky_color(c))))
        forecast_data.append({"date": d, "min": lo, "max": hi, "desc": desc})
    return forecast_data


def ver():
    print("weather:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Weather --")
    print("now()/w()     Current weather")
    print("forecast(d)   Forecast 1-7 days")
    print("  fc(d)       Alias for forecast")
    print("report(d)/r(d) Both, one request")
    print("set_city(name)  Set by city")
    print("set_location(lat,lon,name)")
    print("  Set by coordinates")
    print("show_location() Show location")
    print("tip: import weather as m")


def h():
    return help()


def w():
    return now()


def fc(days=3):
    return forecast(days)


def r(days=REPORT_DAYS):
    return report(days)


def sc(name):
    return set_city(name)
