import gc
import os
import time

try:
    import ujson as json
except ImportError:
    import json


CONFIG_FILE = "openrouter_config.json"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"
DEFAULT_SYSTEM_PROMPT = "Reply concise. Use short lines for a tiny 32-char display. Prefer plain text."
DISPLAY_WIDTH = 32
PAGE_LINES = 8
MAX_PROMPT_CHARS = 480
MAX_OUTPUT_CHARS = 1400
MODULE_VERSION = "2026-02-15.7"
MAX_HISTORY_MESSAGES = 6
_HISTORY = []
_MEMORY_ENABLED = True
PRESET_PROMPTS = {
    "brief": "Reply very concise. Max 4 short bullet points. Plain text.",
    "teacher": "Explain step by step in simple language. Keep each line short.",
    "code": "Focus on practical code help. Use short snippets and concise notes.",
}


def _load_config():
    try:
        with open(CONFIG_FILE, "r") as file:
            data = json.load(file)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_config(config):
    print("Saving config...")
    tmp_file = CONFIG_FILE + ".tmp"
    try:
        with open(tmp_file, "w") as file:
            json.dump(config, file)
            try:
                file.flush()
            except Exception:
                pass
    except Exception as error:
        print("Save err:", error)
        return False
    try:
        os.sync()
    except Exception:
        pass
    try:
        os.remove(CONFIG_FILE)
    except Exception:
        pass
    try:
        os.rename(tmp_file, CONFIG_FILE)
    except Exception as error:
        print("Rename err:", error)
        return False
    try:
        gc.collect()
    except Exception:
        pass
    print("Saved.")
    return True


def _set_config_value(key, value, empty_message, saved_message, show_value=False):
    text = str(value).strip()
    if text == "":
        print(empty_message)
        return False
    config = _load_config()
    config[key] = text
    _save_config(config)
    if show_value:
        print(saved_message, text)
    else:
        print(saved_message)
    return True


def _clip(text, limit):
    value = str(text)
    if len(value) <= limit:
        return value
    return value[:limit]


def _wrap_text(text, width=DISPLAY_WIDTH):
    source = str(text).replace("\r\n", "\n").replace("\r", "\n")
    wrapped = []
    for paragraph in source.split("\n"):
        paragraph = paragraph.strip()
        if paragraph == "":
            wrapped.append("")
            continue

        line = ""
        for word in paragraph.split(" "):
            if word == "":
                continue
            if line == "":
                if len(word) <= width:
                    line = word
                else:
                    start = 0
                    while start < len(word):
                        wrapped.append(word[start : start + width])
                        start += width
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                wrapped.append(line)
                if len(word) <= width:
                    line = word
                else:
                    start = 0
                    while start < len(word):
                        wrapped.append(word[start : start + width])
                        start += width
                    line = ""
        if line:
            wrapped.append(line)
    return wrapped


def _paged_print(text):
    lines = _wrap_text(text)
    if not lines:
        print("(empty)")
        return

    count = 0
    total = len(lines)
    for index, line in enumerate(lines):
        print(line)
        count += 1
        if count >= PAGE_LINES and index < total - 1:
            try:
                answer = input("--more-- Enter/q: ").strip().lower()
            except Exception:
                answer = ""
            if answer == "q":
                print("(stopped)")
                break
            count = 0


def _http_module():
    try:
        import urequests as requests

        return requests
    except ImportError:
        print("Missing urequests.")
        print("Install: import mip; mip.install('urequests')")
        return None


def set_api_key(api_key):
    value = str(api_key).strip()
    if value == "":
        print("Empty key.")
        return False

    config = _load_config()
    config["api_key"] = value
    if "model" not in config:
        config["model"] = DEFAULT_MODEL
    _save_config(config)
    print("API key saved.")
    return True


def set_model(model):
    return _set_config_value("model", model, "Empty model.", "Model:", show_value=True)


def set_system_prompt(text):
    return _set_config_value("system_prompt", text, "Empty prompt.", "System prompt saved.")


def clear_system_prompt():
    config = _load_config()
    if "system_prompt" in config:
        del config["system_prompt"]
        _save_config(config)
    print("System prompt cleared.")
    return True


def presets():
    names = sorted(PRESET_PROMPTS.keys())
    print("Presets:", ", ".join(names))
    return names


def preset(name):
    key = str(name).strip().lower()
    if key not in PRESET_PROMPTS:
        print("Unknown preset.")
        presets()
        return False
    return set_system_prompt(PRESET_PROMPTS[key])


def p(name):
    return preset(name)


def show_config():
    config = _load_config()
    model = config.get("model", DEFAULT_MODEL)
    has_key = bool(config.get("api_key"))
    has_system = bool(config.get("system_prompt"))
    print("Model:", model)
    print("Key:", has_key)
    print("System:", has_system)
    return {"model": model, "has_key": has_key, "has_system": has_system}


def _extract_text(response_json):
    if not isinstance(response_json, dict):
        return None

    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        return None

    first = choices[0]
    if not isinstance(first, dict):
        return None

    message = first.get("message")
    if not isinstance(message, dict):
        return None

    content = message.get("content")
    if content is None:
        return None

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n".join(parts)

    return str(content)


def _history_append(role, content):
    _HISTORY.append({"role": role, "content": content})
    if len(_HISTORY) > MAX_HISTORY_MESSAGES:
        del _HISTORY[: len(_HISTORY) - MAX_HISTORY_MESSAGES]


def mem_clear():
    _HISTORY[:] = []
    print("Memory cleared.")
    return True


def mem_on():
    global _MEMORY_ENABLED
    _MEMORY_ENABLED = True
    print("Memory: on")
    return True


def mem_off():
    global _MEMORY_ENABLED
    _MEMORY_ENABLED = False
    print("Memory: off")
    return True


def mem_status():
    info = {"enabled": _MEMORY_ENABLED, "messages": len(_HISTORY), "max": MAX_HISTORY_MESSAGES}
    print("Mem:", info["enabled"], "msgs:", info["messages"], "/", info["max"])
    return info


def ask(prompt, model=None, max_tokens=220, temperature=0.2, use_memory=None):
    requests = _http_module()
    if requests is None:
        return None

    config = _load_config()
    api_key = config.get("api_key", "")
    selected_model = model or config.get("model", DEFAULT_MODEL)
    system_prompt = config.get("system_prompt", DEFAULT_SYSTEM_PROMPT)

    if api_key == "":
        print("No API key. Use set_api_key(...)")
        return None

    prompt_text = _clip(str(prompt).strip(), MAX_PROMPT_CHARS)
    if prompt_text == "":
        print("Empty prompt.")
        return None

    use_mem = _MEMORY_ENABLED if use_memory is None else bool(use_memory)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": _clip(system_prompt, 220)})
    if use_mem and _HISTORY:
        for item in _HISTORY:
            messages.append({"role": item["role"], "content": item["content"]})
    messages.append({"role": "user", "content": prompt_text})

    payload = {
        "model": selected_model,
        "messages": messages,
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
    }

    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "HTTP-Referer": "https://picocalc.local",
        "X-Title": "PicoCalc",
    }

    print("AI>", selected_model)
    start_ms = time.ticks_ms()

    response = None
    try:
        response = requests.post(OPENROUTER_URL, headers=headers, data=json.dumps(payload))
        status = response.status_code
        body = response.json()
    except Exception as error:
        print("Request fail:", error)
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        return None

    try:
        response.close()
    except Exception:
        pass

    if status != 200:
        print("HTTP:", status)
        if isinstance(body, dict) and "error" in body:
            print("Err:", _clip(body["error"], 80))
        else:
            print("Bad response")
        return None

    text = _extract_text(body)
    if text is None:
        print("No text in response")
        return None

    text = _clip(text, MAX_OUTPUT_CHARS)
    elapsed_ms = time.ticks_diff(time.ticks_ms(), start_ms)
    print("---")
    _paged_print(text)
    print("---")
    print("ms:", elapsed_ms)

    if use_mem:
        _history_append("user", prompt_text)
        _history_append("assistant", text)

    return text


def chat():
    print("AI chat. Empty=exit")
    while True:
        try:
            prompt = input("Q> ").strip()
        except Exception:
            prompt = ""

        if prompt == "":
            print("Bye")
            return

        ask(prompt)


def ver():
    print("openrouter_ai:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("cmd: ver ask chat")
    print("cmd: set_api_key set_model")
    print("cmd: set_system_prompt clear_system_prompt")
    print("cmd: presets preset p")
    print("cmd: mem_on mem_off mem_status mem_clear")
    print("cmd: show_config help h")
    print("tip: import openrouter_ai as ai")


def h():
    return help()
