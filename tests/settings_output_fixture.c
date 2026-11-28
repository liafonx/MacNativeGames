/* Synthetic data page; the resolution and aspect ratio calls are the production code. */
#if defined(GAME_BOTW)
#include "../games/totk/runtime/game_patches_botw.c"
#elif defined(TOTK_GAME_V143)
#include "../games/totk/runtime/game_patches_v143.c"
#else
#include "../games/totk/runtime/game_patches.c"
#endif
#include <assert.h>
#include <limits.h>
#include <sys/stat.h>

#ifdef GAME_BOTW160
int vr_labo_enabled(void) { return 0; }   /* the probe links no zeldaVR; 1.6.0's output_parse asks it first (VR labo off) */
#endif
static _Atomic uint64_t page[8];
#ifdef GAME_BOTW
static uint8_t *testMain;
static uint8_t testCamera[0x100];
static _Atomic uint32_t testAspect;
#endif
void settings_test_output_init(void) {
    assert(runtime_output_size_current() == (1920ull | 1080ull << 32));
#ifdef GAME_BOTW
    /* Zeroed synthetic memory for the production canvas/static slots, never game data. */
    testMain = calloc(1, 0x2cceb18);
    assert(testMain);
#ifdef GAME_BOTW160
    g_out_mode = OUT_AUTO; g_out_w = 2560; g_out_h = 1440;
    g_out_main = testMain; g_out_page = page;
    output_page(1920, 1080);
#endif
#else
    output_w = 2560; output_h = 1440;
    atomic_store(&page[4], pack_size(1920, 1080));
    atomic_store(&output_page, (uint8_t *)page);
#endif
}
void settings_test_output_size(unsigned w, unsigned h) {
#ifdef GAME_BOTW160
    output_page(w, h);
    assert(runtime_output_size_current() == (w | (uint64_t)h << 32));
#elif defined(GAME_BOTW190)
    (void)w; (void)h;   /* This version's production canvas is fixed at 1080p. */
#else
    atomic_store(&page[4], pack_size(w, h));
    assert(runtime_output_size_current() == pack_size(w, h));
#endif
}
#ifdef GAME_BOTW
/* TOTK_OUTPUT_RES=screen through the production parser, on this machine's main display. Returns the modes the Resolution row
   then offers (runtime_output_modes), or -1 where the display has no 16:9 size above 1080p (a headless runner: nothing to check). Both Breath
   of the Wild versions take it: the largest size at the start, native = that size, 2K + MetalFX = 2560x1440. */
int settings_test_output_screen(unsigned *largest, unsigned *native_w, unsigned *mfx_w) {
    static _Atomic uint64_t screen_page[8];
    int mode = g_out_mode, big = g_out_start_big; unsigned w = g_out_w, h = g_out_h; _Atomic uint64_t *pg = g_out_page;
    char *old = getenv("TOTK_OUTPUT_RES"), *keep = old ? strdup(old) : NULL;
    setenv("TOTK_OUTPUT_RES", "screen", 1);
    g_out_mode = -1; g_out_start_big = 0; g_out_page = screen_page;
    output_parse();
    unsigned sw, sh, nh, mh; native_screen_pixels(&sw, &sh);
    int modes = native_k_from_screen(sw, sh) ? runtime_output_modes() : -1;
    *largest = g_out_w; *native_w = *mfx_w = 0;
    if (modes >= 0) { runtime_output_native(OUTPUT_MODE_NATIVE, native_w, &nh); runtime_output_native(OUTPUT_MODE_NATIVE_METALFX, mfx_w, &mh); }
    if (keep) setenv("TOTK_OUTPUT_RES", keep, 1); else unsetenv("TOTK_OUTPUT_RES");
    free(keep);
    g_out_mode = mode; g_out_start_big = big; g_out_w = w; g_out_h = h; g_out_page = pg;
    return modes;
}
#endif
/* A swapchain as if the app carried the 4K heap overlay, for the native-4K mode tests; restore before the rows matter. */
void settings_test_output_4k(int on) {
#ifndef GAME_BOTW
    if (on) { output_w = 3840; output_h = 2160; }
    else { output_w = 2560; output_h = 1440; }
#else
    (void)on;
#endif
}
/* This run's app as if built for a larger canvas (w x h: 2560x1440, 3840x2160 or 5120x2880): the render sizes the
   settings panel offers follow. Tears of the Kingdom: the swapchain's size (1.4.3's 4K + MetalFX to 5K follows it too,
   picture_4k_to_5k_available). Breath of the Wild: an automatic start (output_parse) with that largest canvas, the
   synthetic canvas page of settings_test_output_init (1.9.0's too, which is otherwise fixed at 1080p here). Its native
   4K / 5K also need the app's marker files (native_4k_app / native_5k_app), which production reads once and keeps: the
   first cap at 4K or 5K shows them in a synthetic app folder for that read, and from then on the largest canvas alone
   decides. Markers already read as a 2K app's fail here instead of leaving the test on a 2K app. */
#ifdef GAME_BOTW
static void test_app_markers(unsigned w) {
    char app[PATH_MAX], dir[PATH_MAX], file[PATH_MAX], cwd[PATH_MAX];
    snprintf(app, sizeof app, "%s/botw-app-XXXXXX", getenv("TMPDIR") ? getenv("TMPDIR") : "/tmp");
    assert(mkdtemp(app) && getcwd(cwd, sizeof cwd));
    snprintf(dir, sizeof dir, "%s/extracted", app); assert(!mkdir(dir, 0700));
    snprintf(dir, sizeof dir, "%s/extracted/" TOTK_GAME_DIR, app); assert(!mkdir(dir, 0700));
    const char *names[2] = {"native-4k", "native-5k"};
    for (int i = 0; i < 1 + (w >= 5120); i++) {
        snprintf(file, sizeof file, "%s/%s", dir, names[i]);
        FILE *f = fopen(file, "w"); assert(f); fclose(f);
    }
    assert(!chdir(app));   /* production reads extracted/<game>/native-4k relative to the app's folder */
    assert(native_4k_app() && native_5k_app() == (w >= 5120));
    assert(!chdir(cwd));
    for (int i = 0; i < 1 + (w >= 5120); i++) { snprintf(file, sizeof file, "%s/%s", dir, names[i]); unlink(file); }
    rmdir(dir); snprintf(dir, sizeof dir, "%s/extracted", app); rmdir(dir); rmdir(app);
}
#endif
void settings_test_output_cap(unsigned w, unsigned h) {
#ifndef GAME_BOTW
    output_w = w; output_h = h;
#else
    static int markers;
    if (w >= 3840 && !markers) { test_app_markers(w); markers = 1; }
    g_out_mode = OUT_AUTO; g_out_w = w; g_out_h = h;
    g_out_main = testMain; g_out_page = page;
    if (!atomic_load(&g_out_cur)) output_page(1920, 1080);   /* an automatic start's first canvas (patch_output_res) */
#endif
}
#ifdef GAME_BOTW
/* The game's first present (runtime_frame_step_update): botw_output_tick, once, with 1.9.0's start rule for MetalFX. */
void settings_test_output_first_present(void) { botw_output_tick(); }
#ifndef GAME_BOTW160
/* The tick's keep-or-drop test for MetalFX, which botw_output_tick applies once (the 4K check there has used it up). */
int settings_test_output_mfx_canvas(void) { return output_mfx_canvas(); }
#endif
#endif
/* The aspect ratio as runtime/aspect_patch.h sets it (production code), with a synthetic data-page slot and a window of
   ratio view instead of the stubs aspect_install writes into the game. */
void settings_test_aspect_init(double view) {
#ifdef GAME_BOTW
    g_uw_main = testMain; g_uw_word = &testAspect; g_uw_wide = (float)ASPECT_WIDE;
    uint64_t camera = (uintptr_t)testCamera, vtable = (uintptr_t)testMain + UW_PERSPECTIVE_VTABLE;
    memcpy(testMain + UW_CAMERA_SINGLETON, &camera, 8);
    memcpy(testCamera + 0x38, &vtable, 8);
#else
    static _Atomic(uint32_t) slot;
    g_aspect_wide = (float)ASPECT_WIDE;
    atomic_store(&g_aspect_step, ASPECT_STEP_GAME);
    g_aspect_slot = &slot;
#endif
    runtime_aspect_view(view * 1080, 1080);
}

#ifdef GAME_BOTW   /* both versions have the field of view's row */
/* The field of view's row as it is when patch_fov found the camera code not as shipped (it never runs here). */
void settings_test_fov_refused(int refused) { g_fov_refused = refused; }
/* The factor's word as the stub reads it, on a synthetic data-page slot (patch_fov never runs here). */
static _Atomic uint32_t testFovFactor;
void settings_test_fov_init(void) { g_fov_factor = &testFovFactor; }
uint32_t settings_test_fov_word(void) { return atomic_load(&testFovFactor); }
/* The model detail distance's (0), render distance's (1) and depth of field's (2) rows as they are when patch_view found the game code not as shipped. */
void settings_test_view_refused(int which, int refused) { if (which == 0) g_lod_refused = refused; else if (which == 1) g_disp_refused = refused; else g_dof_refused = refused; }
#endif
void settings_test_aspect_frame(void) {
#ifdef GAME_BOTW
    if (!g_uw_main) return;
    /* The synthetic camera consumes the ratio word as the game's patched writer does. */
    uint32_t bits = atomic_load(&testAspect);
    float ratio = (float)ASPECT_GAME;
    if (bits) memcpy(&ratio, &bits, 4);
    memcpy(testCamera + 0xe8, &ratio, 4);
    botw_aspect_apply();
#endif
}
