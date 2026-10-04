import gc
import time

from pico_utils import clip as _clip
from pico_utils import paged_print as _paged_print
from pico_utils import paged_lines as _paged_lines
from pico_utils import preview_print as _preview_print
from pico_utils import browse_items as _browse_items
from pico_utils import load_json, save_json, http_module as _http_module, check_wifi
from pico_utils import http_request as _http_request
from pico_utils import ticks_ms as _ticks_ms, ticks_diff as _ticks_diff
from pico_utils import screen_header as _screen_header, clear_screen as _clear_screen
from pico_utils import DISPLAY_WIDTH
from pico_utils import paint as _paint, GREY, BCYAN, BYELLOW

try:
    import ujson as json
except ImportError:
    import json


CONFIG_FILE = "openrouter_config.json"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
DEFAULT_SYSTEM_PROMPT = (
    "Reply concise, in plain text: no markdown, tables or emoji."
    " It is read on a small handheld screen."
)
MAX_SYSTEM_CHARS = 220
MAX_PROMPT_CHARS = 480
MAX_OUTPUT_CHARS = 1400
# per read: a non-streamed reply sends nothing until generation ends
AI_TIMEOUT = 60
MODULE_VERSION = "2026-10-04.3"
MAX_HISTORY_MESSAGES = 6
_HISTORY = []
_MEMORY_ENABLED = True
MAX_VIEW_RESPONSES = 5
VIEW_PREVIEW_CHARS = 180
_LAST_RESPONSES = []
PRESET_PROMPTS = {
    "brief": "Reply very concise. Max 4 short bullet points. Plain text.",
    "teacher": "Explain step by step in simple language. Keep each line short.",
    "code": "Focus on practical code help. Use short snippets and concise notes.",
}


def _resolve_response(index):
    try:
        pos = int(index) - 1
    except Exception:
        print("Invalid index.")
        return None, None
    if pos < 0 or pos >= len(_LAST_RESPONSES):
        print("Out of range.")
        return None, None
    return _LAST_RESPONSES[pos], pos


def _render_response_summary(item, pos, total):
    print("[{}/{}] {}".format(pos + 1, total, _clip(item.get("model", "?"), 24)))
    print("ms:", item.get("elapsed_ms", 0))
    print("Q:")
    _preview_print(_clip(item.get("prompt", ""), MAX_PROMPT_CHARS), max_lines=3)
    print("---")
    print("A:")
    _preview_print(_clip(item.get("answer", ""), VIEW_PREVIEW_CHARS), max_lines=4)


def _render_response_detail(item, pos, total):
    _screen_header("AI Detail")
    print("[{}/{}] {}".format(pos + 1, total, _clip(item.get("model", "?"), 24)))
    print("ms:", item.get("elapsed_ms", 0))
    print("Q:")
    _paged_print(item.get("prompt", ""))
    print("---")
    print("A:")
    _paged_print(item.get("answer", ""))


def _load_config():
    data = load_json(CONFIG_FILE)
    if isinstance(data, dict):
        return data
    return {}


def _save_config(config):
    print("Saving config...")
    result = save_json(CONFIG_FILE, config)
    if result:
        print("Saved.")
    else:
        print("Save failed.")
    return result


def _set_config_value(key, value, empty_message, saved_message, show_value=False):
    text = str(value).strip()
    if text == "":
        print(empty_message)
        return False
    config = _load_config()
    config[key] = text
    if not _save_config(config):
        return False
    if show_value:
        print(saved_message, text)
    else:
        print(saved_message)
    return True


def set_api_key(api_key):
    value = str(api_key).strip()
    if value == "":
        print("Empty key.")
        return False

    config = _load_config()
    config["api_key"] = value
    if "model" not in config:
        config["model"] = DEFAULT_MODEL
    if not _save_config(config):
        return False
    print("API key saved.")
    return True


def set_model(model):
    return _set_config_value("model", model, "Empty model.", "Model:", show_value=True)


def set_system_prompt(text):
    if len(str(text).strip()) > MAX_SYSTEM_CHARS:
        print("Note: only the first {} chars are sent.".format(MAX_SYSTEM_CHARS))
    return _set_config_value("system_prompt", text, "Empty prompt.", "System prompt saved.")


def set_endpoint(url=None):
    """OpenAI-compatible chat URL, e.g. a local llama-server or LM Studio:
    set_endpoint('http://192.168.1.20:8080/v1/chat/completions').
    No argument: back to OpenRouter."""
    config = _load_config()
    value = str(url).strip() if url else ""
    if value and not (value.startswith("http://") or value.startswith("https://")):
        print("Invalid URL.")
        return False
    if value:
        config["endpoint"] = value
    elif "endpoint" in config:
        del config["endpoint"]
    if not _save_config(config):
        return False
    print("Endpoint:", _clip(value or OPENROUTER_URL, 44))
    return True


def set_stream(on=True):
    """Stream replies as they are generated (default on)."""
    config = _load_config()
    config["stream"] = bool(on)
    if not _save_config(config):
        return False
    print("Stream:", "on" if on else "off")
    return True


def clear_system_prompt():
    config = _load_config()
    if "system_prompt" in config:
        del config["system_prompt"]
        if not _save_config(config):
            return False
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
    endpoint = config.get("endpoint") or OPENROUTER_URL
    stream = config.get("stream", True)
    print("Model:", model)
    print("Key:", has_key)
    print("System:", has_system)
    print("Endpoint:", _clip(endpoint, 44))
    print("Stream:", stream)
    return {
        "model": model,
        "has_key": has_key,
        "has_system": has_system,
        "endpoint": endpoint,
        "stream": stream,
    }


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
    while len(_HISTORY) > MAX_HISTORY_MESSAGES:
        del _HISTORY[0]


def _response_append(prompt_text, answer_text, model_name, elapsed_ms):
    _LAST_RESPONSES.append(
        {
            "prompt": _clip(prompt_text, MAX_PROMPT_CHARS),
            "answer": _clip(answer_text, MAX_OUTPUT_CHARS),
            "model": str(model_name),
            "elapsed_ms": int(elapsed_ms),
        }
    )
    while len(_LAST_RESPONSES) > MAX_VIEW_RESPONSES:
        del _LAST_RESPONSES[0]


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


def responses():
    if not _LAST_RESPONSES:
        print("No cached responses.")
        return []

    print("Responses:")
    total = len(_LAST_RESPONSES)
    lines = []
    for index, item in enumerate(_LAST_RESPONSES, start=1):
        lines.append("{}: {} ({}ms)".format(index, _clip(item.get("model", "?"), 24), item.get("elapsed_ms", 0)))
        lines.append("   Q: {}".format(_clip(item.get("prompt", ""), 44)))
        lines.append("   A: {}".format(_clip(item.get("answer", ""), 44)))
    lines.append("Cached: {} / {}".format(total, MAX_VIEW_RESPONSES))
    _paged_lines(lines)
    return _LAST_RESPONSES


def resp_clear():
    _LAST_RESPONSES[:] = []
    gc.collect()
    print("Responses cleared.")
    return True


def view(index=1):
    if not _LAST_RESPONSES:
        print("No cached responses. Run ask()/chat().")
        return None
    return _browse_items(
        "AI Responses",
        _LAST_RESPONSES,
        index,
        _render_response_summary,
        _render_response_detail,
    )


def _iter_lines(raw):
    # Lines from a response body read in small chunks (no whole-body buffer).
    buf = bytearray(256)
    pending = b""
    while True:
        n = raw.readinto(buf)
        if not n:
            break
        pending += bytes(buf[:n])
        start = 0
        while True:
            nl = pending.find(b"\n", start)
            if nl < 0:
                break
            yield pending[start:nl]
            start = nl + 1
        pending = pending[start:]
    if pending:
        yield pending


def _delta_text(event):
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    delta = first.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("content"), str):
        return delta["content"]
    return None


def _error_text(err):
    if isinstance(err, dict):
        err = err.get("message", str(err))
    return _clip(err, 80)


class _StreamPrinter:
    # Prints streamed text with word wrap: a word is held until it ends.
    def __init__(self, width=DISPLAY_WIDTH, out=None):
        self.width = width
        self.out = out or (lambda text: print(text, end=""))
        self.col = 0
        self.word = ""
        self.space = False

    def __call__(self, text):
        for ch in text:
            if ch == "\n":
                self._flush()
                self.out("\n")
                self.col = 0
                self.space = False
            elif ch == " ":
                self._flush()
                self.space = self.col > 0
            else:
                self.word += ch
                if len(self.word) >= self.width:
                    self._flush()

    def _flush(self):
        word = self.word
        if not word:
            return
        self.word = ""
        gap = 1 if self.space else 0
        if self.col + gap + len(word) > self.width:
            self.out("\n")
            self.col = 0
        elif gap:
            self.out(" ")
            self.col += 1
        self.out(word)
        self.col += len(word)
        self.space = False

    def close(self):
        self._flush()
        if self.col:
            self.out("\n")
            self.col = 0


def _sse_text(raw, emit):
    # OpenAI-style server-sent events -> reply text, emitted as it arrives.
    parts = []
    size = 0
    try:
        for line in _iter_lines(raw):
            line = line.strip()
            if not line.startswith(b"data:"):
                continue  # blank separators and ": keep-alive" comments
            data = line[5:].strip()
            if data == b"[DONE]":
                break
            try:
                event = json.loads(data)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if "error" in event:
                emit("\n")
                print("Err:", _error_text(event["error"]))
                break
            delta = _delta_text(event)
            if delta:
                emit(delta)
                parts.append(delta)
                size += len(delta)
                if size >= MAX_OUTPUT_CHARS:
                    break
    except (OSError, ValueError) as error:
        emit("\n")
        print("(stream cut:", _clip(error, 24), ")")
    return "".join(parts)


def ask(prompt, model=None, max_tokens=220, temperature=0.2, use_memory=None, raw=False):
    requests = _http_module()
    if requests is None:
        return None

    if not check_wifi():
        return None

    config = _load_config()
    api_key = config.get("api_key", "")
    endpoint = config.get("endpoint") or OPENROUTER_URL
    stream = bool(config.get("stream", True))
    selected_model = model or config.get("model", DEFAULT_MODEL)
    system_prompt = config.get("system_prompt", DEFAULT_SYSTEM_PROMPT)

    if api_key == "" and "openrouter.ai" in endpoint:
        print("No API key. Use set_api_key(...)")
        return None

    prompt_text = _clip(str(prompt).strip(), MAX_PROMPT_CHARS)
    if prompt_text == "":
        print("Empty prompt.")
        return None

    use_mem = _MEMORY_ENABLED if use_memory is None else bool(use_memory)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": _clip(system_prompt, MAX_SYSTEM_CHARS)})
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
    if stream:
        payload["stream"] = True

    headers = {
        "Content-Type": "application/json",
        "HTTP-Referer": "https://picocalc.local",
        "X-Title": "PicoCalc",
    }
    if api_key:
        headers["Authorization"] = "Bearer " + api_key

    print(_paint(selected_model.split("/")[-1] + " ...", GREY))
    start_ms = _ticks_ms()

    response = None
    text = None
    try:
        response = _http_request(
            requests,
            "POST",
            endpoint,
            timeout=AI_TIMEOUT,
            headers=headers,
            data=json.dumps(payload),
        )
        status = response.status_code
        if status != 200:
            print("HTTP:", status)
            try:
                body = response.json()
            except Exception:
                body = None
            if isinstance(body, dict) and "error" in body:
                print("Err:", _error_text(body["error"]))
            else:
                print("Bad response")
            return None
        if stream:
            printer = _StreamPrinter()
            text = _sse_text(response.raw, printer)
            printer.close()
        else:
            try:
                body = response.json()
            except Exception:
                body = None
            if body is None:
                print("Invalid JSON response")
                return None
            text = _extract_text(body)
            del body
            if text is not None:
                text = _clip(text, MAX_OUTPUT_CHARS)
                _paged_print(text)
    except Exception as error:
        print("Request fail:", error)
        return None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    if not text:
        print("No text in response")
        return None

    elapsed_ms = _ticks_diff(_ticks_ms(), start_ms)
    print(_paint("{} ms".format(elapsed_ms), GREY))

    _response_append(prompt_text, text, selected_model, elapsed_ms)

    if use_mem:
        _history_append("user", prompt_text)
        _history_append("assistant", text)

    gc.collect()
    return text


def chat(with_view=False):
    _screen_header("AI chat")
    print(_paint("Type a question, Enter sends. Empty line or q exits.", GREY))
    print(_paint("A sent question can't be cancelled.", GREY))
    print("")
    try:
        while True:
            try:
                prompt = input(_paint("> ", BCYAN)).strip()
            except Exception:
                prompt = ""

            if prompt == "" or prompt in ("q", "quit", "exit"):
                break

            ask(prompt)
            if with_view and _LAST_RESPONSES:
                view(len(_LAST_RESPONSES))
    except KeyboardInterrupt:
        pass
    finally:
        _clear_screen()


def chat_view():
    return chat(with_view=True)


def ver():
    print("openrouter_ai:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- OpenRouter AI --")
    print("ask(prompt)   Send a question")
    print("chat()        Interactive chat")
    print("chat_view()   Chat + viewer")
    print("view(#)/v(#)  Browse responses")
    print("responses()   List cached")
    print("resp_clear()  Clear cache")
    print("set_api_key() Set API key")
    print("set_model()   Change AI model")
    print("set_system_prompt() Set prompt")
    print("clear_system_prompt() Reset")
    print("presets()     List presets")
    print("preset(name)  Apply preset")
    print("mem_on/off()  Toggle memory")
    print("mem_status()  Show memory info")
    print("mem_clear()   Clear memory")
    print("show_config() Show settings")
    print("set_endpoint(url) Local server")
    print("set_stream(b) Stream replies")
    print("tip: import openrouter_ai as ai")


def h():
    return help()


def v(index=1):
    return view(index)
