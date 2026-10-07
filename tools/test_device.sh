#!/bin/sh
# Run the host tests on the PicoCalc, one test file at a time.
# The device compiles a script completely in RAM: the one 110 KB test file the
# suite used to be failed there with MemoryError, so it is split by area and
# each area runs alone. A run sends tests/harness.py and one tests/test_*.py as
# a single script (the area file's `from harness import *` finds no module on
# the device and goes on with the harness text in front of it) through
# `mpremote resume run`; line numbers in a traceback count from harness.py.
#   tools/test_device.sh [tests/test_core.py ...]   (default: every tests/test_*.py)
#   PICOCALC_PORT=/dev/cu.usbmodem... tools/test_device.sh   (default: mpremote picks)
# The exit status is 1 if mpremote fails or a run does not end with "0 failed".
root=$(cd "$(dirname "$0")/.." && pwd) || exit 1
if [ $# -eq 0 ]; then
  set -- "$root"/tests/test_*.py
fi
script=$(mktemp "${TMPDIR:-/tmp}/picocalc_test.XXXXXX") || exit 1
trap 'rm -f "$script"' EXIT
trap 'exit 1' HUP INT TERM
status=0
for file in "$@"; do
  echo "== ${file##*/}"
  { cat "$root/tests/harness.py"; echo; cat "$file"; } > "$script" || { status=1; continue; }
  out=$(mpremote ${PICOCALC_PORT:+connect "$PICOCALC_PORT"} resume run "$script" 2>&1) || status=1
  printf '%s\n' "$out"
  case $out in
    *" tests, 0 failed"*) ;;
    *) status=1 ;;
  esac
done
exit $status
