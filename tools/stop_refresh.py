# Stop the PicoCalc's panel refresh while core1 pushes a frame: the panel's
# DMA channel (the one writing SPI1's data register) busy. Reset between
# frames, core1 can leave the pico-sdk alarm pool's spin lock taken, and
# core0 then spins on it for good. Put in front of device test runs by
# tools/test_device.sh; tools/deploy.sh runs the same lines.
try:
    import picocalc as _pc
    from machine import mem32 as _m32

    _ctrl = 0
    for _ch in range(16):
        if _m32[0x50000004 + _ch * 64] == 0x40088008:
            _ctrl = 0x5000000C + _ch * 64
    _n = 0
    while _ctrl and not _m32[_ctrl] & 0x04000000 and _n < 20000:
        _n += 1
    _pc.display.stopRefresh()
except Exception:
    pass
