import time

from pico_utils import net_error as _net_error
from pico_utils import screen_header as _screen_header, paged_lines as _paged_lines
from pico_utils import sleep_ms as _sleep_ms, ticks_ms as _ticks_ms, ticks_diff as _ticks_diff
from pico_utils import ticks_add as _ticks_add
from pico_utils import read_key as _read_key, read_line as _read_line, poll_key as _poll_key
from pico_utils import clock_synced as _clock_synced, clean as _clean, save_json as _save_json
from pico_utils import settings as _settings, set_setting as _set_setting
from pico_utils import add_service as _add_service, refresh_status as _refresh_status
from pico_utils import remove_service as _remove_service

try:
    import ujson as json
except ImportError:
    import json


CREDENTIALS_FILE = "wifi_credentials.json"
CONNECT_TIMEOUT_SECONDS = 8
CONNECT_POLL_INTERVAL_MS = 300
MENU_NETWORK_LIMIT = 9  # one key each
WLAN_WARMUP_MS = 800
SCAN_RETRY_COUNT = 2
SCAN_RETRY_DELAY_MS = 1200
MAX_CONNECT_CANDIDATES = 2
WIFI_MANAGER_VERSION = "2026-10-12.1"
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
    if active and not wlan.active():
        wlan.active(True)
        _sleep_ms(WLAN_WARMUP_MS)  # the radio needs a moment after power-up
    return wlan


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
    # A name with control characters is left out the same way: printed, an
    # ESC in it would drive the screen terminal (anyone nearby picks a name).
    if isinstance(raw_ssid, bytes):
        try:
            name = raw_ssid.strip(b"\x00").decode("utf-8")
        except UnicodeError:
            return ""
    else:
        name = str(raw_ssid)
    return name if _clean(name) == name else ""


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
    # through .tmp/.bak, with the panel refresh held (pico_utils.save_json)
    return _save_json(CREDENTIALS_FILE, credentials)


def _remember(ssid):
    # the network joined last is tried first at the next start
    if ssid and _settings().get("wifi_last") != ssid:
        _set_setting("wifi_last", ssid)


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


def _scan_retry(wlan):
    # scan_networks(), once more after a pause if it fails or finds nothing
    for attempt in range(SCAN_RETRY_COUNT):
        try:
            networks = scan_networks(wlan)
            if networks:
                return networks
        except Exception as error:
            print("Scan:", _net_error(error))

        if attempt < SCAN_RETRY_COUNT - 1:
            print("Scan retry")
            _sleep_ms(SCAN_RETRY_DELAY_MS)
    return []


def connect_saved_networks(wlan, credentials, networks=None):
    """True once connected, False if no saved network joined, None when
    q/Esc cancelled (no next network is tried). networks: a scan_networks()
    list to pick from; None scans here."""
    if not credentials:
        print("No saved.")
        return False

    if networks is None:
        networks = _scan_retry(wlan)
    scanned_ssids = [found[0] for found in networks]

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
            _remember(ssid)
            _sync_clock()
            return True
        _print_failure(status)

    return False


def auto_connect_or_prompt(interactive=True):
    stop_join()  # the launcher's background join would switch networks under it
    wlan = _sta_wlan(active=True)

    if wlan.isconnected():
        print("Already up. IP:", wlan.ifconfig()[0])
        return True

    credentials = load_credentials()
    # one scan serves the saved networks and then the chooser
    networks = _scan_retry(wlan) if credentials or interactive else []
    joined = connect_saved_networks(wlan, credentials, networks)
    if joined or joined is None:
        return joined  # connected, or q/Esc: no chooser after a cancel

    if not interactive:
        print("No saved. Prompt off.")
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
        _remember(selected_ssid)
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
        print("NTP:", _net_error(error))


# ── background join ─────────────────────────────
# The launcher starts here instead of waiting on a scan and a connect:
# connect() returns at once and the radio joins while the menu is up. The
# saved networks are tried one after the other, the last one joined first,
# with no scan (one blocks for ~2 s); once joined, the clock is set quietly.

JOIN_TIMEOUT_MS = 12000  # per network
JOIN_WARMUP_MS = 2000  # radio power-up to the first scan (0.8 s missed the AP more often)
JOIN_GRACE_MS = 1500  # after connect() the link status still shows the last try's failure
JOIN_ROUNDS = 5  # passes over the saved networks, about a minute in all
JOIN_ROUND_GAP_MS = 4000  # before pass 2; each later gap is that much longer
# (measured over 16 resets: a pass right after power-up missed the network in
# one boot in four; a scan and a connect 20 s later always joined)
NTP_RETRY_MS = 30000
_JOIN = {}  # order, names: the pass; ssid, since: the try; round, wait: when to try
LAST_JOIN = [None]  # (ticks_ms() when joined, pass) of the last background join
_NTP = [0, 0]  # quiet NTP tries, ticks of the last


def start_join():
    """Join a saved network in the background (pico_utils services): True if
    a join started. The menu stays usable; the title bar says WiFi once up."""
    credentials = load_credentials()
    if not credentials:
        return False
    wlan = _sta_wlan()
    warm = 0
    if not wlan.active():
        wlan.active(True)
        # the radio's first scan right after power-up finds nothing (connect
        # scans): the service connects after the warm-up, the menu draws now
        warm = JOIN_WARMUP_MS
    if wlan.isconnected():
        _joined(None)
        return False
    order = sorted(credentials)
    last = _settings().get("wifi_last")
    if last in credentials:
        order.remove(last)
        order.insert(0, last)
    _JOIN.clear()
    _JOIN["names"] = order
    _JOIN["order"] = list(order)
    _JOIN["round"] = 1
    _JOIN["scan"] = True
    _JOIN["wait"] = _ticks_add(_ticks_ms(), warm)
    _add_service(_join_service)
    return True


def joining():
    """True while a background join is under way."""
    return bool(_JOIN)


def stop_join():
    """End a background join (a connect by hand takes over)."""
    _JOIN.clear()
    _remove_service(_join_service)


def _join_next(wlan, credentials):
    # Connect to the next saved network of this pass; at the end of a pass,
    # wait JOIN_ROUND_GAP_MS and start another (a network can be busy or out
    # of reach for a moment). False once the passes are over.
    if _JOIN.get("scan"):
        # One scan first in each pass (~0.7 s): seen on the device, connect()
        # alone answered 'network not found' after one boot in four, pass
        # after pass, and a connect right after a scan joined in 3 s.
        _JOIN["scan"] = False
        try:
            wlan.scan()
        except Exception:
            pass
    while _JOIN.get("order"):
        ssid = _JOIN["order"].pop(0)
        if ssid not in credentials:
            continue
        try:
            wlan.connect(ssid, credentials[ssid])
        except Exception:
            continue
        _JOIN["ssid"] = ssid
        _JOIN["since"] = _ticks_ms()
        return True
    if _JOIN.get("round", JOIN_ROUNDS) < JOIN_ROUNDS and _JOIN.get("names"):
        _JOIN["wait"] = _ticks_add(_ticks_ms(), JOIN_ROUND_GAP_MS * _JOIN["round"])
        _JOIN["round"] += 1
        _JOIN["order"] = list(_JOIN["names"])
        _JOIN["scan"] = True
        _JOIN["ssid"] = None
        return True
    _JOIN.clear()
    return False


def _join_service(now, waiting):
    if not _JOIN:
        return False
    wlan = _sta_wlan()
    if wlan.isconnected():
        ssid = _JOIN.get("ssid")
        LAST_JOIN[0] = (now, _JOIN.get("round"))
        _JOIN.clear()
        _joined(ssid)
        return False
    if "wait" in _JOIN:
        if _ticks_diff(now, _JOIN["wait"]) < 0:
            return True  # the radio warming up, or the gap between passes
        del _JOIN["wait"]
        return _join_next(wlan, load_credentials())
    # A failure counts only after the grace time (traced on the device: right
    # after connect() the status was still the last try's -3, so the next
    # network looked failed at once), and 'wrong password' (-3) is no reason
    # to give a network up: the home network answered it after an abrupt
    # reset, while it still held the old association, and joined later.
    status = wlan.status()
    tried = _ticks_diff(now, _JOIN["since"])
    if (status < 0 and tried > JOIN_GRACE_MS) or tried > JOIN_TIMEOUT_MS:
        return _join_next(wlan, load_credentials())  # the next one, or give up
    return True


def _joined(ssid):
    _refresh_status()
    _remember(ssid)
    if not _clock_synced():
        _NTP[0] = 0
        _add_service(_ntp_service)


def _ntp_service(now, waiting):
    # The clock, quietly: only in a key wait (the request waits up to 2 s, and
    # its DNS lookup, which that timeout does not cover, up to lwIP's limit
    # when the network's DNS is down), three tries 30 s apart.
    if _clock_synced():
        return False
    if not waiting or (_NTP[0] and _ticks_diff(now, _NTP[1]) < NTP_RETRY_MS):
        return True
    _NTP[0] += 1
    _NTP[1] = now
    try:
        import clock_ntp

        if clock_ntp.sync_quiet():
            _refresh_status()
            return False
    except Exception:
        pass
    return _NTP[0] < 3


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
