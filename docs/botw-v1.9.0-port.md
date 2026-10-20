# Breath of the Wild 1.9.0 port: test record

Breath of the Wild (BOTW) 1.9.0 with both DLC packs builds and runs on the BOTW 1.6.0 port of this repository. Both
versions use the runtime and the NVN-to-Metal layer of Tears of the Kingdom (TOTK) in `games/totk`. The BOTW pins and
shader list are in `games/botw/assets`. Code for both BOTW versions is behind `GAME_BOTW`, code for one version behind
`GAME_BOTW160` or `GAME_BOTW190`.

Mac: Apple M1 Pro, 16 GB, macOS 27.0, built-in 120 Hz Liquid Retina XDR display (3456x2234). Date: 2026-10-20.

## Build

Inputs:

- the base game NSP (`01007EF00011E000`) as the game file
- the 1.9.0 update NSP (`01007EF00011E800`) with `--update`
- the DLC NSPs with `--dlc`, once for each pack (`01007EF00011F001`, `01007EF00011F002`). Each pack is optional. A pack
  that is left out is removed from the work folder, so the app holds the given packs only.
- `--firmware` with an unpacked firmware NCA folder for the six shared fonts (an NSP holds none), or `--shared-fonts`

```sh
python3.14 build_app.py ~/Games/botw-base.nsp -k ~/keys/prod.keys --update ~/Games/botw-1.9.0.nsp \
    --dlc ~/Games/botw-dlc-1.nsp --dlc ~/Games/botw-dlc-2.nsp --firmware ~/Games/firmware
```

`build_app.py` picks the game by the title IDs in the file, never by its name. A card image builds 1.6.0. A base NSP
needs `--update` and builds 1.9.0. A package on the wrong option stops the build and names the option it belongs on.
`--dlc` with a card image or with TOTK also stops the build. Both BOTW versions use the work folder `BotW_build`, the app
`Breath of the Wild.app` and the data folder `~/Library/Application Support/MacNativeGames/BotW`.

Steps:

- Code: `scripts/extract_exefs.py --update --title 01007EF00011E000` takes the ExeFS from the update NSP. The generic
  jump-table detector of the rewriter already rewrites the three 1.9.0 modules to the pinned bytes, so the rewriter is
  unchanged.
- Data: `scripts/extract_update_romfs.py --title` merges the base and 1.9.0 RomFS (hactool BKTR) and verifies every IVFC
  level. `games/botw/scripts/extract_dlc.py` verifies all six IVFC levels of each pack's Data NCA (`verify_ivfc`, split
  out of `extract_romfs.py`) and writes `extracted/botw190/aoc/<index>`. This is the layout of the 1.6.0 card, which
  `prepare_romfs_backend.py --aoc` and `runtime/aoc_romfs.c` already read.
- Runtime: `make runtime-botw190` in `games/totk` compiles the 1.6.0 runtime sources with `-DGAME_BOTW=1
  -DGAME_BOTW190=1`, and its own graphics objects in `build/gfx-botw190`.

Pins (`games/botw/assets/expected-v1.9.0.json`, from a real run):

| Item | Pin |
|---|---|
| Version | 1.9.0 (1114112) |
| ExeFS | `main`, `main.npdm`, `rtld`, `sdk`, `subsdk0` |
| Module IDs | `main` `cd57b23f…`, `sdk` `574284b4…`, `subsdk0` `f39f4c43…` |
| Rewritten modules | `main` `6d48bff1…`, `sdk` `4d625d9b…`, `subsdk0` `03229589…` |
| Merged RomFS | 15,132,662,100 bytes, 41,062 files |
| DLC `01007EF00011F001` | 1,390,370,680 bytes, 7,094 files: the DLC content |
| DLC `01007EF00011F002` | 648 bytes, one file (`System/AocVersion.txt`): a version and entitlement pack |
| Shared fonts | firmware 22.5.0; four of the six differ from the TOTK 1.0.0 pins |

The two DLC RomFS images are the same as on the 1.6.0 card and have the same pins.

## Rewrite verification

`verify_rewrites.py --version botw190` passed in 53 s and caught 600 of 600 negative controls.

| Module | Structure sites | Semantic sites |
|---|---|---|
| `main` | 12,529 | 12,435 |
| `sdk` | 15,797 | 15,696 |
| `subsdk0` | 9,784 | 9,784 |

1.9.0 has no x18 exit window, as 1.6.0. Its four exclusive stores with a w18 status (one in `main`, three in `sdk`) are
compare-and-swap loops that retry inside the window. `prepare_runtime.py`, `prepare_romfs_backend.py` and
`test_exclusive_windows.py` take `botw190`.

## Runtime: what differs from 1.6.0

1.9.0 has the game code of 1.6.0 with the SDK 20.5 of a later build. The runtime answers the differences:

- `game_version.h` has a `GAME_BOTW190` block: folder `botw190`, and the `nnMain` and `ShowKeyboard` addresses of 1.9.0.
- `nn::init::Start` takes five arguments (SDK 20.5, as TOTK), not the four of SDK 7.
- NVN reports version 55.15, the TOTK value, not 53.311.
- Three calls that 1.6.0 never makes now have answers. `nn::account::PopOpenUsers` gives the one local user.
  `nn::pl::IsRunningOnOunce` answers no, so the game takes the paths of the original Switch. Audio renderer commands 12
  and 13 keep the voice-drop parameter and give it back. The mixer never drops voices.
- Real time at any frame rate (`game_patches_botw.c`, the `#else` branch of `GAME_BOTW160`). Three parts follow the
  measured frame step: the VFR ratio (`ksys::VFR::calc`, main+0x151d484/48c), the fade (main+0x125f118/11c/128) and the
  controller's held counts (main+0xeb0f78/80). 1.9.0 counts held frames in floats, so the ratio is the step and no
  tick is needed. The fast-repeat countdown of the pause menu already reads the VFR's delta frames. Each patch checks
  the original words first. `verify_realtime_patch.py` and `verify_pad_repeat_patch.py` with `--version botw190` read
  the words from the C file and run them in Unicorn, with negative controls.
- MetalFX keys 2 and 3: 1.9.0 upscales the 1600x900 picture into the 1920x1080 canvas with its own fragment shader
  (`bd7ffdd4`, nine texel reads). `BOTW_MFXUP_KEY` in `nvn_obj.h` is the upscale key of the version that is built.

Two changes act on both BOTW versions:

- Write-back staging: the game invalidates 1x1 and 3x3 GPU-written textures every frame. The write-back waited for all
  GPU work (`mtl_finish_gpu`) and made one blit round trip per texture on the game thread. On our earlier stack,
  Kakariko ran at 49 FPS. Now the submission that renders such a texture (256 KiB or less) copies it into a shared stage
  buffer at its end. The invalidate waits only for the fences of the listed renderings and swizzles a staged texture
  from its stage. Larger textures keep the read-back blit. With staging, Kakariko holds 59.5 FPS at the 60 FPS cap.
- 120 Hz displays: the uncapped mode stays off on a display of 120 Hz or more (`UNCAPPED_HZ`). The BOTW rule that
  ignores the game's present interval 2 acted only in that mode, so BOTW ran at 60 FPS on a 120 Hz display. The
  display-following path now ignores interval 2 in the same cases: real time, `DM_PRESENT_HZ` unset, `TOTK_UNCAPPED`,
  `TOTK_FPS60` and `TOTK_UNCAPPED_INTERVAL` not 0, and no deterministic clock. Interpolation at half rate or 30+30 keeps its
  own pacing. The result is one game frame per refresh, up to 120 FPS. The log names the opt-out.

## Route measurements

Build: `1209b15` plus the 120 Hz change, which was committed after review as `baa4fba` (a dead term and the probe hooks
removed, a log line added). Both DLC packs, the uncapped real-time default, MetalFX keys off, write-back staging on.
Window output: 1920x1080 canvas, 1600x900 render. Each route used its own profile with the route save in slot 0 and a
warm shader cache. The castle, field and Korok routes had one warm-up run first. Still view from T0 to T0+20 s
(T0 = 115 s), then a walk with a right turn from T0+20 to T0+35 s. FPS is the frames in the window divided by the
window length.

| Route | Still FPS | Still p50 / p95 ms | Still frames > 16.7 ms | Walk FPS | Walk p50 / p95 ms | Walk frames > 16.7 ms | GPU % still / walk |
|---|---|---|---|---|---|---|---|
| Shrine of Resurrection | 78.4 | 12.4 / 15.0 | 25 | 27.9 | 12.2 / 20.4 | 32 | 89 / 48 |
| Great Plateau (cliff) | 66.3 | 14.7 / 17.5 | 158 | 46.5 | 15.9 / 20.7 | 227 | 94 / 86 |
| Kakariko (Goddess statue) | 64.0 | 15.3 / 17.8 | 190 | 19.1 | 17.1 / 70.1 | 163 | 95 / 77 |
| Central Hyrule field | 82.6 | 11.7 / 14.3 | 19 | 74.5 | 12.0 / 15.1 | 25 | 90 / 92 |
| Korok Forest | 69.5 | 13.9 / 17.3 | 99 | 64.7 | 14.9 / 18.1 | 149 | 94 / 95 |
| Hyrule Castle exit | 60.1 | 16.4 / 18.3 | 405 | 54.4 | 14.4 / 18.7 | 154 | 95 / 82 |

- The walk windows include first-time pipeline compiles. The walks reach new shader variants in every run: each run
  translates 4-8 shaders not seen before, and all are cached afterwards. The log's long-frame lines (`[长帧]`) show frames
  of 1-3.3 s that create 2-9 pipelines and shader libraries. An example is the Shrine at 146.2 s: 9 pipelines and
  4 libraries, 3227 ms.
- The Kakariko walk also swims into the pond. In the fresh profile, it starts the banner of the DLC quest
  "EX Treasure: Garb of Winds" and an autosave.
- No route crashed. CPU per frame (`nvn_ms` p50): 6.9-10.4 ms. GPU p50: 7.6-11.8 ms.

Other checks:

- Capped reference, before the 120 Hz change (60 FPS on this display): Kakariko still view 59.5 FPS, p50 16.6 ms.
- Real-time step at 120 FPS (title screen): 3601 frames in 30.0 s. The steps add up to 1.0021-1.0033 of the frames'
  time, so game time runs 0.2-0.3 % fast at the 1/120 s clamp. In game at 78-83 FPS: 1.0002-1.0014.
- Key 3 (Great Plateau): MetalFX replaced 1,801 of 1,801 upscales in a stats window and kept none. The picture is
  correct with the HUD on top, and the still view runs at 65.8 FPS. Key 2 (`interpmax`) interpolates every in-game
  frame, with 7.1 ms of MetalFX GPU time.
- Picture: the pond water colour, the caption picture of a new autosave and the first Game Over glyphs are correct.
- Save loads: the 6 route runs, the 3 warm-ups and the 6 checks each loaded a save, with no stop on a command handle.

## Shared edits for the TOTK re-test

Every file under `games/totk` that this port changes (`git diff --stat e58b2bd HEAD -- games/totk`):

| File | What changed | Guard |
|---|---|---|
| `Makefile` | new rules `runtime-botw190`, `build/totk-native-botw190`, `build/gfx-botw190/*.o` and `build/botw190/*.h` | additive; no TOTK rule changed |
| `runtime/account.c` | `nn::account::PopOpenUsers` gives the one local user | `#ifdef GAME_BOTW` |
| `runtime/aoc_romfs.c` | default DLC folder `extracted/<TOTK_GAME_DIR>/aoc` instead of the fixed `extracted/botw160/aoc` | the file is inside `#ifdef GAME_BOTW` |
| `runtime/audio_mixer.h` | two fields keep the voice-drop parameter | `#ifdef GAME_BOTW` |
| `runtime/audio_renderer.c` | `IAudioRenderer` commands 12 and 13 (Set/GetVoiceDropParameter) | `#ifdef GAME_BOTW` |
| `runtime/game_patches_botw.c` | the 1.9.0 real-time and held-count sites; the 1.6.0 sites are unchanged | `#ifdef GAME_BOTW160` / `#else`; only BOTW binaries compile this file |
| `runtime/game_version.h` | the 1.9.0 version block | `#elif defined(GAME_BOTW190)`, before the TOTK blocks |
| `runtime/loader.c` | the five-argument `nn::init::Start` of SDK 20.5; `module_finis` is compiled for 1.9.0 too | `#ifdef GAME_BOTW160` / `#elif defined(GAME_BOTW)`; `#ifndef GAME_BOTW` became `#ifndef GAME_BOTW160` |
| `runtime/shared_font.c` | `nn::pl::IsRunningOnOunce` answers no | `#ifdef GAME_BOTW` |
| `scripts/extract_exefs.py` | `--title` and `program_nca(..., title=)` | default title `0100F2C0115B6000` (TOTK); same checks and messages |
| `scripts/extract_romfs.py` | the IVFC loop moved into `verify_ivfc` | refactor; same checks, messages and report |
| `scripts/extract_update_romfs.py` | `--title` and `merge(..., title=)` | default title TOTK |
| `scripts/prepare_romfs_backend.py` | `botw190` in `--version` | new choice only |
| `scripts/prepare_runtime.py` | `botw190`: its `main` hash check and the DT_INIT shape of 1.4.3 | new choice; TOTK branches unchanged |
| `scripts/test_exclusive_windows.py` | `'botw190': []` in `WINDOWS` | additive |
| `scripts/verify_rewrites.py` | `botw190` in `--version`; an empty window list passes for it, as for `botw160` | new choice; TOTK checks unchanged |
| `third_party/d2r_gfx/gfx/nvn/hle_nvn.c` | 1.9.0 reports the TOTK NVN version; `BOTW_FAST_DISPLAY_*` hooks at the ends of three lines | `#if defined(GAME_BOTW) && !defined(GAME_BOTW190)`; the hooks are empty for TOTK |
| `third_party/d2r_gfx/gfx/nvn/nvn_mtl.m` | `BOTW_WB_*` hooks at the ends of existing lines, the staging bodies at the end of the file, `BOTW_MFXUP_KEY` in the scene test | bodies in `#ifdef GAME_BOTW`; for TOTK the hooks are empty and `WB_WAIT_GPU` is `mtl_finish_gpu()`, the call it replaces |
| `third_party/d2r_gfx/gfx/nvn/nvn_obj.h` | the `BOTW_WB_*` and `BOTW_FAST_DISPLAY_*` macros; `BOTW_MFXUP_KEY` for each version | inside the existing `#ifdef GAME_BOTW` / `#else`, empty in the `#else` |

Outside `games/totk`, `build_app.py` adds the 1.9.0 branch, `--dlc` and the package checks. For TOTK, `--dlc` stops the
build, and a TOTK DLC file given as the game file stops with a clearer message. The font functions take the TOTK pins
by default.

For TOTK 1.0.0, 1.1.2, 1.2.1 and 1.4.3, the preprocessed code (`clang -E -P`) of every changed C, Objective-C and
header file is identical to `e58b2bd`. The Python scripts keep the TOTK defaults, and the Makefile only adds rules.
TOTK itself was not re-tested in game on this branch.

## Open items

- Sinking legs: Link stands idle after a load of the 08:29 Great Plateau autosave, and his legs sink into the stone
  path by the ruin wall. One re-test on this branch showed the same as before. An earlier A/B showed that the write-back
  is not the cause: with the write-back off (`flag writeback 0`), the legs sank the same. On our earlier stack, one move
  or attack cleared it on the same spot. The cause is unknown.
- 25-minute run: RESULT_PENDING
- Checked in Unicorn only (the verify scripts), not in game: the held d-pad in the pause menu and the sprint landmark.
- Not tested:
  - TOTK in game
  - BOTW 1.6.0 in game (no 1.6.0 card image here). The write-back staging and the 120 Hz rule are under `GAME_BOTW`, so
    they act on 1.6.0 too: re-test it with the PR.
  - a cold start with an empty shader cache (every route run was warm)
  - iPhone and iPad (a BOTW build with `--platform ios` stops)
