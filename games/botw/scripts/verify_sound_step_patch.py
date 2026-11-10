#!/usr/bin/env python3
"""Run Breath of the Wild 1.9.0's sound frame update (main+0x1550fcc) with Unicorn, as shipped and with patch_sound_step of
games/totk/runtime/game_patches_botw.c.

The update reads the VFR's ratio (main+0x59c788: core 0's ratio, +0x3c), floors it at 0.5 (one 60 fps frame) into s8, and
gives s8 to the sound clock [[main+0x1e36488]+0x38] (+0x20 = s8, +0x24 = s8 / the VFR's 30 at +0x154: the seconds the
sound timers add each frame) and to the xlink2 sound system's rate [main+0x1e37038]+0x84. The VFR object is laid out the
way its constructor builds it (botw190_unicorn.vfr_object), with the ratio the real-time VFR patch writes (frame time x 30;
verify_realtime_patch.py --version botw190 checks that part). One real second of frames runs at each rate:
- patched, the sound clock adds up to 1 s (within 0.1 %) and the xlink2 rate equals the ratio at 30, 60, 95, 120, 144 and
  240 fps;
- patched at 30 and 60 fps, every value equals the shipped code's;
- negative control: shipped at 120 fps the sound clock adds up to 2 s (the 0.5 floor), and must not pass the check above.
The patch and shipped words are parsed from the check[] and put[] tables of patch_sound_step; this script restates none.
usage: verify_sound_step_patch.py [main.patched.bin]   (default totk/rewritten/botw190/main.patched.bin)"""
import argparse
import json
import struct
import sys
from pathlib import Path

from unicorn.arm64_const import UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from botw190_unicorn import ROOT, VFR, c_190, f32, machine, patch_words

FN, SITE, RET = 0x1550fcc, 0x155100c, 0x1550ff8
SOUND_MGR, SLINK_SYS = 0x1e36488, 0x1e37038                 # pointer slots in main, filled at run time
MGR, CLOCK, SLINK = VFR + 0x10000, VFR + 0x11000, VFR + 0x12000
STOP = VFR + 0x20000                                        # the return address: emulation ends there


def frames(image, data_off, patches, fps):
    """One second of frames at `fps`: (seconds the sound clock added, the clock's last +0x20, the xlink2 rate)."""
    mu = machine(image, data_off, patches)
    ratio = f32(30.0 / fps)
    mu.mem_write(data_off, struct.pack("<ff", ratio, f32(1.0 / fps)))
    mu.mem_write(VFR + 0x3c, struct.pack("<f", ratio))      # what the patched VFR::calc stores every frame
    mu.mem_write(SOUND_MGR, struct.pack("<Q", MGR))
    mu.mem_write(MGR + 0x38, struct.pack("<Q", CLOCK))
    mu.mem_write(CLOCK + 0x1c, struct.pack("<ffffff", 30.0, 1.0, 1 / 30, 1.0, 1.0, 1.0))   # as at 30 fps
    mu.mem_write(SLINK_SYS, struct.pack("<Q", SLINK))
    total = 0.0
    for _ in range(fps):
        mu.reg_write(UC_ARM64_REG_SP, VFR + 0x80000)
        mu.reg_write(UC_ARM64_REG_LR, STOP)
        mu.emu_start(FN, STOP, count=500)
        assert mu.reg_read(UC_ARM64_REG_PC) == STOP
        total += struct.unpack("<f", mu.mem_read(CLOCK + 0x24, 4))[0]   # what a sound timer adds this frame
    rate, = struct.unpack("<f", mu.mem_read(SLINK + 0x84, 4))
    step, = struct.unpack("<f", mu.mem_read(CLOCK + 0x20, 4))
    return total, step, rate


def main():
    parser = argparse.ArgumentParser(description="Unicorn check of the 1.9.0 sound-step patch")
    parser.add_argument("image", nargs="?", type=Path, default=ROOT / "rewritten/botw190/main.patched.bin")
    image = parser.parse_args().image.read_bytes()
    data_off = (len(image) + 0x3fff) & ~0x3fff
    word = lambda a: struct.unpack_from("<I", image, a)[0]
    shipped, put = c_190("patch_sound_step")
    for offset, original in shipped.items():
        if word(offset) != original:
            raise SystemExit(f"original instruction mismatch at main+{offset:#x}: {word(offset):08x} != {original:08x}")
    if word(RET) != 0xd65f03c0:
        raise SystemExit(f"main+{RET:#x} is {word(RET):08x}, not the update's ret")
    if any(struct.unpack_from("<Q", image, a)[0] for a in (SOUND_MGR, SLINK_SYS, 0x1e93890)):
        raise SystemExit("the sound manager, xlink2 system or VFR slot is not empty in the image")
    patched = patch_words(put, (SITE,), data_off)

    def real_time(result, fps):
        total, step, rate = result
        return abs(total - 1.0) < 1e-3 and abs(rate - 30.0 / fps) < 1e-6 and abs(step - 30.0 / fps) < 1e-6

    checks = {}
    for fps in (30, 60, 95, 120, 144, 240):
        result = frames(image, data_off, patched, fps)
        assert real_time(result, fps), (fps, result)
        if fps in (30, 60):
            assert result == frames(image, data_off, {}, fps), (fps, result)   # the shipped values at 30 and 60 fps
        checks[f"{fps} fps"] = {"sound_clock_s_per_s": round(result[0], 5), "xlink2_rate": round(result[2], 5)}
    negative = frames(image, data_off, {}, 120)             # shipped: the 0.5 floor, the clock runs twice as fast
    assert not real_time(negative, 120) and abs(negative[0] - 2.0) < 1e-3 and negative[2] == 0.5, negative
    print(json.dumps({"patched": checks, "negative_control_shipped_120fps": {"sound_clock_s_per_s": round(negative[0], 5),
                                                                            "xlink2_rate": negative[2]}}, indent=2))


if __name__ == "__main__":
    main()
