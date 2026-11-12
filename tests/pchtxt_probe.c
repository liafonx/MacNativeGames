/* TOTK_PCHTXT as the runtime applies it (runtime/pchtxt.h through patch_pchtxt in game_patches.c, or in
   game_patches_botw.c when built with -DGAME_BOTW190 or -DGAME_BOTW160, in game_patches_v143.c with -DTOTK_GAME_V143=1, the production code) on a synthetic main: prints the 16 bytes at 0x40 after the patch, or
   "FAIL <reason>" with exit code 3 when the start would stop.
   Run from a folder holding extracted/v0/exefs/main (extracted/botw190/exefs/main for GAME_BOTW190, extracted/v143/exefs/main for TOTK_GAME_V143), the build id at
   0x40, with TOTK_PCHTXT set. The synthetic main is 0x3000000 bytes (PROBE_SIZE), so it holds every Breath of the Wild site the reader
   refuses. */
#if defined(GAME_BOTW190) || defined(GAME_BOTW160)
#include "../games/totk/runtime/game_patches_botw.c"
#elif defined(TOTK_GAME_V143)
#include "../games/totk/runtime/game_patches_v143.c"
#else
#include "../games/totk/runtime/game_patches.c"
#endif
#include <stdio.h>
#include <stdlib.h>

_Noreturn void runtime_fail(const char *message) { printf("FAIL %s\n", message); exit(3); }

int main(void) {
#ifndef PROBE_SIZE
#define PROBE_SIZE 0x3000000   /* TOTK 1.4.3 sites reach 0x3add238: its test builds with a larger main */
#endif
    enum { SIZE = PROBE_SIZE };
    uint8_t *image = calloc(1, SIZE);
    if (!image) return 2;
    Module m = {0};
    m.name = "main"; m.base = image; m.file_size = m.size = SIZE;
    patch_pchtxt(&m);
    for (int i = 0; i < 16; i++) printf("%02x", image[0x40 + i]);
    printf("\n");
    return 0;
}
