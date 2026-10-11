#!/bin/sh
# Copy the toolkit to the PicoCalc over USB, then restart it.
# Modules go as .mpy, compiled here by mpy-cross for the device's bytecode
# version and architecture: the device loads them 5-11x faster than it
# compiles a .py (rss_news 692 -> 64 ms, measured 2026-10-11) and skips the
# compiler's heap peak. Kept as source: main.py (run as a file), go.py (it
# says when the .mpy files don't suit the firmware) and default_style.py (the
# power-on hook the firmware's boot.py imports: it stays readable and loads
# whatever MicroPython comes next). A .py next to a .mpy of the same name wins
# on import, so the device's copy of the other kind is removed.
# The panel refresh runs on core1 from code in flash while files are written,
# and the driver never lets MicroPython pause core1 for flash writes: a copy
# hung the device once (2026-10-06). So the refresh stops first (at a safe
# point of core1's loop, see below), and the reset afterwards brings
# everything back.
#   tools/deploy.sh [file.py ...]        (default: every .py in the repo root)
#   tools/deploy.sh --py [file.py ...]   sources only, no mpy-cross
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
source_only=
if [ "$1" = "--py" ]; then
  source_only=1
  shift
fi
if [ $# -gt 0 ]; then
  files="$*"
else
  files=$(ls ./*.py)
fi

# The device's .mpy version (major.minor) and architecture, from
# sys.implementation._mpy: bits 0-7 major, 8-9 minor, 10+ architecture.
arch=
if [ -z "$source_only" ]; then
  if ! command -v mpy-cross >/dev/null 2>&1; then
    echo "mpy-cross not found (brew install micropython): sending sources"
    source_only=1
  else
    code=$(mp exec "import sys; print(sys.implementation._mpy)" | tr -dc '0-9')
    want="$((code & 255)).$(((code >> 8) & 3))"
    have=$(mpy-cross --version | sed -n 's/.*mpy v\([0-9.]*\).*/\1/p')
    case $((code >> 10)) in
      5) arch=armv7m ;;
      6) arch=armv7em ;;
      7) arch=armv7emsp ;;
      8) arch=armv7emdp ;;
      4) arch=armv6m ;;
      *) arch= ;;
    esac
    if [ "$want" != "$have" ] || [ -z "$arch" ]; then
      echo "device wants mpy v$want ($arch), mpy-cross makes v$have: sending sources"
      source_only=1
    fi
  fi
fi

build=$(mktemp -d "${TMPDIR:-/tmp}/picocalc_build.XXXXXX")
trap 'rm -rf "$build"' EXIT
sent=
stale=
for file in $files; do
  name=$(basename "$file" .py)
  case "$name" in
    "" | [0-9]* | *[!A-Za-z0-9_]*)
      echo "not a module name: $file (nothing sent)"
      exit 1
      ;;
  esac
  case "$name" in
    main | go | default_style) keep=1 ;;
    *) keep=$source_only ;;
  esac
  if [ -n "$keep" ]; then
    cp "$file" "$build/$name.py"
    sent="$sent $build/$name.py"
    stale="$stale '$name.mpy',"
  else
    mpy-cross -march="$arch" -o "$build/$name.mpy" "$file"
    sent="$sent $build/$name.mpy"
    stale="$stale '$name.py',"
  fi
done

# Stop the refresh while core1 pushes a frame (the panel's DMA busy): reset
# during its sleep between frames it can keep the pico-sdk alarm pool's spin
# lock, and core0 then hangs for good on it (pico_utils._stop_refresh).
mp exec "
import picocalc
from machine import mem32
ctrl = 0
for ch in range(16):
    if mem32[0x50000004 + ch * 64] == 0x40088008:
        ctrl = 0x5000000C + ch * 64
n = 0
while ctrl and not mem32[ctrl] & 0x04000000 and n < 20000:
    n += 1
picocalc.display.stopRefresh()
"
# shellcheck disable=SC2086
mp cp $sent :
mp exec "
import os
for n in [$stale]:
    try:
        os.remove(n)
    except OSError:
        pass
"
mp exec "import machine; machine.reset()" || true  # the USB link drops on reset
echo "deployed:$(for f in $sent; do printf ' %s' "$(basename "$f")"; done)"
