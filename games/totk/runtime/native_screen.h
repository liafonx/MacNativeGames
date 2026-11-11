/* The main display's native pixel size (TOTK_OUTPUT_RES=screen): shared by game_patches_botw.c and game_patches_v143.c. */
#if TARGET_OS_IPHONE
static void native_screen_pixels(unsigned *w, unsigned *h) { *w = *h = 0; }   /* the phone never asks: output_parse stops first */
#else
#include <CoreGraphics/CoreGraphics.h>
static void native_screen_pixels(unsigned *w, unsigned *h) {   /* the main display's native mode, else its current mode */
    CGDirectDisplayID d = CGMainDisplayID(); *w = *h = 0;
    const void *key[] = {kCGDisplayShowDuplicateLowResolutionModes}, *val[] = {kCFBooleanTrue};
    CFDictionaryRef opt = CFDictionaryCreate(NULL, key, val, 1, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFArrayRef modes = CGDisplayCopyAllDisplayModes(d, opt);
    for (CFIndex i = 0; modes && i < CFArrayGetCount(modes); i++) {
        CGDisplayModeRef mode = (CGDisplayModeRef)CFArrayGetValueAtIndex(modes, i);
        unsigned mw = (unsigned)CGDisplayModeGetPixelWidth(mode), mh = (unsigned)CGDisplayModeGetPixelHeight(mode);
        if (CGDisplayModeGetIOFlags(mode) & 0x02000000u && (uint64_t)mw * mh > (uint64_t)*w * *h) { *w = mw; *h = mh; }   /* kDisplayModeNativeFlag */
    }
    if (modes) CFRelease(modes);
    if (opt) CFRelease(opt);
    if (!*w) { CGDisplayModeRef cur = CGDisplayCopyDisplayMode(d); if (cur) { *w = (unsigned)CGDisplayModeGetPixelWidth(cur); *h = (unsigned)CGDisplayModeGetPixelHeight(cur); CGDisplayModeRelease(cur); } }
}
#endif
