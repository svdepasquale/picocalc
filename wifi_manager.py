import time

from pico_utils import clip as _clip_util
from pico_utils import screen_header as _screen_header, paged_lines as _paged_lines
from pico_utils import sleep_ms as _sleep_ms, ticks_ms as _ticks_ms, ticks_diff as _ticks_diff
from pico_utils import read_key as _read_key, read_line as _read_line, poll_key as _poll_key
from pico_utils import clock_synced as _clock_synced

try:
    import ujson as json
except ImportError:
    import json


CREDENTIALS_FILE = "wifi_credentials.json"
CONNECT_TIMEOUT_SECONDS = 8
CONNECT_POLL_INTERVAL_MS = 300
DISPLAY_LINE_CHARS = 32
MENU_NETWORK_LIMIT = 9  # one key each
WLAN_WARMUP_MS = 800
SCAN_RETRY_COUNT = 2
SCAN_RETRY_DELAY_MS = 1200
MAX_CONNECT_CANDIDATES = 2
WIFI_MANAGER_VERSION = "2026-10-07.1"
_NETWORK_MODULE = None

# wlan.status() in words, for the WiFi screen and failed connects: the rp2
# port's STAT_* values (cyw43 link states), plus 2, joined but no IP yet,
# which has no STAT_ name. The failures are the negative ones.
_STATUS_TEXT = {
    3: "connected",
    2: "no IP yet",
    1: "connecting",
    0: "not connected",
    -1: "connect failed",
    -2: "network not found",
    -3: "wrong password",
}


def _network_module():
    global _NETWORK_MODULE
    if _NETWORK_MODULE is None:
        import network as network_module
        _NETWORK_MODULE = network_module
    return _NETWORK_MODULE


def _sta_wlan(active=False):
    network = _network_module()
    wlan = network.WLAN(network.STA_IF)
    if active:
        wlan.active(True)
    return wlan


def _clip(text, limit=DISPLAY_LINE_CHARS):
    return _clip_util(text, limit)


def _status_text(status):
    return _STATUS_TEXT.get(status, "status {}".format(status))


def _print_failure(status):
    # A connect that ended without an IP: the link's reason, or the timeout
    # while it was still on its way.
    text = _status_text(status)
    print("Fail:", text if status < 0 else "timed out, " + text)


def _decode_ssid(raw_ssid):
    # SSIDs are bytes: hidden networks scan as NULs, and a name need not be
    # UTF-8. MicroPython 1.27 raises UnicodeError on those even with
    # errors="ignore": "" for a name that can't be shown.
    if isinstance(raw_ssid, bytes):
        try:
            return raw_ssid.strip(b"\x00").decode("utf-8")
        except UnicodeError:
            return ""
    return str(raw_ssid)


def load_credentials():
    for path in (CREDENTIALS_FILE, CREDENTIALS_FILE + ".tmp", CREDENTIALS_FILE + ".bak"):
        try:
            with open(path, "r") as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            pass
    return {}


def save_credentials(credentials):
    import os as _os
    tmp = CREDENTIALS_FILE + ".tmp"
    bak = CREDENTIALS_FILE + ".bak"
    try:
        with open(tmp, "w") as file:
            json.dump(credentials, file)
            try:
                file.flush()
            except Exception:
                pass
    except Exception as e:
        print("Save err:", e)
        return False
    # Remove old backup
    try:
        _os.remove(bak)
    except Exception:
        pass
    # Rename current to backup (so it's never deleted without a replacement ready)
    try:
        _os.rename(CREDENTIALS_FILE, bak)
    except Exception:
        pass
    # Rename tmp to main
    try:
        _os.rename(tmp, CREDENTIALS_FILE)
    except Exception as e:
        print("Rename err:", e)
        # Try to restore from backup
        try:
            _os.rename(bak, CREDENTIALS_FILE)
        except Exception as re:
            print("Restore err:", re)
        return False
    # Clean up backup on success
    try:
        _os.remove(bak)
    except Exception:
        pass
    return True


def scan_networks(wlan):
    """(ssid, rssi, auth) for each name, strongest first: auth is the scan's
    security field, 0 for an open network (None if the scan has none)."""
    results = wlan.scan()
    networks = []
    seen = set()

    for item in results:
        ssid = _decode_ssid(item[0]).strip()
        rssi = item[3]

        if not ssid or ssid in seen:
            continue

        seen.add(ssid)
        networks.append((ssid, rssi, item[4] if len(item) > 4 else None))

    networks.sort(key=lambda x: x[1], reverse=True)
    return networks


def connect_to_wifi(wlan, ssid, password, timeout=CONNECT_TIMEOUT_SECONDS):
    """The link status it ends on: network.STAT_GOT_IP once connected, a
    failure or the status at the timeout otherwise (compare it, never test
    its truth: failures are negative). None when q/Esc cancelled."""
    network = _network_module()

    if wlan.isconnected():
        wlan.disconnect()
        time.sleep(0.1)

    wlan.connect(ssid, password)

    timeout_ms = int(timeout * 1000)
    start = _ticks_ms()

    while _ticks_diff(_ticks_ms(), start) < timeout_ms:
        status = wlan.status()

        if status == network.STAT_GOT_IP:
            return status

        if status in (network.STAT_WRONG_PASSWORD, network.STAT_NO_AP_FOUND, network.STAT_CONNECT_FAIL):
            return status

        if _poll_key() in ("q", "Q", "esc"):
            wlan.disconnect()
            print("Cancelled.")
            return None

        _sleep_ms(CONNECT_POLL_INTERVAL_MS)

    return wlan.status()


def get_connection_status():
    wlan = _sta_wlan()

    connected = wlan.isconnected()
    status = wlan.status()

    if connected:
        ip_config = wlan.ifconfig()
    else:
        ip_config = None

    ssid = None
    if connected:
        try:
            ssid = _decode_ssid(wlan.config("ssid"))
        except Exception:
            ssid = None

    return {
        "connected": connected,
        "status": status,
        "ifconfig": ip_config,
        "ssid": ssid,
    }


def print_connection_status():
    info = get_connection_status()
    print("WiFi:", _status_text(info["status"]))
    if info["ssid"]:
        print("SSID:", info["ssid"])
    if info["ifconfig"]:
        print("IP:", info["ifconfig"][0])


def choose_network(networks):
    """One key picks one of scan_networks()' first nine: its (ssid, rssi,
    auth), or None."""
    if not networks:
        print("No WiFi networks.")
        return None

    _screen_header("WiFi Setup")
    print("Choose network")
    print("---")
    visible_networks = networks[:MENU_NETWORK_LIMIT]
    lines = []
    for index, (ssid, rssi, auth) in enumerate(visible_networks, start=1):
        lines.append("{}: {} {}dBm{}".format(index, ssid, rssi, " open" if auth == 0 else ""))
    _paged_lines(lines, page_lines=MENU_NETWORK_LIMIT)

    if len(networks) > MENU_NETWORK_LIMIT:
        print("Showing top {} of {}".format(MENU_NETWORK_LIMIT, len(networks)))

    print("Press 1-{}, q/Esc cancel".format(len(visible_networks)))
    while True:
        try:
            key = _read_key()
        except KeyboardInterrupt:
            return None
        if key in (None, "q", "Q", "esc", "eof", "enter"):
            return None
        if key and len(key) == 1 and key.isdigit():
            selected_index = int(key)
            if 1 <= selected_index <= len(visible_networks):
                return visible_networks[selected_index - 1]


def connect_saved_networks(wlan, credentials):
    """True once connected, False if no saved network joined, None when
    q/Esc cancelled (no next network is tried)."""
    if not credentials:
        print("No saved.")
        return False

    scanned_ssids = []
    for attempt in range(SCAN_RETRY_COUNT):
        try:
            networks = scan_networks(wlan)
            scanned_ssids = [found[0] for found in networks]
            if scanned_ssids:
                break
        except Exception as error:
            print("Scan err:", _clip(error, 24))

        if attempt < SCAN_RETRY_COUNT - 1:
            print("Scan retry")
            _sleep_ms(SCAN_RETRY_DELAY_MS)

    if scanned_ssids:
        candidates = [ssid for ssid in scanned_ssids if ssid in credentials]
    else:
        candidates = list(credentials.keys())

    if not candidates:
        print("No candidates.")
        return False

    limited_candidates = candidates[:MAX_CONNECT_CANDIDATES]
    if len(candidates) > MAX_CONNECT_CANDIDATES:
        print("Top {} candidates".format(MAX_CONNECT_CANDIDATES))

    got_ip = _network_module().STAT_GOT_IP
    for ssid in limited_candidates:
        print("Try:", ssid)
        status = connect_to_wifi(wlan, ssid, credentials[ssid])
        if status is None:
            return None
        if status == got_ip:
            print("OK:", ssid)
            print("IP:", wlan.ifconfig()[0])
            _sync_clock()
            return True
        _print_failure(status)

    return False


def auto_connect_or_prompt(interactive=True):
    wlan = _sta_wlan(active=True)
    _sleep_ms(WLAN_WARMUP_MS)

    if wlan.isconnected():
        print("Already up. IP:", wlan.ifconfig()[0])
        return True

    credentials = load_credentials()
    joined = connect_saved_networks(wlan, credentials)
    if joined or joined is None:
        return joined  # connected, or q/Esc: no chooser after a cancel

    if not interactive:
        print("No saved. Prompt off.")
        return False

    try:
        networks = scan_networks(wlan)
    except Exception as error:
        print("Scan err:", _clip(error, 24))
        print("No selection.")
        return False

    chosen = choose_network(networks)
    if not chosen:
        print("No selection.")
        return False
    selected_ssid, _, auth = chosen

    print("Network:", selected_ssid)  # whole: a 32-byte name fits the line
    if auth == 0:
        password = ""  # open network: cyw43 joins with no key when it is empty
    else:
        try:
            password = _read_line("Password then Enter: ", mask="*")
        except KeyboardInterrupt:
            password = None
        if not password:
            print("No password.")
            return False

    print("Connecting...")
    status = connect_to_wifi(wlan, selected_ssid, password)
    if status is None:
        return None
    if status == _network_module().STAT_GOT_IP:
        credentials[selected_ssid] = password
        # save_credentials() prints what failed; the link is up either way
        print("OK. Saved." if save_credentials(credentials) else "Connected, not saved.")
        print("IP:", wlan.ifconfig()[0])
        _sync_clock()
        return True

    _print_failure(status)
    return False


def _sync_clock():
    # after power-on the clock reads 2021-01-01: set it once we are online
    if _clock_synced():
        return
    try:
        import clock_ntp

        clock_ntp.sync()
    except Exception as error:
        print("NTP:", _clip(error, 24))


def saved():
    names = sorted(load_credentials().keys())
    if not names:
        print("No saved networks.")
        return []
    for index, name in enumerate(names, start=1):
        print("{}: {}".format(index, name))
    return names


def forget(name_or_index):
    credentials = load_credentials()
    names = sorted(credentials.keys())
    name = None
    try:
        pos = int(name_or_index) - 1
        if 0 <= pos < len(names):
            name = names[pos]
    except ValueError:
        if name_or_index in credentials:
            name = name_or_index
    if name is None:
        print("Not found.")
        return False
    del credentials[name]
    if not save_credentials(credentials):
        return False
    print("Forgot:", name)
    return True


def ac(interactive=True):
    return auto_connect_or_prompt(interactive=interactive)


def acs():
    return auto_connect_or_prompt(interactive=False)


def st():
    print_connection_status()


def ver():
    print("wifi_manager:", WIFI_MANAGER_VERSION)
    return WIFI_MANAGER_VERSION


def help():
    print("-- WiFi Manager --")
    print("ac()     Auto-connect/prompt")
    print("acs()    Auto-connect silent")
    print("st()     Connection status")
    print("saved()  Saved networks")
    print("forget(n) Forget a network")
    print("tip: import wifi_manager as w")


def h():
    return help()


if __name__ == "__main__":
    auto_connect_or_prompt()
