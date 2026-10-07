# Run every tests/test_*.py, each in its own process (as on the device), with
# the interpreter that runs this script:
#   python3 tests/run_all.py
#   micropython tests/run_all.py
# Prints each file's result line and a total; the exit status is 1 if a file
# failed or died. The full output of such a file is printed before its line.

import os
import sys

try:
    import subprocess
except ImportError:  # MicroPython's unix port has none: os.system, the output through a file
    subprocess = None

HERE = __file__.rsplit("/", 1)[0] if "/" in __file__ else "."
EXE = sys.executable or "micropython"  # sys.executable is empty on MicroPython's unix port


def quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def run_file(path):
    # (exit status, output) of one test file
    if subprocess:
        done = subprocess.run([EXE, path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return done.returncode, done.stdout.decode("utf-8", "replace")
    out = HERE + "/_tmp_run_all.txt"
    status = os.system("{} {} > {} 2>&1".format(quote(EXE), quote(path), quote(out)))
    with open(out) as f:
        text = f.read()
    os.remove(out)
    return status, text


def summary(text):
    # (tests, failed) from the "N tests, M failed" line that ends a run, or None.
    # An app's prompt with no newline can sit in front of it on the same line.
    words = text.split()
    if len(words) < 4 or words[-1] != "failed" or words[-3] != "tests," or not words[-2].isdigit():
        return None
    digits = ""
    for ch in reversed(words[-4]):
        if not "0" <= ch <= "9":
            break
        digits = ch + digits
    if not digits:
        return None
    return int(digits), int(words[-2])


def main():
    names = sorted(n for n in os.listdir(HERE) if n.startswith("test_") and n.endswith(".py"))
    tests = failed = broken = 0
    for name in names:
        status, text = run_file(HERE + "/" + name)
        found = summary(text)
        if found:
            tests += found[0]
            failed += found[1]
            line = "{} tests, {} failed".format(found[0], found[1])
        else:  # died before it could count: its last line says why
            line = text.rstrip().split("\n")[-1] if text.strip() else "no output"
        if status != 0 or not found or found[1]:
            broken += 1
            print(text.rstrip())
        print("{}: {}".format(name, line))
    print("total: {} tests, {} failed in {} files".format(tests, failed, len(names)))
    if broken or not names:
        sys.exit(1)


main()
