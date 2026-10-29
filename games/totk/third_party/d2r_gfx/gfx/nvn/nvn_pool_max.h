/* The zero-copy pool limit of nvn_mtl.m (pool_range_check): a header, so the shared file keeps its line numbers. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
/* TOTK_DRAM_8GB (the 8 GB pool for big texture packs, whose TextureHeap pool is ~3.8 GiB): 4 GiB. Read once. */
static uint64_t pool_buffer_max(void) {
    static uint64_t v;
    if (!v) { const char *e = getenv("TOTK_DRAM_8GB"); v = e && *e && strcmp(e, "0") ? 4ull << 30 : 1ull << 30; }
    return v;
}
#ifdef GAME_BOTW   /* both versions: the 1.6.0 graphics objects are built with GAME_BOTW alone */
/* Breath of the Wild 1.9.0 and 1.6.0 with TOTK_BOTW_HD_HEAPS=1 (game_patches_botw.c): the HD texture pack's 2032 MiB texture
   compaction heap is one pool, so the limit is 2 GiB there (the owner, 2026-10-24; Bayonetta 3's layer runs at 2 GiB too). */
int runtime_botw_hd_heaps(void);
#define POOL_BUFFER_MAX (runtime_botw_hd_heaps() ? 2ull << 30 : pool_buffer_max())
#else
#define POOL_BUFFER_MAX pool_buffer_max()
#endif
