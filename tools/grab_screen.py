#!/usr/bin/env python3
"""Save the PicoCalc screen as a PNG over USB in about a second.

The framebuffer goes over as base64 with the screen terminal detached
meanwhile; copying a screenshot BMP off the flash with `mpremote cp` takes
over a minute. Taking the REPL stops a running launcher (host takeover), so
to catch the launcher itself copy the framebuffer into a bytearray on the
device first (see CLAUDE.md, Testing) and pass it with --source.

    tools/grab_screen.py out.png [--source snaps[0]] [--scale 3] [--port PORT]
"""

import argparse
import base64
import struct
import subprocess
import zlib

DEVICE_CODE = r"""
import os, binascii, picocalc, picocalcdisplay
_p = os.dupterm(None)
try:
    print("LUT:" + binascii.b2a_base64(bytes(picocalcdisplay.getLUTview())[:32]).decode().strip())
    _m = memoryview(SOURCE)
    for _i in range(0, len(_m), 3072):
        print("FB:" + binascii.b2a_base64(_m[_i:_i + 3072]).decode().strip())
finally:
    os.dupterm(_p)
"""


def grab(port, source):
    cmd = ["mpremote"] + (["connect", port] if port else []) + ["resume", "exec"]
    result = subprocess.run(
        cmd + [DEVICE_CODE.replace("SOURCE", source)], capture_output=True, text=True, timeout=120
    )
    lut, fb = b"", b""
    for line in result.stdout.splitlines():
        if line.startswith("LUT:"):
            lut = base64.b64decode(line[4:])
        elif line.startswith("FB:"):
            fb += base64.b64decode(line[3:])
    if len(lut) != 32 or len(fb) != 320 * 320 // 2:
        raise SystemExit("no framebuffer read: " + (result.stderr or result.stdout)[-400:])
    return lut, fb


def palette(lut):
    # The driver keeps RGB565 byte-swapped for the SPI transfer.
    colors = []
    for i in range(16):
        value = int.from_bytes(lut[2 * i : 2 * i + 2], "little")
        raw = ((value & 0xFF) << 8) | (value >> 8)
        colors.append(bytes((((raw >> 11) & 0x1F) << 3, ((raw >> 5) & 0x3F) << 2, (raw & 0x1F) << 3)))
    return colors


def png(fb, colors, scale):
    rows = []
    for y in range(320):
        row = bytearray()
        for x in range(320):
            byte = fb[y * 160 + x // 2]
            row += colors[byte >> 4 if x % 2 == 0 else byte & 0x0F] * scale  # left pixel high
        rows.extend([b"\x00" + bytes(row)] * scale)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    size = 320 * scale
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 6))
        + chunk(b"IEND", b"")
    )


def main():
    parser = argparse.ArgumentParser(description="Save the PicoCalc screen as a PNG.")
    parser.add_argument("out")
    parser.add_argument("--source", default="picocalc.display", help="a 4 bpp 320x320 buffer on the device")
    parser.add_argument("--scale", type=int, default=3)
    parser.add_argument("--port", help="serial port (default: mpremote picks the device)")
    args = parser.parse_args()
    lut, fb = grab(args.port, args.source)
    with open(args.out, "wb") as f:
        f.write(png(fb, palette(lut), args.scale))
    print(args.out)


main()
