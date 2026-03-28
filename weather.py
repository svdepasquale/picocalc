import gc
import time

from pico_utils import clip as _clip
from pico_utils import load_json, save_json, http_module as _http_module, check_wifi


CONFIG_FILE = "weather_config.json"
MODULE_VERSION = "2026-03-28.1"
API_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
DEFAULT_LAT = 41.9
DEFAULT_LON = 12.5

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
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Light showers",
    81: "Showers",
    82: "Heavy showers",
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
    _save_config(config)
    label = _location_label(config["name"], lat_f, lon_f)
    print("Location:", label)
    return True


def set_city(name):
    city = str(name).strip()
    if city == "":
        print("Empty city name.")
        return False

    requests = _http_module()
    if requests is None:
        return False

    if not check_wifi():
        return False

    url = "{}?name={}&count=1&language=en&format=json".format(
        GEOCODING_URL, city.replace(" ", "+")
    )

    print("Looking up:", _clip(city, 24))
    response = None
    try:
        response = requests.get(url)
        status = response.status_code
        if status != 200:
            print("HTTP:", status)
            return False
        data = response.json()
    except Exception as e:
        print("Err:", _clip(e, 24))
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
    found_name = match.get("name", city)
    country = match.get("country", "")
    label = found_name
    if country:
        label = "{}, {}".format(found_name, country)

    del data
    gc.collect()

    return set_location(lat, lon, _clip(label, 28))


def show_location():
    lat, lon, name = _get_location()
    print("Lat:", lat)
    print("Lon:", lon)
    if name:
        print("Name:", name)
    return {"lat": lat, "lon": lon, "name": name}


def now():
    requests = _http_module()
    if requests is None:
        return None

    if not check_wifi():
        return None

    lat, lon, name = _get_location()
    label = _location_label(name, lat, lon)
    url = "{}?latitude={}&longitude={}&current_weather=true".format(API_URL, lat, lon)

    print("Weather>", label)
    response = None
    start = time.ticks_ms()

    try:
        response = requests.get(url)
        status = response.status_code
        if status != 200:
            print("HTTP:", status)
            return None
        data = response.json()
    except Exception as e:
        print("Err:", _clip(e, 24))
        return None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    cw = data.get("current_weather")
    del data
    if not isinstance(cw, dict):
        print("Bad response.")
        gc.collect()
        return None

    temp = cw.get("temperature", "?")
    wind = cw.get("windspeed", "?")
    wdir = cw.get("winddirection", "?")
    code = cw.get("weathercode", -1)
    wtime = cw.get("time", "")

    desc = WMO_CODES.get(code, "Code:{}".format(code))
    elapsed = time.ticks_diff(time.ticks_ms(), start)

    print("---")
    print("Location:", label)
    if not name:
        print("Tip: set_location(lat,lon,'CityName')")
    print(desc)
    print("Temp: {}C".format(temp))
    print("Wind: {} km/h {}deg".format(wind, wdir))
    if wtime:
        print("At:", _clip(wtime, 20))
    print("ms:", elapsed)

    gc.collect()
    return None


def forecast(days=3):
    requests = _http_module()
    if requests is None:
        return None

    if not check_wifi():
        return None

    try:
        num_days = max(1, min(7, int(days)))
    except Exception:
        num_days = 3

    lat, lon, name = _get_location()
    label = _location_label(name, lat, lon)
    url = (
        "{}?latitude={}&longitude={}"
        "&daily=temperature_2m_max,temperature_2m_min,weathercode"
        "&timezone=auto&forecast_days={}"
    ).format(API_URL, lat, lon, num_days)

    print("Forecast>", label, "({}d)".format(num_days))
    response = None
    start = time.ticks_ms()

    try:
        response = requests.get(url)
        status = response.status_code
        if status != 200:
            print("HTTP:", status)
            return None
        data = response.json()
    except Exception as e:
        print("Err:", _clip(e, 24))
        return None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    daily = data.get("daily")
    del data
    if not isinstance(daily, dict):
        print("Bad response.")
        gc.collect()
        return None

    dates = daily.get("time", [])
    tmax = daily.get("temperature_2m_max", [])
    tmin = daily.get("temperature_2m_min", [])
    codes = daily.get("weathercode", [])

    elapsed = time.ticks_diff(time.ticks_ms(), start)

    print("---")
    print("Location:", label)
    if not name:
        print("Tip: set_location(lat,lon,'CityName')")
    for i in range(min(num_days, len(dates))):
        d = str(dates[i]) if i < len(dates) else "?"
        short_d = d[5:] if len(d) >= 10 else d
        hi = tmax[i] if i < len(tmax) else "?"
        lo = tmin[i] if i < len(tmin) else "?"
        c = codes[i] if i < len(codes) else -1
        desc = WMO_CODES.get(c, "?")
        print("{} {}".format(short_d, desc))
        print("  {}..{}C".format(lo, hi))

    print("ms:", elapsed)
    gc.collect()
    return None


def ver():
    print("weather:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Weather --")
    print("now()/w()     Current weather")
    print("forecast(d)   Forecast 1-7 days")
    print("  fc(d)       Alias for forecast")
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


def sc(name):
    return set_city(name)
