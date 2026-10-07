#!/bin/sh
# Copy the toolkit to the PicoCalc over USB, then restart it.
# The panel refresh runs on core1 from code in flash while files are written,
# and the driver never lets MicroPython pause core1 for flash writes: a copy
# hung the device once (2026-10-06; that this is the cause is not proven). So
# the refresh stops first, and the reset afterwards brings everything back.
#   tools/deploy.sh [file.py ...]   (default: every .py in the repo root)
#   PICOCALC_PORT=/dev/cu.usbmodem... tools/deploy.sh   (default: mpremote picks)
set -e
cd "$(dirname "$0")/.."
mp() {
  if [ -n "$PICOCALC_PORT" ]; then
    mpremote connect "$PICOCALC_PORT" resume "$@"
  else
    mpremote resume "$@"
  fi
}
if [ $# -gt 0 ]; then
  files="$*"
else
  files=$(ls ./*.py)
fi
mp exec "import picocalc; picocalc.display.stopRefresh()"
# shellcheck disable=SC2086
mp cp $files :
mp exec "import machine; machine.reset()" || true  # the USB link drops on reset
echo "deployed: $files"
