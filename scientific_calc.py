import gc
import math

from pico_utils import clip, paged_print, safe_input


MODULE_VERSION = "2026-03-22.1"
DISPLAY_WIDTH = 32
MAX_HISTORY = 20
DEG_MODE = False

_HISTORY = []
_LAST = None
_VARS = {}


def _store(expr, result):
    global _LAST
    _LAST = result
    _HISTORY.append({"expr": clip(str(expr), 60), "result": result})
    while len(_HISTORY) > MAX_HISTORY:
        del _HISTORY[0]


def _print_result(expr, result):
    if isinstance(result, float) and result == int(result) and abs(result) < 1e15:
        display = str(int(result))
    else:
        display = str(result)
    print("=", clip(display, DISPLAY_WIDTH - 2))
    _store(expr, result)


def _to_rad(x):
    if DEG_MODE:
        return x * math.pi / 180.0
    return x


def _from_rad(x):
    if DEG_MODE:
        return x * 180.0 / math.pi
    return x


def deg():
    global DEG_MODE
    DEG_MODE = True
    print("Angle mode: degrees")
    return True


def rad():
    global DEG_MODE
    DEG_MODE = False
    print("Angle mode: radians")
    return True


def mode():
    m = "degrees" if DEG_MODE else "radians"
    print("Angle mode:", m)
    return m


def sin(x):
    result = math.sin(_to_rad(x))
    _print_result("sin({})".format(x), result)
    return result


def cos(x):
    result = math.cos(_to_rad(x))
    _print_result("cos({})".format(x), result)
    return result


def tan(x):
    result = math.tan(_to_rad(x))
    _print_result("tan({})".format(x), result)
    return result


def asin(x):
    result = _from_rad(math.asin(x))
    _print_result("asin({})".format(x), result)
    return result


def acos(x):
    result = _from_rad(math.acos(x))
    _print_result("acos({})".format(x), result)
    return result


def atan(x):
    result = _from_rad(math.atan(x))
    _print_result("atan({})".format(x), result)
    return result


def sqrt(x):
    result = math.sqrt(x)
    _print_result("sqrt({})".format(x), result)
    return result


def log(x, base=None):
    if base is not None:
        result = math.log(x) / math.log(base)
        _print_result("log({},{})".format(x, base), result)
    else:
        result = math.log(x)
        _print_result("ln({})".format(x), result)
    return result


def log10(x):
    result = math.log10(x)
    _print_result("log10({})".format(x), result)
    return result


def log2(x):
    result = math.log(x) / math.log(2)
    _print_result("log2({})".format(x), result)
    return result


def exp(x):
    result = math.exp(x)
    _print_result("exp({})".format(x), result)
    return result


def power(base, exponent):
    result = math.pow(base, exponent)
    _print_result("{}^{}".format(base, exponent), result)
    return result


def factorial(n):
    try:
        val = int(n)
    except Exception:
        print("Integer required.")
        return None
    if val < 0:
        print("Non-negative required.")
        return None
    if val > 170:
        print("Too large (max 170).")
        return None
    result = 1
    for i in range(2, val + 1):
        result *= i
    _print_result("{}!".format(val), result)
    return result


def abs_val(x):
    result = abs(x)
    _print_result("abs({})".format(x), result)
    return result


def ceil(x):
    result = math.ceil(x)
    _print_result("ceil({})".format(x), result)
    return result


def floor(x):
    result = math.floor(x)
    _print_result("floor({})".format(x), result)
    return result


def pi():
    print("pi =", math.pi)
    return math.pi


def e():
    print("e =", math.e)
    return math.e


def hypot(x, y):
    result = math.sqrt(x * x + y * y)
    _print_result("hypot({},{})".format(x, y), result)
    return result


def d2r(degrees):
    result = degrees * math.pi / 180.0
    _print_result("d2r({})".format(degrees), result)
    return result


def r2d(radians):
    result = radians * 180.0 / math.pi
    _print_result("r2d({})".format(radians), result)
    return result


def c2f(celsius):
    result = celsius * 9.0 / 5.0 + 32
    _print_result("{}C->F".format(celsius), result)
    return result


def f2c(fahrenheit):
    result = (fahrenheit - 32) * 5.0 / 9.0
    _print_result("{}F->C".format(fahrenheit), result)
    return result


def km2mi(km):
    result = km * 0.621371
    _print_result("{}km->mi".format(km), result)
    return result


def mi2km(mi):
    result = mi / 0.621371
    _print_result("{}mi->km".format(mi), result)
    return result


def store(name, value=None):
    if value is None:
        if _LAST is None:
            print("No last result.")
            return False
        value = _LAST
    key = str(name).strip()
    if key == "":
        print("Empty name.")
        return False
    _VARS[key] = value
    print("{}={}".format(key, value))
    return True


def recall(name):
    key = str(name).strip()
    if key not in _VARS:
        print("Not found:", key)
        return None
    val = _VARS[key]
    print("{}={}".format(key, val))
    return val


def variables():
    if not _VARS:
        print("No stored variables.")
        return {}
    for k, v in _VARS.items():
        print("{}={}".format(k, v))
    return _VARS


def clear_vars():
    _VARS.clear()
    print("Variables cleared.")
    return True


def last():
    if _LAST is None:
        print("No last result.")
        return None
    print("Last:", _LAST)
    return _LAST


def history():
    if not _HISTORY:
        print("No history.")
        return []
    print("History ({}):".format(len(_HISTORY)))
    for i, item in enumerate(_HISTORY, 1):
        expr = clip(item["expr"], 18)
        res = clip(str(item["result"]), 10)
        print("{}: {} = {}".format(i, expr, res))
    return _HISTORY


def clear_history():
    global _LAST
    _HISTORY[:] = []
    _LAST = None
    gc.collect()
    print("History cleared.")
    return True


def calc():
    print("Calc. Empty=exit")
    while True:
        try:
            expr = safe_input("> ").strip()
        except Exception:
            expr = ""

        if expr == "":
            print("Bye")
            return

        if expr in ("q", "quit", "exit"):
            print("Bye")
            return

        if expr == "h":
            help()
            continue

        try:
            namespace = {
                "sin": math.sin, "cos": math.cos, "tan": math.tan,
                "asin": math.asin, "acos": math.acos, "atan": math.atan,
                "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
                "exp": math.exp, "pow": math.pow, "abs": abs,
                "pi": math.pi, "e": math.e,
                "ceil": math.ceil, "floor": math.floor,
            }
            for k, v in _VARS.items():
                namespace[k] = v
            if _LAST is not None:
                namespace["ans"] = _LAST
            result = eval(expr, {"__builtins__": {}}, namespace)
            _print_result(expr, result)
        except Exception as err:
            print("Err:", clip(str(err), 26))


def ver():
    print("scientific_calc:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("cmd: sin cos tan asin acos atan")
    print("cmd: sqrt log log10 log2 exp")
    print("cmd: power factorial abs_val")
    print("cmd: ceil floor hypot pi e")
    print("cmd: deg rad mode d2r r2d")
    print("cmd: c2f f2c km2mi mi2km")
    print("cmd: store recall variables")
    print("cmd: clear_vars last history")
    print("cmd: clear_history calc")
    print("cmd: ver help h")
    print("tip: import scientific_calc as sc")


def h():
    return help()
