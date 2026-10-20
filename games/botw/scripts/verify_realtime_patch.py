#!/usr/bin/env python3
"""Execute Breath of the Wild 1.6.0's per-frame VFR update (main+0x1c01180) with Unicorn, with and without the real-time
frame-step patch of games/totk/runtime/game_patches_botw.c (botw.md).

A VFR object is laid out the way main+0x183eb98 builds it (interval 2, 1/30 s per frame, every core's pointers), and the
update runs with the game's interval 2. Patched, the frame ratio (+0x3c), core 0's delta frames (+0x108) and delta time
(+0x138) follow the measured frame time written to the data page after main; the unpatched code is the negative control:
it stays at the interval's ratio 1.0 (33.3 ms) whatever the frame time.
--version botw190 runs the 1.9.0 version of the same two checks (ksys::VFR::calc at main+0x151d3d0 and the fade at
main+0x125f108, the words of the `#else` branch of game_patches_botw.c) on the 1.9.0 image, with the same negative controls.
usage: verify_realtime_patch.py [--version {botw160,botw190}] [main.patched.bin]
       (default botw160; the image defaults to totk/rewritten/<version>/main.patched.bin)"""
import argparse
import json
import math
import re
import struct
import sys
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_HOOK_CODE, UC_MODE_ARM
from unicorn import arm64_const as A
from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_W1, UC_ARM64_REG_X0

ROOT = Path(__file__).resolve().parents[2] / "totk"


def adrp(rd, site, target):
    pages = (target >> 12) - (site >> 12)
    assert -(1 << 20) <= pages < (1 << 20)
    imm = pages & 0x1fffff
    return 0x90000000 | ((imm & 3) << 29) | ((imm >> 2) << 5) | rd


def c_190(function):
    """The check[] and put[] tables of `function` in the 1.9.0 branch of game_patches_botw.c: (check {off: original word},
    put {off: ("adrp", register, site) | ("word", value)}). Nothing about the patch words is restated in this script."""
    c = (ROOT / "runtime/game_patches_botw.c").read_text().split("Breath of the Wild 1.9.0", 1)[1]
    body = c[c.index(f"static void {function}("):]
    body = body[:body.index("\n}\n")]
    tables = {}
    for name in ("check", "put"):
        text = re.search(name + r"\[\w*\] = \{(.*?)\};", body, re.S).group(1)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        entries = re.findall(r"\{0x([0-9a-f]+)u,\s*(?:adrp_reg\((\d+), \(uintptr_t\)m->base \+ 0x([0-9a-f]+), page\)|0x([0-9a-f]+)u)\}", text)
        assert len(entries) == text.count("{0x"), f"{function}: a {name}[] entry of game_patches_botw.c is in a form this script cannot read"
        tables[name] = {}
        for off, reg, site, value in entries:
            off = int(off, 16)
            if reg:
                assert int(site, 16) == off, f"{function}: adrp at {off:#x} is written for site {site}"
                tables[name][off] = ("adrp", int(reg), off)
            else:
                tables[name][off] = ("word", int(value, 16))
    check = {off: v[1] for off, v in tables["check"].items()}
    assert set(tables["put"]) <= set(check), f"{function}: put[] writes an instruction check[] does not verify"
    return check, tables["put"]


def patch_words(put, offsets, data_off):
    """The words the C writes at `offsets` (all of them, nothing dropped), the adrp ones aimed at the data page."""
    assert set(put) == set(offsets), f"game_patches_botw.c writes {sorted(map(hex, put))}, expected {sorted(map(hex, offsets))}"
    return {off: adrp(v[1], v[2], data_off) if v[0] == "adrp" else v[1] for off, v in put.items()}


def verify_190(image_path):
    """1.9.0: ksys::VFR::calc (main+0x151d3d0) and the fade (main+0x125f108), on the flat image like the 1.6.0 checks."""
    image = image_path.read_bytes()
    data_off = (len(image) + 0x3fff) & ~0x3fff
    word = lambda a: struct.unpack_from("<I", image, a)[0]
    f32 = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]
    sreg = lambda mu, r: struct.unpack("<f", struct.pack("<I", mu.reg_read(r) & 0xffffffff))[0]
    FN, RET, FADE, FADE_END = 0x151d3d0, 0x151d514, 0x125f108, 0x125f138
    SHIPPED, put = c_190("patch_realtime")           # check[]: the words the image must hold; put[]: what the C writes
    SITE1, SITE2 = (0x151d484, 0x151d48c), (0x125f118, 0x125f11c, 0x125f128)
    for offset, original in SHIPPED.items():
        if word(offset) != original:
            raise SystemExit(f"original instruction mismatch at main+{offset:#x}: {word(offset):08x} != {original:08x}")
    ret, = struct.unpack_from("<I", image, RET)
    if ret != 0xd65f03c0:
        raise SystemExit(f"main+{RET:#x} is {ret:08x}, not the call's ret")
    # the GOT slots 1.9.0's code loads through are already relocated in the flat image: the interval helper's two slots
    # (main+0x1d66318, 0x1d66320) point into the image, the pad loop's (0x1d6e9e0) at the float 1.0 at 0x1dad0e0; only the
    # VFR singleton (0x1e93890) is a runtime pointer, so the setup below writes it
    got = {a: struct.unpack_from("<Q", image, a)[0] for a in (0x1d66318, 0x1d66320, 0x1d6e9e0, 0x1e93890)}
    assert 0 < got[0x1d66318] < len(image) and 0 < got[0x1d66320] < len(image), got
    assert got[0x1d6e9e0] == 0x1dad0e0 and word(0x1dad0e0) == 0x3f800000 and got[0x1e93890] == 0, got
    # adrp x9, <page>; ldr s1, [x9] at site 1 and adrp x8, <page>; ldr s0, [x8]; nop at site 2, as game_patches_botw.c writes them
    words = patch_words(put, SITE1 + SITE2, data_off)
    P1 = {off: words[off] for off in SITE1}
    P2 = {off: words[off] for off in SITE2}
    VFR, CORE_STUB, FMODF_STUB = 0x7000_0000, 0x159fee0, 0x15a05d0

    def machine(patches, seconds):
        mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        mu.mem_map(0, data_off + 0x4000)
        mu.mem_write(0, image)
        for offset, w in patches.items():
            mu.mem_write(offset, struct.pack("<I", w))
        mu.mem_map(VFR, 0x100000)
        mu.mem_write(0x1e93890, struct.pack("<Q", VFR))                  # ksys::VFR singleton
        mu.mem_write(data_off, struct.pack("<ff", f32(seconds * 30), seconds))

        def stubs(uc, addr, size, _):
            if addr == CORE_STUB:                                        # nn::os current core (+1): core 0
                uc.reg_write(A.UC_ARM64_REG_X0, 1)
            elif addr == FMODF_STUB:
                r = f32(math.fmod(sreg(uc, A.UC_ARM64_REG_S0), sreg(uc, A.UC_ARM64_REG_S1)))
                uc.reg_write(A.UC_ARM64_REG_S0, struct.unpack("<I", struct.pack("<f", r))[0])
            else:
                return
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(A.UC_ARM64_REG_LR))
        mu.hook_add(UC_HOOK_CODE, stubs, begin=CORE_STUB, end=FMODF_STUB + 4)
        o = bytearray(0x160)                                             # the 1.6.0 object layout, as vfr_object() below
        struct.pack_into("<I", o, 0x2c, 1)
        struct.pack_into("<f", o, 0x30, 1.0)
        struct.pack_into("<II", o, 0x34, 2, 2)
        struct.pack_into("<ff", o, 0x3c, 1.0, 1.0)
        struct.pack_into("<I", o, 0x88, 0x59)
        struct.pack_into("<IIf", o, 0x150, 2, 30, 1 / 30)
        for i in range(3):
            for base, target in ((0x48, 0x34), (0x60, 0x3c), (0xf0, 0x8c + 4 * i), (0x108, 0x98 + 4 * i),
                                 (0x120, 0xa4 + 4 * i), (0x138, 0xb0 + 4 * i)):
                struct.pack_into("<Q", o, base + 8 * i, VFR + target)
        mu.mem_write(VFR, bytes(o))
        return mu

    def calc(patches, seconds, frames=3):
        mu = machine(patches, seconds)
        for _ in range(frames):                                          # the ratio must be written every frame
            mu.reg_write(UC_ARM64_REG_X0, VFR)
            mu.reg_write(UC_ARM64_REG_W1, 2)
            mu.reg_write(UC_ARM64_REG_SP, VFR + 0x80000)
            mu.emu_start(FN, RET, count=2000)
            assert mu.reg_read(UC_ARM64_REG_PC) == RET
        o = mu.mem_read(VFR, 0x160)
        return tuple(struct.unpack_from("<f", o, x)[0] for x in (0x3c, 0x98, 0xb0))   # ratio, core 0 delta frames, delta time

    def fade(patches, seconds):                                          # s1 = the step added to the fade, before main+0x125f138
        mu = machine(patches, seconds)
        for reg, value in ((A.UC_ARM64_REG_X19, VFR + 0x40000), (A.UC_ARM64_REG_X21, 1), (UC_ARM64_REG_SP, VFR + 0x80000),
                           (A.UC_ARM64_REG_LR, 0)):
            mu.reg_write(reg, value)
        mu.emu_start(FADE, FADE_END, count=200)
        return sreg(mu, A.UC_ARM64_REG_S1), mu.reg_read(UC_ARM64_REG_X0) & 0xffffffff

    checks = []
    for hz in (30, 60, 95, 120):
        s = 1 / hz
        ratio, df, dt = calc(P1, s)
        assert abs(ratio - s * 30) < 1e-6 and abs(df - s * 30) < 1e-6 and abs(dt - s) < 1e-7, (hz, ratio, df, dt)
        step, w0 = fade(P2, s)
        assert abs(step - s * 30 * 0.2) < 1e-6 and w0 == fade({}, s)[1], (hz, step, w0)   # w0 (the helper's result) stays as shipped
        checks.append({"hz": hz, "ratio": ratio, "delta_frames": df, "delta_time_ms": dt * 1e3, "fade_step": step})
    assert abs(fade({}, 1 / 30)[0] - 0.2) < 1e-6        # shipped: interval 2 x 0.5 x 0.2, the patched value at 30 fps
    negative = calc({}, 1 / 120)                         # the shipped code ignores the page
    assert abs(negative[0] - 1.0) < 1e-6 and abs(negative[2] - 1 / 30) < 1e-7, negative
    negative_fade = fade({}, 1 / 120)[0]
    assert abs(negative_fade - 0.2) < 1e-6, negative_fade
    print(json.dumps({"status": "pass", "version": "botw190", "image": str(image_path), "data_page": hex(data_off),
                      "patched": checks, "negative_control_at_120hz": {"ratio": negative[0], "delta_time_ms": negative[2] * 1e3,
                                                                      "fade_step": negative_fade}}, indent=2))


parser = argparse.ArgumentParser(description="Unicorn check of the real-time frame-step patch")
parser.add_argument("--version", choices=("botw160", "botw190"), default="botw160")
parser.add_argument("image", nargs="?", type=Path)
args = parser.parse_args()
if args.version == "botw190":
    verify_190(args.image or ROOT / "rewritten/botw190/main.patched.bin")
    sys.exit(0)
image_path = args.image or ROOT / "rewritten/botw160/main.patched.bin"
image = image_path.read_bytes()
data_off = (len(image) + 0x3fff) & ~0x3fff
FN, RET = 0x1c01180, 0x1c01394
SITES = {0x1c0120c: 0x1e202020, 0x1c01210: 0x3900a008, 0x1c01214: 0x54000060, 0x1c01218: 0xbd003c01}
for offset, original in SITES.items():
    actual, = struct.unpack_from("<I", image, offset)
    if actual != original:
        raise SystemExit(f"original instruction mismatch at main+{offset:#x}: {actual:08x} != {original:08x}")
ret, = struct.unpack_from("<I", image, RET)
if ret != 0xd65f03c0:
    raise SystemExit(f"main+{RET:#x} is {ret:08x}, not the update's ret")


PATCH = {0x1c0120c: adrp(9, 0x1c0120c, data_off), 0x1c01214: 0xbd400121}   # adrp x9, <page>; ldr s1, [x9]
VFR = 0x100000


def vfr_object():
    o = bytearray(0x160)
    struct.pack_into("<I", o, 0x2c, 1)
    struct.pack_into("<f", o, 0x30, 1.0)
    struct.pack_into("<II", o, 0x34, 2, 2)          # interval, previous interval (second setup at main+0x183ec94)
    struct.pack_into("<ff", o, 0x3c, 1.0, 1.0)      # ratio, previous ratio
    struct.pack_into("<I", o, 0x88, 0x59)
    struct.pack_into("<IIf", o, 0x150, 2, 30, 1 / 30)
    for i in range(3):                              # per-core pointer arrays, as main+0x183ec3c..0x183ec8c set them
        struct.pack_into("<Q", o, 0x48 + 8 * i, VFR + 0x34)
        struct.pack_into("<Q", o, 0x60 + 8 * i, VFR + 0x3c)
        struct.pack_into("<Q", o, 0xf0 + 8 * i, VFR + 0x8c + 4 * i)
        struct.pack_into("<Q", o, 0x108 + 8 * i, VFR + 0x98 + 4 * i)
        struct.pack_into("<Q", o, 0x120 + 8 * i, VFR + 0xa4 + 4 * i)
        struct.pack_into("<Q", o, 0x138 + 8 * i, VFR + 0xb0 + 4 * i)
    return bytes(o)


def run(seconds, patched, frames=3):
    mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    for page in sorted({FN & ~0x3fff, RET & ~0x3fff, 0x260d000 & ~0x3fff}):
        mu.mem_map(page, 0x4000)
        mu.mem_write(page, image[page:page + 0x4000])
    mu.mem_map(data_off, 0x4000)
    mu.mem_map(VFR, 0x4000)
    if patched:
        for offset, word in PATCH.items():
            mu.mem_write(offset, struct.pack("<I", word))
    mu.mem_write(VFR, vfr_object())
    mu.mem_write(data_off, struct.pack("<ff", seconds * 30, seconds))
    for _ in range(frames):                         # several frames: the ratio must be written every frame, not only on a change
        mu.reg_write(UC_ARM64_REG_X0, VFR)
        mu.reg_write(UC_ARM64_REG_W1, 2)
        mu.reg_write(UC_ARM64_REG_SP, VFR + 0x3000)
        mu.emu_start(FN, RET, count=400)
        assert mu.reg_read(UC_ARM64_REG_PC) == RET
    o = mu.mem_read(VFR, 0x160)
    ratio, = struct.unpack_from("<f", o, 0x3c)
    frames_core0, = struct.unpack_from("<f", o, 0x98)
    seconds_core0, = struct.unpack_from("<f", o, 0xb0)
    interval, = struct.unpack_from("<I", o, 0x34)
    return ratio, frames_core0, seconds_core0, interval


# The fade at main+0x1821548: from the load of the VFR's interval pointer to the step (s0) before it is added.
FADE, FADE_END = 0x1821564, 0x1821590
FADE_ORIG = {0x1821564: 0xf9400108, 0x1821568: 0xb9400108, 0x182156c: 0x7100011f, 0x1821570: 0x1a9f1508,
             0x1821574: 0x1e230100, 0x1821578: 0x1e2c100b, 0x182157c: 0xb0006f68, 0x1821580: 0xbd409109,
             0x1821584: 0xbd435ee1, 0x1821588: 0x1e2b0800, 0x182158c: 0x1e290800}
for offset, original in FADE_ORIG.items():
    actual, = struct.unpack_from("<I", image, offset)
    if actual != original:
        raise SystemExit(f"original instruction mismatch at main+{offset:#x}: {actual:08x} != {original:08x}")
NOP = 0xd503201f
FADE_PATCH = {0x1821568: adrp(8, 0x1821568, data_off), 0x182156c: 0xbd400100, 0x1821570: NOP, 0x1821574: NOP, 0x1821588: NOP}


def fade_step(seconds, patched, interval=2):
    from unicorn.arm64_const import UC_ARM64_REG_X8, UC_ARM64_REG_X23, UC_ARM64_REG_S0
    mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    for page in sorted({FADE & ~0x3fff, 0x260e000 & ~0x3fff}):
        mu.mem_map(page, 0x4000)
        mu.mem_write(page, image[page:page + 0x4000])
    mu.mem_map(data_off, 0x4000)
    mu.mem_map(VFR, 0x4000)
    if patched:
        for offset, word in FADE_PATCH.items():
            mu.mem_write(offset, struct.pack("<I", word))
    mu.mem_write(data_off, struct.pack("<ff", seconds * 30, seconds))
    mu.mem_write(VFR + 0x34, struct.pack("<I", interval))      # the interval the per-core pointer points at
    mu.mem_write(VFR + 0x48, struct.pack("<Q", VFR + 0x34))   # x8 = &pointer (the core's slot of the +0x48 array)
    mu.reg_write(UC_ARM64_REG_X8, VFR + 0x48)
    mu.reg_write(UC_ARM64_REG_X23, VFR + 0x1000)               # the fading object; +0x35c = 0.0
    mu.emu_start(FADE, FADE_END, count=20)
    assert mu.reg_read(UC_ARM64_REG_PC) == FADE_END
    return struct.unpack("<f", struct.pack("<I", mu.reg_read(UC_ARM64_REG_S0) & 0xffffffff))[0]


checks = []
for hz in (30, 45, 60, 90, 100, 120):
    s = 1 / hz
    ratio, df, dt, interval = run(s, True)
    assert abs(ratio - s * 30) < 1e-6, (hz, ratio)
    assert abs(df - s * 30) < 1e-6, (hz, df)
    assert abs(dt - s) < 1e-7, (hz, dt)
    assert interval == 2
    fade = fade_step(s, True)
    assert abs(fade - s * 30 * 0.2) < 1e-6, (hz, fade)
    checks.append({"hz": hz, "ratio": ratio, "delta_frames": df, "delta_time_ms": dt * 1e3, "fade_step": fade})
assert abs(fade_step(1 / 30, False) - 0.2) < 1e-6   # shipped: interval 2 x 0.5 x 0.2, the patched value at 30 fps
negative = run(1 / 120, False)
assert abs(negative[0] - 1.0) < 1e-6 and abs(negative[2] - 1 / 30) < 1e-7, negative
negative_fade = fade_step(1 / 120, False)
assert abs(negative_fade - 0.2) < 1e-6, negative_fade
print(json.dumps({"status": "pass", "image": str(image_path), "data_page": hex(data_off), "patched": checks,
                  "negative_control_at_120hz": {"ratio": negative[0], "delta_time_ms": negative[2] * 1e3,
                                                "fade_step": negative_fade}}, indent=2))
