try:
    from harness import *  # noqa
except ImportError:  # on the device tools/test_device.sh puts harness.py in front
    pass

# Host checks for files, apps and scientific_calc.
# Run from the repo root with either interpreter:
#   python3 tests/test_tools.py
#   micropython tests/test_tools.py


# ── scientific_calc ─────────────────────────────

import scientific_calc as sc


def test_format_result():
    check("small int", sc._format_result(42), "42")
    check("integral float", sc._format_result(3.0), "3")
    check("2**100 fits 30", sc._format_result(2 ** 100, 30), "1.2676506002282294014967032e30")
    check("30! no cut digits", sc._format_result(sc._calc_factorial(30), 10), "2.65253e32")
    check("negative big", sc._format_result(-(10 ** 40), 12), "-1.000000e40")
    check("round up carries", sc._format_result(99999999999, 8), "1.000e11")
    check("inf", sc._format_result(float("inf")), "inf")
    check("-inf", sc._format_result(float("-inf")), "-inf")
    check("nan", sc._format_result(float("nan")), "nan")


def test_format_result_float32():
    # rp2 floats are float32: the values the device produces, shown as there.
    saved = sc._SINGLE
    try:
        sc._SINGLE = True
        check("1e11", sc._format_result(99999997952.0), "1e+11")
        check("100000000/3", sc._format_result(33333334.0), "3.333333e+07")
        check("exp(20)", sc._format_result(485165184.0), "4.851652e+08")
        check("integral below 1e7", sc._format_result(9999999.0), "9999999")
        check("1/3", sc._format_result(0.3333333432674408), "0.3333333")
        check("small negative", sc._format_result(-1.2345678e-05), "-1.234568e-05")
        check("history column: fewer digits", sc._format_result(33333334.0, 10), "3.3333e+07")
        check("ints stay exact", sc._format_result(2 ** 40), "1099511627776")
    finally:
        sc._SINGLE = saved


def test_calc_huge_ints():
    # str() of an int is quadratic (2**100000 took seconds on the device):
    # from 10**1000 on none is printed, nor kept in the history.
    saved = (sc._LAST, list(sc._HISTORY))
    try:
        big = 10 ** 1000
        check("too large", sc._format_result(big), "too large to show")
        check("negative too", sc._format_result(-big), "too large to show")
        check("170! still shown", sc._format_result(sc._calc_factorial(170), 12), "7.257416e306")
        check("2^1000 still shown", sc._format_result(2 ** 1000, 12), "1.071509e301")
        del sc._HISTORY[:]
        sc._store("10**1000", big)
        check("ans keeps it", sc._LAST == big, True)
        check("the history doesn't", sc._HISTORY, [])
    finally:
        sc._LAST = saved[0]
        sc._HISTORY[:] = saved[1]


def test_calc_degrees():
    # Degrees: zeros at multiples of 90 come out as zeros (float32's pi made
    # sin(180) -8.7e-08) and tan(90) is an error, not -22877332.
    import math

    saved = (sc.DEG_MODE, sc._LAST, list(sc._HISTORY))
    try:
        sc.DEG_MODE = True
        ns = sc._calc_namespace()  # what the prompt evaluates with
        check("sin(180)", ns["sin"](180), 0)
        check("cos(90)", ns["cos"](90), 0)
        check("sin(540)", ns["sin"](540), 0)
        check("tan(180)", ns["tan"](180), 0)
        check("sin(-90)", abs(ns["sin"](-90) + 1) < 1e-6, True)
        check("reduced before converting", abs(ns["sin"](360 * 10 ** 20 + 30) - 0.5) < 1e-6, True)
        check("tan(45)", abs(ns["tan"](45) - 1) < 1e-6, True)
        undefined = []
        for angle in (90, -90, 270, 450.0):
            try:
                ns["tan"](angle)
            except ValueError:
                undefined.append(angle)
        check("tan at odd multiples of 90", undefined, [90, -90, 270, 450.0])
        check("sin() at the REPL too", sc.sin(180), 0)
        sc.DEG_MODE = False
        check("radians untouched", ns["sin"](math.pi) != 0, True)
    finally:
        sc.DEG_MODE, sc._LAST = saved[:2]
        sc._HISTORY[:] = saved[2]


def test_calc_ans_chaining():
    saved = sc._LAST
    try:
        sc._LAST = 10
        check("+ chains", sc._prepare_expr("+5"), "ans+5")
        check("* chains", sc._prepare_expr("*2"), "ans*2")
        check("- stays negative", sc._prepare_expr("-5"), "-5")
        check("^2 chains as a power", sc._prepare_expr("^2"), "ans**2")
        sc._LAST = None
        check("no ans yet", sc._prepare_expr("+5"), "+5")
        check("^ is a power, not XOR", eval(sc._prepare_expr("2^10")), 1024)
        check("10^3", eval(sc._prepare_expr("10^3")), 1000)
    finally:
        sc._LAST = saved


def test_calc_prompt():
    # What the calc's help lists works at its prompt: deg, rad, history...
    # as commands; ln, store() and recall() in expressions.
    saved = (sc.safe_input, sc._calc_help, sc.clip, sc._LAST, list(sc._HISTORY), dict(sc._VARS), sc.DEG_MODE)
    script = ["2^10", "^2", "deg", "sin(90)", 'store("r", 7)', "r*2", "ln(1)", 'recall("r")']
    script += ["nope + 1", "history", "last", "variables()", "h", "rad()", "q"]
    helps = []
    limits = []
    real_clip = sc.clip
    try:
        sc._LAST = None
        del sc._HISTORY[:]
        sc._VARS.clear()
        sc.safe_input = lambda prompt: script.pop(0)
        sc._calc_help = lambda: helps.append(1)
        sc.clip = lambda text, limit: limits.append(limit) or real_clip(text, limit)
        sc.calc()
        check("results kept", [item["result"] for item in sc._HISTORY], [1024, 1048576, 1, 14, 0])
        check("store() at the prompt", sc._VARS, {"r": 7})
        check("the calc's own help", helps, [1])
        check("deg and rad() switch", sc.DEG_MODE, False)
        check("errors clipped to the screen", sc.DISPLAY_WIDTH - 5 in limits, True)
        check("every line read", script, [])
    finally:
        sc.safe_input, sc._calc_help, sc.clip, sc._LAST = saved[:4]
        sc._HISTORY[:] = saved[4]
        sc._VARS.clear()
        sc._VARS.update(saved[5])
        sc.DEG_MODE = saved[6]


# apps from the SD card

import gfx  # the console run_app() puts back, in test_app_puts_back_cwd_and_console


def test_app_header():
    import apps

    check("two fields", apps.parse_header("# picocalc-app: Tetris | Blocks"), ("Tetris", "", "Blocks"))
    check("three fields", apps.parse_header("# picocalc-app: Tetris | Games | Blocks "), ("Tetris", "Games", "Blocks"))
    check("name only", apps.parse_header("# picocalc-app:Hello"), ("Hello", "", ""))
    check("no name", apps.parse_header("# picocalc-app:  | x"), None)
    check("plain comment", apps.parse_header("# hello"), None)


def test_app_discover_and_run():
    import apps

    folder = HERE + "/_tmp_apps"
    rm_tree(folder)
    os.mkdir(folder)
    try:
        write_file(folder + "/zeta.py", "# picocalc-app: Alpha | Games | fun\nimport pico_utils\npico_utils._APP_RAN = __name__\n")
        write_file(folder + "/beta.py", "import _tmp_helper\n_tmp_helper.go()\n")
        write_file(folder + "/_tmp_helper.py", "import pico_utils\ndef go():\n    pico_utils._APP_RAN = 'helper'\n")
        write_file(folder + "/._beta.py", "junk")
        write_file(folder + "/notes.txt", "x")
        write_file(folder + "/boom.py", "1/0\n")
        write_file(folder + "/leave.py", "import sys\nsys.exit()\n")
        found = apps.discover((folder,))
        names = [app[0] for app in found]
        check("sorted, dot files and non-py skipped", names, ["_tmp_helper", "Alpha", "beta", "boom", "leave"])
        check("header fields", found[1][:3], ("Alpha", "Games", "fun"))
        keys_from(b" ")
        check("runs as main", apps.run_app(folder + "/zeta.py"), True)
        check("saw __main__", getattr(pu, "_APP_RAN", None), "__main__")
        keys_from(b" ")
        apps.run_app(folder + "/beta.py")
        check("sibling import", getattr(pu, "_APP_RAN", None), "helper")
        check("its modules dropped", "_tmp_helper" in sys.modules, False)
        check("path restored", folder in sys.path, False)
        keys_from(b" ")
        check("error comes back", apps.run_app(folder + "/boom.py"), False)
        keys_from(b" ")
        check("sys.exit comes back", apps.run_app(folder + "/leave.py"), True)
        check("missing dir", apps.discover((folder + "/nope",)), [])
    finally:
        pu._key_byte = REAL_KEY_BYTE
        if hasattr(pu, "_APP_RAN"):
            del pu._APP_RAN
        rm_tree(folder)


def test_app_puts_back_cwd_and_console():
    # Apps may chdir (the toolkit and its config files are found through the
    # cwd) or give the screen back to the terminal (gfx.end(), or
    # console_suspend() and an error): run_app restores both.
    import apps

    folder = HERE + "/_tmp_apps"
    rm_tree(folder)
    os.mkdir(folder)
    cwd = os.getcwd()
    saved_print = apps._print_error
    try:
        write_file(folder + "/away.py", "import os\nos.chdir(" + repr(folder) + ")\n")
        write_file(folder + "/ender.py", "import gfx\ngfx.end()\n")
        write_file(folder + "/lender.py", "import pico_utils\npico_utils.console_suspend()\n1/0\n")
        write_file(folder + "/plain.py", "x = 1\n")
        keys_from(b" ")
        apps.run_app(folder + "/away.py")
        check("cwd put back", os.getcwd(), cwd)
        os.chdir(cwd)  # HERE is relative on MicroPython: keep the rest independent

        def body(fake, vt):
            gfx.begin()
            gfx.app()
            keys_from(b" ")
            apps.run_app(folder + "/ender.py")
            check("console back after gfx.end()", (pu._CONSOLE[0] is gfx.CON, gfx._FB[0] is fake), (True, True))
            seen = []
            apps._print_error = lambda error: seen.append(pu._CONSOLE[0] is gfx.CON)
            keys_from(b" ")
            check("error after a suspend", apps.run_app(folder + "/lender.py"), False)
            check("error shown once the console is back", seen, [True])
            gfx.end()
            keys_from(b" ")
            apps.run_app(folder + "/plain.py")
            check("from the REPL: no console", (pu._CONSOLE[0], gfx._FB[0]), (None, None))

        with_display(body)
    finally:
        pu._key_byte = REAL_KEY_BYTE
        apps._print_error = saved_print
        os.chdir(cwd)
        rm_tree(folder)


# files


def test_files_paths_and_listing():
    import files

    check("join root", files.join("/", "a.py"), "/a.py")
    check("join dir", files.join("/sd/", "b"), "/sd/b")
    check("parent", files.parent("/sd/apps"), "/sd")
    check("parent of top", files.parent("/sd"), "/")
    check("parent of root", files.parent("/"), "/")
    folder = HERE + "/_tmp_files"
    rm_tree(folder)
    os.mkdir(folder)
    try:
        write_file(folder + "/b.txt", "12345")
        write_file(folder + "/A.txt", "")
        os.mkdir(folder + "/zdir")
        check("dirs first, then by name", files.entries(folder), [("zdir", True, 0), ("A.txt", False, 0), ("b.txt", False, 5)])
        keys_from(b"n")
        check("delete needs y", files.delete(folder + "/b.txt"), False)
        keys_from(b"y")
        check("delete file", files.delete(folder + "/b.txt"), True)
        keys_from(b"y")
        check("delete empty dir", files.delete(folder + "/zdir", True), True)
        check("left", [e[0] for e in files.entries(folder)], ["A.txt"])
    finally:
        pu._key_byte = REAL_KEY_BYTE
        rm_tree(folder)


def _ctrl_c(fn, *args):
    # fn(*args) with Ctrl+C typed: what it returns, or that it let it through.
    keys_from(b"\x03")
    try:
        return fn(*args)
    except KeyboardInterrupt:
        return "KeyboardInterrupt"


def test_files_prompts_and_delete():
    # Ctrl+C at a prompt cancels instead of leaving Files; a folder with
    # something in it is said so before any y/n.
    import files

    folder = HERE + "/_tmp_files"
    rm_tree(folder)
    os.mkdir(folder)
    saved = files.edit
    try:
        write_file(folder + "/a.txt", "x")
        check("Ctrl+C at y/n", _ctrl_c(files.delete, folder + "/a.txt"), False)
        check("still there", files._exists(folder + "/a.txt"), True)
        check("Ctrl+C at a name", _ctrl_c(files._ask_name, "Name, then Enter: "), None)
        files.edit = lambda path: True  # quit the editor without saving
        keys_from(b"new.txt\r")
        check("nothing saved, nothing to highlight", files._new_file(folder), None)
        files.edit = lambda path: write_file(path, "")
        keys_from(b"new.txt\r")
        check("saved: highlighted", files._new_file(folder), "new.txt")
        os.mkdir(folder + "/full")
        write_file(folder + "/full/f.txt", "x")
        keys_from(b"y")
        check("folder not empty", (files.delete(folder + "/full", True), files._exists(folder + "/full")), (False, True))
    finally:
        pu._key_byte = REAL_KEY_BYTE
        files.edit = saved
        rm_tree(folder)


def test_files_text():
    import files

    check("text", files.is_binary(b"def f():\n\treturn 1\n"), False)
    check("utf-8 is text", files.is_binary("città".encode()), False)
    check("nul is binary", files.is_binary(b"ab\x00cd"), True)
    check("control bytes", files.is_binary(bytes(range(1, 9)) * 4), True)
    check("empty", files.is_binary(b""), False)
    check("cut character dropped", files.decode("caffè".encode()[:-1]), "caff")
    check("bad byte", files.decode(b"a\xffb\xfe"), "a?b?")
    check("whole multi-byte kept", files.decode("è".encode()), "è")
    latin = b"caff\xe8 latte\n" * 200  # small: the suite also runs on the device
    check("latin-1 as ?", files.decode(latin), "caff? latte\n" * 200)
    check(
        "hard wrap keeps indent",
        files.text_lines("    abcdef\n\n\tx\x1by\n", width=6),
        ["    ab", "cdef", "", "    x?", "y"],
    )
    check("crlf", files.text_lines("a\r\nb"), ["a", "b"])


class ReadSpy:
    # The file files.view() opens, its reads recorded.
    def __init__(self, f, sizes):
        self.f = f
        self.sizes = sizes

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.f.close()

    def read(self, n=-1):
        self.sizes.append(n)
        return self.f.read(n)

    def seek(self, pos):
        return self.f.seek(pos)


def test_files_view():
    import files

    folder = HERE + "/_tmp_files"
    rm_tree(folder)
    os.mkdir(folder)
    shown = []
    reads = []
    saved = (files.pager, files.decode)
    try:
        files.pager = lambda title, lines: shown.append(lines)
        files.open = lambda path, mode="r": ReadSpy(open(path, mode), reads)
        text = "".join("line {}\n".format(i) for i in range(200))  # past the 512-byte sample
        write_file(folder + "/t.txt", text)
        check("text shown", files.view(folder + "/t.txt"), True)
        check("all of it, from the start", shown, [files.text_lines(text)])
        check("the sample, then the text", reads, [512, files.VIEW_MAX])
        with open(folder + "/b.bin", "wb") as f:
            f.write(b"\x00" * 600 + b"text")
        del reads[:]
        keys_from(b" ")
        check("binary not shown", (files.view(folder + "/b.bin"), len(shown)), (False, 1))
        check("binary: only the sample read", reads, [512])
        with open(folder + "/l.txt", "wb") as f:
            f.write(b"caff\xe8 latte\n" * 100)
        files.view(folder + "/l.txt")
        check("latin-1 shown", shown[-1][:2], ["caff? latte", "caff? latte"])

        def no_memory(data):
            raise MemoryError

        files.decode = no_memory
        keys_from(b" ")
        check("out of memory stays in Files", (files.view(folder + "/t.txt"), len(shown)), (False, 2))
    finally:
        pu._key_byte = REAL_KEY_BYTE
        files.pager, files.decode = saved
        if hasattr(files, "open"):
            del files.open
        rm_tree(folder)


run(globals())
