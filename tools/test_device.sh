#!/bin/sh
# Run the host tests on the PicoCalc, one test file at a time.
# The device compiles a script completely in RAM: the one 110 KB test file the
# suite used to be failed there with MemoryError, so it is split by area and
# each area runs alone. A run sends tests/harness.py and one tests/test_*.py as
# a single script (the area file's `from harness import *` finds no module on
# the device and goes on with the harness text in front of it) through
# `mpremote resume run`; line numbers in a traceback count from harness.py.
# The device is reset before each file: runs share the REPL's globals and
# heap, so the second file re-ran the first one's tests and the later ones
# failed with MemoryError (2026-10-07). Never a soft reset: one leaves the
# stock terminal and stalled the next mpremote connection.
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
mp() {
  mpremote ${PICOCALC_PORT:+connect "$PICOCALC_PORT"} resume "$@"
}
fresh() {
  # reset, then wait for the device to answer again (boot, launcher, Wi-Fi)
  mp exec "import machine; machine.reset()" >/dev/null 2>&1
  sleep 8
  tries=0
  until mp exec "print('up')" 2>/dev/null | grep -q up; do
    tries=$((tries + 1))
    [ $tries -ge 10 ] && return 1
    sleep 3
  done
}
status=0
for file in "$@"; do
  echo "== ${file##*/}"
  { cat "$root/tests/harness.py"; echo; cat "$file"; } > "$script" || { status=1; continue; }
  fresh || { echo "device not answering after a reset"; status=1; break; }
  out=$(mp run "$script" 2>&1) || status=1
  printf '%s\n' "$out"
  case $out in
    *" tests, 0 failed"*) ;;
    *) status=1 ;;
  esac
done
exit $status
