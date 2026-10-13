#!/bin/sh
# Build the PicoCalc's MicroPython firmware with the flash-write hang fixed.
#
# The base is the official ClockworkPi build the toolkit runs on
# (Bin/MicroPython/micropython_pico2w.uf2 of 2025-10-30: MicroPython
# v1.27.0-preview.370 = 4efc5e12b9, zenodante's PicoCalc-micropython-driver
# fa8b24c, its Python modules frozen in), reproduced: built this way, all 541
# frozen functions match that file byte for byte (checked 2026-10-12; the C
# side differs in the build dates and newlib, 4.3.0 here against 4.4.0, with
# the same GCC 13.2.1). Three patches, in tools/firmware/:
#   driver-keyboard-no-reset.diff  ClockworkPi's own change, read back from
#       that file's bytecode: PicoKeyboard no longer resets the keyboard MCU at
#       start (from keyboard firmware 1.4 the reset restarts the whole
#       PicoCalc, so the device rebooted endlessly)
#   driver-core1-lockout.diff  core1, which refreshes the panel running from
#       flash, registers as a multicore lockout victim: it waits in RAM while
#       core0 erases or programs the flash
#   micropython-rp2_flash.diff  MicroPython locks core1 out whenever it is a
#       victim; upstream also wants one of its own threads there (core1_entry),
#       a workaround for pico-sdk #2201, which the pico-sdk it uses (2.1.1)
#       fixes in multicore_reset_core1()
# Without the last two a flash write can hang the device (CLAUDE.md, flash
# writes).
#
#   tools/build_firmware.sh [WORKDIR]   (default ~/projects/picocalc-firmware)
#
# Needs git (GitHub over SSH), cmake, make and curl. On macOS arm64 the ARM
# toolchain the official build used (GCC 13.2.1) is downloaded into WORKDIR and
# checked against its published SHA-256; elsewhere put arm-none-eabi-gcc
# 13.2.1 on PATH first. Prints the UF2's path and hash. To flash it:
# `machine.bootloader()` at the REPL (or BOOTSEL held while plugging in), then
# copy the file to the RP2350 drive; the official UF2 goes back the same way.
# The files on the flash stay: the filesystem keeps its place (the board's
# default layout, as in the official build).
set -e
here=$(cd "$(dirname "$0")" && pwd)
patches="$here/firmware"
work=${1:-$HOME/projects/picocalc-firmware}
MP_COMMIT=4efc5e12b9
DRIVER_COMMIT=fa8b24c3d
BOARD=RPI_PICO2_W
TOOLCHAIN=arm-gnu-toolchain-13.2.rel1-darwin-arm64-arm-none-eabi.tar.xz
TOOLCHAIN_SHA256=39c44f8af42695b7b871df42e346c09fee670ea8dfc11f17083e296ea2b0d279

mkdir -p "$work"
cd "$work"

if [ ! -x toolchain/bin/arm-none-eabi-gcc ] && ! command -v arm-none-eabi-gcc >/dev/null 2>&1; then
  if [ "$(uname -s)-$(uname -m)" != "Darwin-arm64" ]; then
    echo "put arm-none-eabi-gcc 13.2.1 on PATH: the toolchain download is the macOS arm64 one"
    exit 1
  fi
  curl -sSL -o "$TOOLCHAIN" "https://developer.arm.com/-/media/Files/downloads/gnu/13.2.rel1/binrel/$TOOLCHAIN"
  echo "$TOOLCHAIN_SHA256  $TOOLCHAIN" | shasum -a 256 -c -
  mkdir -p toolchain
  tar -xJf "$TOOLCHAIN" -C toolchain --strip-components 1
  rm "$TOOLCHAIN"
fi
if [ -x toolchain/bin/arm-none-eabi-gcc ]; then
  PATH="$work/toolchain/bin:$PATH"
  export PATH
fi

# GitHub over SSH, submodules included
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=url.git@github.com:.insteadOf GIT_CONFIG_VALUE_0=https://github.com/
[ -d micropython ] || git clone -q --filter=blob:none git@github.com:micropython/micropython.git
[ -d PicoCalc-micropython-driver ] || git clone -q git@github.com:zenodante/PicoCalc-micropython-driver.git

# The pinned sources, clean, then the patches
git -C micropython checkout -q -f "$MP_COMMIT"
git -C micropython clean -q -fd ports/rp2/modules
git -C PicoCalc-micropython-driver checkout -q -f "$DRIVER_COMMIT"
git -C micropython apply "$patches/micropython-rp2_flash.diff"
git -C PicoCalc-micropython-driver apply "$patches/driver-core1-lockout.diff" "$patches/driver-keyboard-no-reset.diff"

# Frozen as in the official build: the driver's modules, boot.py and main.py
driver="$work/PicoCalc-micropython-driver"
cp "$driver"/pico_files/modules/*.py micropython/ports/rp2/modules/
cp "$driver/pico_files/root/boot.py" "$driver/pico_files/root/main.py" micropython/ports/rp2/modules/
cat > usercmodules.cmake <<'EOF'
include(${CMAKE_CURRENT_LIST_DIR}/PicoCalc-micropython-driver/picocalcdisplay/micropython.cmake)
include(${CMAKE_CURRENT_LIST_DIR}/PicoCalc-micropython-driver/vtterminal/micropython.cmake)
EOF

log="$work/build.log"
{
  make -C micropython/mpy-cross -j8
  make -C micropython/ports/rp2 BOARD=$BOARD submodules
  make -C micropython/ports/rp2 BOARD=$BOARD BUILD=build-picocalc USER_C_MODULES="$work/usercmodules.cmake" -j8
} > "$log" 2>&1 || {
  tail -30 "$log"
  echo "build failed: $log"
  exit 1
}
uf2="$work/micropython/ports/rp2/build-picocalc/firmware.uf2"
echo "$uf2"
shasum -a 256 "$uf2"
