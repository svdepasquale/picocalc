"""Native-code (viper) helpers for gfx's screen console: find control bytes,
turn UTF-8 into the font's CP437 codes, move framebuffer rows. gfx imports
this in a try and falls back to Python where viper is missing (CPython, the
unix port on arm64)."""

import micropython

MODULE_VERSION = "2026-10-06.1"

# Flags in the high half of to_codes()' result.
SPECIAL = 1  # box/block codes, drawn as lines and rectangles
SLOW = 2  # a character that folds to several (…, €): convert in Python
HIGH = 4  # codes from 0x8E: the firmware font is misaligned there


@micropython.viper
def scan(buf, start: int, end: int) -> int:
    # Index of the first byte below 0x20 (controls, ESC) in buf[start:end],
    # else end; times 2, plus 1 if a byte from 0x80 (not ASCII) came first.
    p = ptr8(buf)
    i = start
    high = 0
    while i < end:
        c = p[i]
        if c < 0x20:
            return (i << 1) | high
        if c >= 0x80:
            high = 1
        i += 1
    return (end << 1) | high


@micropython.viper
def to_codes(src, dst, tables, nkeys: int) -> int:
    # UTF-8 bytes in src -> one CP437 code per character in dst. tables:
    # [0:128] codes for U+0080..U+00FF, [128:384] flags per code, then nkeys
    # sorted code points above U+00FF (uint16, little-endian) and their codes
    # (0 = several characters). Returns the number of codes | flags << 16.
    # Unknown characters become "?".
    s = ptr8(src)
    d = ptr8(dst)
    t = ptr8(tables)
    end = int(len(src))
    keys = 384
    vals = 384 + 2 * nkeys
    i = 0
    j = 0
    flags = 0
    while i < end:
        c = s[i]
        if c < 0x80:
            d[j] = c
            i += 1
            j += 1
            continue
        cp = 0
        if (c & 0xE0) == 0xC0 and i + 1 < end:
            cp = ((c & 0x1F) << 6) | (s[i + 1] & 0x3F)
            i += 2
        elif (c & 0xF0) == 0xE0 and i + 2 < end:
            cp = ((c & 0x0F) << 12) | ((s[i + 1] & 0x3F) << 6) | (s[i + 2] & 0x3F)
            i += 3
        elif (c & 0xF8) == 0xF0 and i + 3 < end:
            i += 4  # outside the BMP
        else:
            i += 1  # stray byte
        code = 0x3F
        if cp >= 0x80 and cp < 0x100:
            code = t[cp - 0x80]
        elif cp >= 0x100:
            lo = 0
            hi = nkeys - 1
            while lo <= hi:
                mid = (lo + hi) >> 1
                k = t[keys + 2 * mid] | (t[keys + 2 * mid + 1] << 8)
                if k == cp:
                    code = t[vals + mid]
                    break
                if k < cp:
                    lo = mid + 1
                else:
                    hi = mid - 1
        if code == 0:
            flags |= 2
            code = 0x3F
        flags |= t[128 + code]
        d[j] = code
        j += 1
    return j | (flags << 16)


@micropython.viper
def kinds_of(buf, start: int, end: int, kinds) -> int:
    # The flags of the codes in buf[start:end] (bytes already in CP437).
    p = ptr8(buf)
    kd = ptr8(kinds)
    flags = 0
    i = start
    while i < end:
        flags |= kd[p[i]]
        i += 1
    return flags


@micropython.viper
def move_up(buf, dst: int, src: int, nbytes: int):
    # Copy nbytes from src down to dst (dst < src, all multiples of 4):
    # framebuffer rows moving up. Word by word, front to back.
    p = ptr32(buf)
    d = dst >> 2
    s = src >> 2
    n = nbytes >> 2
    i = 0
    while i + 4 <= n:
        p[d + i] = p[s + i]
        p[d + i + 1] = p[s + i + 1]
        p[d + i + 2] = p[s + i + 2]
        p[d + i + 3] = p[s + i + 3]
        i += 4
    while i < n:
        p[d + i] = p[s + i]
        i += 1


def ver():
    print("gfx_native:", MODULE_VERSION)
    return MODULE_VERSION


def help():
    print("-- gfx_native: viper helpers for gfx --")
    print("scan, to_codes, kinds_of, move_up")


def h():
    return help()
