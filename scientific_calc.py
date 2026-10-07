import gc
import math

from pico_utils import clip, paged_print, paged_lines, safe_input, clear_screen, screen_header
from pico_utils import DISPLAY_WIDTH


MODULE_VERSION = "2026-10-07.1"
MAX_HISTORY = 20
MAX_EXPR_LEN = 160
MAX_VARS = 50
DEG_MODE = False

_HISTORY = []
_LAST = None
_VARS = {}
# rp2 floats are float32: ~7 significant digits, and every float from 2**24
# up is integral (1e11 is 99999997952.0 there).
_SINGLE = 16777217.0 == 16777216.0
# str() of an int takes time quadratic in its digits: 2**100000 (30,103 of
# them) kept the device busy for seconds, deaf to Ctrl+C; 1,000 digits cost
# ~1/900 of that (unix port). Ints from here on are not printed: 2^1000 and
# 170!, the largest factorial here (~300 digits), still are.
_HUGE = 10 ** 1000


def _store(expr, result):
    global _LAST
    _LAST = result
    if isinstance(result, int) and not -_HUGE < result < _HUGE:
        return  # ans has it; the history keeps no int that big
    _HISTORY.append({"expr": clip(str(expr), 60), "result": result})
    while len(_HISTORY) > MAX_HISTORY:
        del _HISTORY[0]


def _fit_int(value, width):
    # Too many digits: scientific notation, never silently cut digits.
    if not -_HUGE < value < _HUGE:
        return clip("too large to show", width)
    text = str(value)
    if len(text) <= width:
        return text
    sign = "-" if value < 0 else ""
    digits = text[len(sign):]
    exp = len(digits) - 1
    keep = max(1, width - len(sign) - len(str(exp)) - 2)
    head = int(digits[:keep])
    if digits[keep] >= "5":
        head += 1
        if len(str(head)) > keep:
            head //= 10
            exp += 1
    head = str(head)
    mantissa = head[0] + ("." + head[1:] if len(head) > 1 else "")
    return "{}{}e{}".format(sign, mantissa, exp)


def _fit_float(value, width):
    # The digits float32 holds, fewer if the column is narrow (history):
    # never a clipped exponent.
    for digits in range(7, 0, -1):
        text = ("{:." + str(digits) + "g}").format(value)
        if len(text) <= width:
            break
    return text


def _format_result(value, width=DISPLAY_WIDTH - 2):
    if isinstance(value, float):
        if math.isinf(value) or math.isnan(value):
            return str(value)
        # An int only where float32 is still exact to the unit: past that
        # its noise printed as digits (1e11 showed 99999997952, exp(20)
        # 485165184).
        if abs(value) < (1e7 if _SINGLE else 1e15) and value == int(value):
            value = int(value)
        elif _SINGLE:
            return _fit_float(value, width)
    if isinstance(value, int):
        return _fit_int(value, width)
    return clip(str(value), width)


def _prepare_expr(expr):
    # ^ is the power, as on calculators (Python's XOR made 2^10 show 8).
    # Like a desk calculator: "+5", "*2" or "^2" continues from the last
    # result. A leading "-" stays a negative number.
    expr = expr.replace("^", "**")
    if _LAST is not None and expr[:1] in ("+", "*", "/", "%"):
        return "ans" + expr
    return expr


def _print_result(expr, result):
    print("=", _format_result(result))
    _store(expr, result)


def _to_rad(x):
    if DEG_MODE:
        return x * math.pi / 180.0
    return x


def _from_rad(x):
    if DEG_MODE:
        return x * 180.0 / math.pi
    return x


def _calc_log(x, base=None):
    if base is None:
        return math.log(x)
    return math.log(x) / math.log(base)


def _calc_factorial(n):
    try:
        val = int(n)
    except Exception as error:
        raise ValueError("Integer required.") from error
    if val < 0:
        raise ValueError("Non-negative required.")
    if val > 170:
        raise ValueError("Too large (max 170).")
    result = 1
    for i in range(2, val + 1):
        result *= i
    return result


def _calc_namespace():
    namespace = {
        "sin": lambda x: math.sin(_to_rad(x)),
        "cos": lambda x: math.cos(_to_rad(x)),
        "tan": lambda x: math.tan(_to_rad(x)),
        "asin": lambda x: _from_rad(math.asin(x)),
        "acos": lambda x: _from_rad(math.acos(x)),
        "atan": lambda x: _from_rad(math.atan(x)),
        "sqrt": math.sqrt,
        "ln": math.log,
        "log": _calc_log,
        "log10": math.log10,
        "log2": lambda x: _calc_log(x, 2),
        "exp": math.exp,
        "pow": math.pow,
        "power": math.pow,
        "abs": abs,
        "abs_val": abs,
        "pi": math.pi,
        "e": math.e,
        "ceil": math.ceil,
        "floor": math.floor,
        "factorial": _calc_factorial,
        "hypot": lambda x, y: math.sqrt(x * x + y * y),
        "d2r": lambda degrees: degrees * math.pi / 180.0,
        "r2d": lambda radians: radians * 180.0 / math.pi,
        "c2f": lambda celsius: celsius * 9.0 / 5.0 + 32,
        "f2c": lambda fahrenheit: (fahrenheit - 32) * 5.0 / 9.0,
        "km2mi": lambda km: km * 0.621371,
        "mi2km": lambda mi: mi / 0.621371,
        "store": _calc_store,
        "recall": _calc_recall,
    }
    for key, value in _VARS.items():
        namespace[key] = value
    if _LAST is not None:
        namespace["ans"] = _LAST
    return namespace


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
    result = _calc_log(x, base)
    if base is not None:
        _print_result("log({},{})".format(x, base), result)
    else:
        _print_result("ln({})".format(x), result)
    return result


def log10(x):
    result = math.log10(x)
    _print_result("log10({})".format(x), result)
    return result


def log2(x):
    result = _calc_log(x, 2)
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
        result = _calc_factorial(n)
    except ValueError as error:
        print(error)
        return None
    _print_result("{}!".format(int(n)), result)
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
    if len(_VARS) >= MAX_VARS and key not in _VARS:
        print("Var limit ({}).".format(MAX_VARS))
        return False
    _VARS[key] = value
    print("{}={}".format(key, _format_result(value)))
    return True


def recall(name):
    key = str(name).strip()
    if key not in _VARS:
        print("Not found:", key)
        return None
    val = _VARS[key]
    print("{}={}".format(key, _format_result(val)))
    return val


def variables():
    if not _VARS:
        print("No stored variables.")
        return {}
    lines = []
    for k, v in _VARS.items():
        lines.append("{}={}".format(k, _format_result(v)))
    paged_lines(lines)
    return _VARS


def clear_vars():
    _VARS.clear()
    print("Variables cleared.")
    return True


def last():
    if _LAST is None:
        print("No last result.")
        return None
    print("Last:", _format_result(_LAST))
    return _LAST


def history():
    if not _HISTORY:
        print("No history.")
        return []
    print("History ({}):".format(len(_HISTORY)))
    lines = []
    for i, item in enumerate(_HISTORY, 1):
        expr = clip(item["expr"], 18)
        res = _format_result(item["result"], 10)
        lines.append("{}: {} = {}".format(i, expr, res))
    paged_lines(lines)
    return _HISTORY


def clear_history():
    global _LAST
    _HISTORY[:] = []
    _LAST = None
    gc.collect()
    print("History cleared.")
    return True


def _calc_store(name, value=None):
    store(name, value)  # prints name=value: nothing left to show or keep


def _calc_recall(name):
    recall(name)


# Typed alone at the prompt, "()" optional. They print their own answer;
# through eval, history() would have landed in its own list.
_COMMANDS = {
    "deg": deg,
    "rad": rad,
    "mode": mode,
    "history": history,
    "last": last,
    "variables": variables,
}


def _calc_help():
    # What works at the prompt; help() lists the REPL functions.
    print("-- Calculator ({}) --".format("degrees" if DEG_MODE else "radians"))
    print("2^10 or 2**10  power      7%3  remainder")
    print("+5 *2 ^2       continue from ans (last result)")
    print("sin cos tan asin acos atan   deg / rad: mode")
    print("sqrt exp ln log10 log2 log(x,base) abs")
    print("ceil floor factorial(n) hypot(x,y)  pi e")
    print("d2r r2d c2f f2c km2mi mi2km  conversions")
    print('store("r") keeps ans as r: then r*2')
    print('recall("r")  variables  history  last')
    print("q or empty line  exit")


def calc():
    screen_header("Calculator")
    print("expr h=help q/empty=exit")
    print("+5 or *2 continues from ans")
    print("Mode:", "deg" if DEG_MODE else "rad")
    print("")
    try:
        while True:
            try:
                expr = safe_input("> ").strip()
            except Exception:
                expr = ""

            if expr == "":
                break

            if expr in ("q", "quit", "exit"):
                break

            if expr in ("h", "help"):
                _calc_help()
                continue

            command = _COMMANDS.get(expr[:-2] if expr.endswith("()") else expr)
            if command is not None:
                command()
                continue

            if len(expr) > MAX_EXPR_LEN:
                print("Too long (max {}).".format(MAX_EXPR_LEN))
                continue

            try:
                expr = _prepare_expr(expr)
                namespace = _calc_namespace()
                result = eval(expr, {"__builtins__": {}}, namespace)
                if result is not None:  # store() and recall() print their own
                    _print_result(expr, result)
            except MemoryError:
                print("Too complex.")
                gc.collect()
            except Exception as err:
                print("Err:", clip(str(err), DISPLAY_WIDTH - 5))
    except KeyboardInterrupt:
        pass
    finally:
        clear_screen()


def ver():
    print("scientific_calc:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- Scientific Calculator --")
    print("calc()        Interactive mode")
    print("sin cos tan   Trig functions")
    print("asin acos atan  Inverse trig")
    print("sqrt log exp  Math functions")
    print("log10 log2    Logarithms")
    print("power(b,e)    Exponentiation")
    print("factorial(n)  Factorial")
    print("abs_val ceil floor  Rounding")
    print("hypot(x,y)    Hypotenuse")
    print("pi() e()      Constants")
    print("deg() rad()   Set angle mode")
    print("mode()        Show angle mode")
    print("d2r() r2d()   Deg/rad convert")
    print("c2f() f2c()   Temp convert")
    print("km2mi() mi2km()  Dist convert")
    print("store(n,v)    Save variable")
    print("recall(n)     Load variable")
    print("variables()   List variables")
    print("history()     Show history")
    print("last()        Last result")
    print("tip: import scientific_calc as sc")


def h():
    return help()
