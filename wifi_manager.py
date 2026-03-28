import time

try:
    import ujson as json
except ImportError:
    import json


CREDENTIALS_FILE = "wifi_credentials.json"
CONNECT_TIMEOUT_SECONDS = 8
CONNECT_POLL_INTERVAL_MS = 300
DISPLAY_LINE_CHARS = 32
SSID_PREVIEW_CHARS = 12
MENU_NETWORK_LIMIT = 6
WLAN_WARMUP_MS = 800
SCAN_RETRY_COUNT = 2
SCAN_RETRY_DELAY_MS = 1200
MAX_CONNECT_CANDIDATES = 2
WIFI_MANAGER_VERSION = "2026-03-28.1"
_NETWORK_MODULE = None


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
    value = str(text)
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


def _clip_ssid(ssid):
    return _clip(ssid, SSID_PREVIEW_CHARS)


def _decode_ssid(raw_ssid):
    if isinstance(raw_ssid, bytes):
        return raw_ssid.decode("utf-8", "ignore")
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
    results = wlan.scan()
    networks = []
    seen = set()

    for item in results:
        ssid = _decode_ssid(item[0]).strip()
        rssi = item[3]

        if not ssid or ssid in seen:
            continue

        seen.add(ssid)
        networks.append((ssid, rssi))

    networks.sort(key=lambda x: x[1], reverse=True)
    return networks


def connect_to_wifi(wlan, ssid, password, timeout=CONNECT_TIMEOUT_SECONDS):
    network = _network_module()

    if wlan.isconnected():
        wlan.disconnect()
        time.sleep(0.1)

    wlan.connect(ssid, password)

    timeout_ms = int(timeout * 1000)
    start = time.ticks_ms()

    while time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
        status = wlan.status()

        if status == network.STAT_GOT_IP:
            return True

        if status in (network.STAT_WRONG_PASSWORD, network.STAT_NO_AP_FOUND, network.STAT_CONNECT_FAIL):
            return False

        time.sleep_ms(CONNECT_POLL_INTERVAL_MS)

    return wlan.isconnected()


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
    print("WiFi:", info["connected"])
    print("St:", info["status"])
    if info["ssid"]:
        print("SSID:", _clip_ssid(info["ssid"]))
    if info["ifconfig"]:
        print("IP:", info["ifconfig"][0])


def _safe_input(prompt):
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""


def choose_network(networks):
    if not networks:
        print("No WiFi networks.")
        return None

    print("\nWiFi networks:")
    visible_networks = networks[:MENU_NETWORK_LIMIT]
    for index, (ssid, rssi) in enumerate(visible_networks, start=1):
        print("{}: {} {}dBm".format(index, _clip_ssid(ssid), rssi))

    if len(networks) > MENU_NETWORK_LIMIT:
        print("Showing top {} of {}".format(MENU_NETWORK_LIMIT, len(networks)))

    MAX_ATTEMPTS = 5
    attempts = 0
    while attempts < MAX_ATTEMPTS:
        choice = _safe_input("Net # (Enter=cancel): ").strip()

        if choice == "":
            return None

        try:
            selected_index = int(choice)
        except ValueError:
            print("Please enter a valid number.")
            attempts += 1
            continue

        if 1 <= selected_index <= len(visible_networks):
            return visible_networks[selected_index - 1][0]

        print("Selection out of range.")
        attempts += 1

    print("Too many invalid attempts.")
    return None


def connect_saved_networks(wlan, credentials):
    if not credentials:
        print("No saved.")
        return False

    scanned_ssids = []
    for attempt in range(SCAN_RETRY_COUNT):
        try:
            networks = scan_networks(wlan)
            scanned_ssids = [ssid for ssid, _ in networks]
            if scanned_ssids:
                break
        except Exception as error:
            print("Scan err:", _clip(error, 24))

        if attempt < SCAN_RETRY_COUNT - 1:
            print("Scan retry")
            time.sleep_ms(SCAN_RETRY_DELAY_MS)

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

    for ssid in limited_candidates:
        print("Try:", _clip_ssid(ssid))
        if connect_to_wifi(wlan, ssid, credentials[ssid]):
            print("OK:", _clip_ssid(ssid))
            print("IP:", wlan.ifconfig()[0])
            return True
        print("Fail:", _clip_ssid(ssid))

    return False


def auto_connect_or_prompt(interactive=True):
    wlan = _sta_wlan(active=True)
    time.sleep_ms(WLAN_WARMUP_MS)

    if wlan.isconnected():
        print("Already up. IP:", wlan.ifconfig()[0])
        return True

    credentials = load_credentials()
    if connect_saved_networks(wlan, credentials):
        return True

    if not interactive:
        print("No saved. Prompt off.")
        return False

    try:
        networks = scan_networks(wlan)
    except Exception as error:
        print("Scan err:", _clip(error, 24))
        print("No selection.")
        return False

    selected_ssid = choose_network(networks)
    if not selected_ssid:
        print("No selection.")
        return False

    password = _safe_input("Pass '{}': ".format(_clip_ssid(selected_ssid)))
    if password == "":
        print("No password.")
        return False

    print("Connecting:", _clip_ssid(selected_ssid))
    if connect_to_wifi(wlan, selected_ssid, password):
        credentials[selected_ssid] = password
        save_credentials(credentials)
        print("OK. Saved.")
        print("IP:", wlan.ifconfig()[0])
        return True

    print("Connect failed.")
    return False


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
    print("tip: import wifi_manager as w")


def h():
    return help()


if __name__ == "__main__":
    auto_connect_or_prompt()
