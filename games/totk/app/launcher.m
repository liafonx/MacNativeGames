// The launcher inside the packaged app. It is part of the game binary, which build_app.py places as Contents/MacOS/TOTK
// (and, for tools, as Contents/Resources/totk/build/totk-native): started from Contents/MacOS, runtime/loader.c main runs
// runtime_app_launch first and then boots the game in the same process. One process on purpose: an app that execs another
// binary after it started is left out of the Cmd+Tab switcher (macOS 27; the Dock still shows it).
// Everything the game writes goes to the player's own folders:
//   ~/Library/Application Support/MacNativeGames/TOTK/   profile (account, saves), shader cache, game slot, RomFS proof (BotW/ for Breath of the Wild)
//   ~/Library/Logs/MacNativeGames/totk.log             this run, only with the debug log on (totk.previous.log: the one before; botw.log for Breath of the Wild)
// If the game is already running, the launcher only brings it to the front.
#import <AppKit/AppKit.h>
#import <CommonCrypto/CommonDigest.h>
#import <GameController/GameController.h>
#include <IOKit/hid/IOHIDManager.h>
#include <IOKit/hid/IOHIDUsageTables.h>
#include <errno.h>
#include <fcntl.h>
#include <libproc.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/event.h>
#include <sys/file.h>
#include <sys/proc.h>
#include <sys/stat.h>
#include <unistd.h>
#include "../third_party/d2r_gfx/gfx/nvn/dmsh_chain.h"
#include "../runtime/game_version.h"
#include "../runtime/controller_map.h"
#include "../runtime/gamepad_layout.h"   // gamepad_icon_style: the button icons for key 6's type and the connected controller
#include "../runtime/runtime.h"
#include "../third_party/d2r_gfx/gfx/nvn/include/totk_ui_lang.h"
#include "../third_party/d2r_gfx/gfx/nvn/totk_shader_contrib.h"
#include "../third_party/d2r_gfx/gfx/nvn/totk_contrib_upload.h"
#ifdef TOTK_GAME_V143
#include "settings_txt.h"   /* <data>/settings.txt, TOTK 1.4.3 local build only */
#endif

static void show_error(NSString *detail) {
    NSAlert *alert = [NSAlert new];
    alert.messageText = @"The game could not start";
    alert.informativeText = detail;
    [alert runModal];
}

#ifdef GAME_BOTW
/* Whether a game controller is connected: a HID joystick / gamepad (DualSense, Pro Controller, Xbox over Bluetooth),
   or one the GameController framework adopts (Xbox over USB), which it reports a moment after the first run loop turns. */
static BOOL controller_connected(void) {
    IOHIDManagerRef hid = IOHIDManagerCreate(kCFAllocatorDefault, kIOHIDOptionsTypeNone);
    NSMutableArray *matching = [NSMutableArray array];
    for (NSNumber *usage in @[@(kHIDUsage_GD_Joystick), @(kHIDUsage_GD_GamePad), @(kHIDUsage_GD_MultiAxisController)])
        [matching addObject:@{@kIOHIDDeviceUsagePageKey: @(kHIDPage_GenericDesktop), @kIOHIDDeviceUsageKey: usage}];
    IOHIDManagerSetDeviceMatchingMultiple(hid, (__bridge CFArrayRef)matching);
    CFSetRef devices = IOHIDManagerCopyDevices(hid);
    BOOL found = devices && CFSetGetCount(devices) > 0;
    if (devices) CFRelease(devices);
    CFRelease(hid);
    for (int i = 0; !found && i < 10; i++) {
        for (GCController *controller in GCController.controllers)
            if (controller.extendedGamepad) found = YES;
        if (!found) [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
    }
    return found;
}
#endif

static NSDictionary<NSString *, NSString *> *kv_read(NSString *path) {
    NSMutableDictionary *out = [NSMutableDictionary dictionary];
    NSString *text = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil];
    for (NSString *line in [text componentsSeparatedByString:@"\n"]) {
        NSRange eq = [line rangeOfString:@"="];
        if (eq.location == NSNotFound) continue;
        NSString *key = [line substringToIndex:eq.location];
        if (!out[key]) out[key] = [line substringFromIndex:eq.location + 1];
    }
    return out;
}

#if defined(GAME_BOTW190) || defined(GAME_BOTW160)
/* HD texture packs (runtime/game_patches_botw.c runtime_botw_hd_heaps): the bytes the player's mod manifest replaces, and
   the size of its Pack/TitleBG.pack. HDPacks 4k replaces 19.7 GiB, its TitleBG.pack 346 MiB (the shipped StarterPackMgr
   heap is 225 MiB); the largest other mod seen, 0.24 GiB. */
static unsigned long long mod_bytes(const char *mods, unsigned long long *title_bg) {
    char path[4096]; snprintf(path, sizeof path, "%s/manifest.tsv", mods);
    *title_bg = 0;
    FILE *m = fopen(path, "r"); if (!m) return 0;
    unsigned long long total = 0, size; char line[8192], name[4096];
    while (fgets(line, sizeof line, m))
        if (line[0] != '#' && sscanf(line, "%4095[^\t]\t%llu", name, &size) == 2) {
            total += size;
            if (!strcmp(name, "Pack/TitleBG.pack")) *title_bg = size;
        }
    fclose(m);
    return total;
}
#endif

static uint64_t pid_start(pid_t pid) {   // 0 if gone or a zombie; with the pid it names one process exactly
    struct proc_bsdinfo bi;
    if (pid <= 0 || proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &bi, sizeof bi) != (int)sizeof bi || bi.pbi_status == SZOMB) return 0;
    return bi.pbi_start_tvsec;
}

/* The game slot (runtime/game_slot.c): the running game holds an exclusive lock on <slot>/lock and names itself in
   <slot>/holder. Returns the running game's pid, or 0. */
static pid_t running_game(NSString *slot) {
    int fd = open([slot stringByAppendingPathComponent:@"lock"].fileSystemRepresentation, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    int held = flock(fd, LOCK_EX | LOCK_NB) != 0;
    close(fd);
    if (!held) return 0;
    NSDictionary *h = kv_read([slot stringByAppendingPathComponent:@"holder"]);
    /* A player launch must reach runtime/game_slot.c to preempt an agent's
       test. Only an existing player game (or another test for a test request)
       is brought forward here. */
    const char *requestOwner = getenv("TOTK_SLOT_OWNER");
    if (![h[@"owner"] isEqualToString:@"user"] &&
        (!requestOwner || !*requestOwner || !strcmp(requestOwner, "user"))) return 0;
    pid_t pid = [h[@"game_pid"] intValue];
    return pid > 0 && pid_start(pid) == (uint64_t)[h[@"game_start"] longLongValue] ? pid : 0;
}

static void note(NSString *text) {
    NSAlert *alert = [NSAlert new];
    alert.messageText = text;
    [alert runModal];
}

/* Saves sync (runtime/cloud_save.m): a start with no sync folder offers to choose one, before the game reads its save, so
   it syncs from this start. "Not now" asks again at the next start; "Don't ask again" (or Stop Syncing in the app menu,
   runtime/frontend.m) never. Returns the folder chosen now, or nil. */
static NSString *cloud_folder_offer(NSUserDefaults *sync) {
    if ([sync boolForKey:@"TOTK_CLOUD_SAVE_ASKED"]) return nil;
    [NSApplication sharedApplication]; [NSApp activateIgnoringOtherApps:YES];
    NSAlert *alert = [NSAlert new];
    alert.messageText = @(TOTK_UI("要和其他设备同步存档吗？", "Sync saves with your other devices?"));
    alert.informativeText = @(TOTK_UI("选择一个每台设备都能看到的文件夹，例如 iCloud 云盘里的文件夹（也可以是和另一个 Apple ID 共享的文件夹）。存档仍保存在这台 Mac 上，只把副本放进这个文件夹，其他设备在那里接续。以后也可以在应用菜单 › 存档同步里更改。",
                                      "Choose a folder every device can see, such as a folder in iCloud Drive (one shared with another Apple ID works too). Saves stay on this Mac; a copy goes to the folder, and your other devices carry on from there. You can change it later in the app menu › Save Sync."));
    [alert addButtonWithTitle:@(TOTK_UI("选择文件夹…", "Choose Folder…"))];
    [alert addButtonWithTitle:@(TOTK_UI("以后再说", "Not Now"))];
    [alert addButtonWithTitle:@(TOTK_UI("不要再问", "Don't Ask Again"))];
    NSModalResponse choice = [alert runModal];
    if (choice == NSAlertThirdButtonReturn) [sync setBool:YES forKey:@"TOTK_CLOUD_SAVE_ASKED"];
    if (choice != NSAlertFirstButtonReturn) return nil;
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseFiles = NO; panel.canChooseDirectories = YES; panel.canCreateDirectories = YES; panel.allowsMultipleSelection = NO;
    panel.message = @(TOTK_UI("选择每台设备都能看到的文件夹（例如 iCloud 云盘里共享的文件夹）。", "Choose a folder every device can see (e.g. a shared folder in iCloud Drive)."));
    panel.prompt = @(TOTK_UI("选择", "Choose"));
    panel.directoryURL = [NSURL fileURLWithPath:[NSHomeDirectory() stringByAppendingPathComponent:@"Library/Mobile Documents/com~apple~CloudDocs"] isDirectory:YES];
    if ([panel runModal] != NSModalResponseOK || !panel.URL) return nil;   // cancelled: asked again at the next start
    [sync setObject:panel.URL.path forKey:@"TOTK_CLOUD_SAVE_FOLDER"];
    return panel.URL.path;
}

/* The one question about the shader contribution (totk_contrib_upload.h, the same on iPhone / iPad), before the game
   starts, at the first start where the player has not chosen yet (no row threshold, user 2026-11-10). */
static void contrib_ask(NSString *path) {
    NSUInteger rows = totk_inbox_should_ask(path);
    if (!rows) return;
    [NSApplication sharedApplication]; [NSApp activateIgnoringOtherApps:YES];
    NSAlert *alert = [NSAlert new];
    alert.messageText = totk_inbox_prompt_title();
    alert.informativeText = totk_inbox_prompt_text();
    for (NSString *answer in totk_inbox_prompt_answers()) [alert addButtonWithTitle:answer];
    NSModalResponse choice = [alert runModal];
    totk_inbox_answer(path, choice == NSAlertFirstButtonReturn ? TOTK_INBOX_AUTO : TOTK_INBOX_NEVER);
}

/* The contribution file: in the project folder the app was built from, <clone>/shader-list-contribution/shader-list-contribution.txt
   (MNGContributionFolder in Info.plist, build_app.py), where players look and where the iPhone / iPad copies go too; the
   data folder when that folder is gone (user 2026-10-22: nobody found the file in ~/Library). The file that was in the data
   folder moves there once, with its reminder state. */
static NSString *contrib_file(NSString *data) {
    NSFileManager *fm = NSFileManager.defaultManager;
    NSString *old = [data stringByAppendingPathComponent:@"shader-list-contribution.txt"];
    NSString *folder = [NSBundle.mainBundle objectForInfoDictionaryKey:@"MNGContributionFolder"];
    if (![folder isKindOfClass:NSString.class] || !folder.length || ![fm fileExistsAtPath:folder.stringByDeletingLastPathComponent]) return old;
    if (![fm createDirectoryAtPath:folder withIntermediateDirectories:YES attributes:nil error:nil]) return old;
    // the Mac's file (an iPhone / iPad contributes its own, totk_contrib_upload.h). Each game its own file: Tears of the
    // Kingdom's without a suffix, Breath of the Wild's -botw, Bananza's and Pokopia's -<TOTK_GAME_DIR>, as Ocarina of Time's
    // launcher. Until 2026-11-12 every game but Tears of the Kingdom wrote -botw's, so one file held Breath of the Wild's and
    // Pokopia's rows and whichever app sent it named its own game for both (the inbox: 464 Pokopia rows sent as Breath of
    // the Wild 1.6.0's).
    NSString *mine = [folder stringByAppendingPathComponent:!strcmp(GAME_FOLDER, "TOTK") ? @"shader-list-contribution.txt"
                      : !strcmp(GAME_FOLDER, "BotW") ? @"shader-list-contribution-botw.txt"
                      : [NSString stringWithFormat:@"shader-list-contribution-%s.txt", TOTK_GAME_DIR]];
    if (![fm fileExistsAtPath:mine] && [fm fileExistsAtPath:old]) {
        [fm moveItemAtPath:old toPath:mine error:nil];
        NSString *state = [old stringByAppendingString:@".reminded"];   // where the state was kept before (totk_contrib_state)
        if ([fm fileExistsAtPath:state]) [fm moveItemAtPath:state toPath:totk_contrib_state(mine) error:nil];
    }
    return mine;
}

static BOOL make_dir(NSString *path) {
    return [NSFileManager.defaultManager createDirectoryAtPath:path withIntermediateDirectories:YES attributes:nil error:nil];
}

#ifdef GAME_BOTW
/* A path in kit.txt (python=, pythonpath=): absolute, or relative to the app (build_app.py --bundle-python), which then holds its own Python. */
static NSString *kit_path(NSString *value) {
    return value.isAbsolutePath ? value : [NSBundle.mainBundle.bundlePath stringByAppendingPathComponent:value];
}

#endif
/* Button icons (docs/mods.md, Button prompts): an app built with them (build_app.py: button-icons/kit.txt) draws the game's
   prompts for the controller in use. The style comes from key 6's type in controller-type.txt, or for auto from the
   controller connected now, else the last one the game saw (controller-detected.txt, runtime/gamepad.m); the face mode
   and controls.txt as the game maps them; the size from the main display (gamepad_icon_scale: 2x for 3456x2234). Switch
   icons are drawn only at 2x or more (only the Breath of the Wild tool draws them); at 1x the game keeps its own. Each style,
   face mode, size and controls.txt gets its own folder of RomFS files under
   <controls folder>/button-icons/<version>/<key>, made once by the bundled tool from this app's RomFS (seconds) and
   then reused; runtime/romfs.c loads it like a mod (TOTK_ROMFS_BUTTON_ICONS). The game reads it at start only, so a
   new pick shows from the next start. Any failure starts the game with its own Switch icons. Returns the style word for
   TOTK_BUTTON_ICONS, and *folder the folder to load or nil. */
static NSString *button_icons(NSString *root, NSString *controls, NSString **folder) {
    *folder = nil;
    NSString *kit = [root stringByAppendingPathComponent:@"button-icons"];
    NSDictionary *info = kv_read([kit stringByAppendingPathComponent:@"kit.txt"]);
    if (!info[@"python"] || !info[@"tool"] || !info[@"id"]) return nil;
    NSString *settings = controls.stringByDeletingLastPathComponent;
    NSString *(^word)(NSString *) = ^NSString *(NSString *name) {
        NSString *text = [NSString stringWithContentsOfFile:[settings stringByAppendingPathComponent:name] encoding:NSUTF8StringEncoding error:nil];
        return text ?: @""; };
    int type = gamepad_type_parse(word(@"controller-type.txt").UTF8String);
    if (type < 0) type = GAMEPAD_TYPE_AUTO;
#ifdef GAME_BOTW
    /* The keyboard's key caps (botw_prompts.py --style keyboard): picked by TOTK_BUTTON_ICONS_STYLE=keyboard or controller-type.txt, or
       auto with no controller connected now and none seen before. A picked one skips the controller check (type is then a
       stand-in that is not auto); a connected or remembered controller keeps its own style. */
    BOOL keyboard = gamepad_icon_keyboard_picked(getenv("TOTK_BUTTON_ICONS_STYLE"), word(@"controller-type.txt").UTF8String);
    if (keyboard) type = GAMEPAD_TYPE_XBOX;
    BOOL pad_now = NO;
#endif
    int detected = -1;
    if (type == GAMEPAD_TYPE_AUTO) {
        /* controllers macOS already knows show up within a few run loop turns */
        for (int i = 0; i < 5 && detected < 0; i++) {
            for (GCController *c in GCController.controllers)
                if (c.extendedGamepad) { detected = gamepad_type_detect(c.productCategory.UTF8String, c.vendorName.UTF8String); break; }
            if (detected < 0) [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.1]];
        }
#ifdef GAME_BOTW
        pad_now = detected >= 0 || controller_connected();
#endif
        if (detected < 0) detected = gamepad_type_parse(word(@"controller-detected.txt").UTF8String);
#ifndef GAME_BOTW
        if (detected < 0) detected = GAMEPAD_TYPE_XBOX;     /* no pad seen yet: Xbox prompts */
#else
        keyboard = gamepad_icon_keyboard_auto(type, pad_now, detected);
#endif
    }
    NSScreen *screen = NSScreen.mainScreen;     /* sharper icons for a bigger display: the game's size times this */
    CGFloat backing = screen ? screen.backingScaleFactor : 1;
    int scale = screen && [info[@"tool"] hasSuffix:@"botw_prompts.py"]
        ? gamepad_icon_scale(screen.frame.size.width * backing, screen.frame.size.height * backing) : 1;
    setenv("TOTK_BUTTON_ICONS_SCALE", @(scale).stringValue.UTF8String, 1);   /* runtime/gamepad.m logs the next start's pick with it */
    int by_position = 1, style = gamepad_icon_style(type, detected, scale, &by_position);
#ifdef GAME_BOTW
    if (keyboard) style = GAMEPAD_ICON_KEYBOARD;
#endif
    if (style < 0) return @"none";
    NSString *name = [NSString stringWithFormat:@"%s-%s", gamepad_type_name(style), by_position ? "position" : "label"];
#ifdef GAME_BOTW
    if (keyboard) { name = @"keyboard"; by_position = 1; }   /* the keys are the player's, not a face mode */
    NSString *bindings = [settings stringByAppendingPathComponent:@"keyboard.txt"];   /* the player's keys (runtime/input.c) */
    if (!keyboard || ![NSFileManager.defaultManager fileExistsAtPath:bindings]) bindings = nil;
    /* Mouse mode (mouse.txt, off without it: runtime/gamepad.m load_mouse_mode): off, a cap shows the next key bound, not a mouse button. */
    BOOL mouse = [[word(@"mouse.txt") stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet] isEqualToString:@"on"];
#endif
    NSData *layout = [NSData dataWithContentsOfFile:controls];
    NSMutableData *keyed = [[[NSString stringWithFormat:@"%@\n%@\n%d\n", info[@"id"], name, scale] dataUsingEncoding:NSUTF8StringEncoding] mutableCopy];
    if (layout) [keyed appendData:layout];
#ifdef GAME_BOTW
    if (bindings) [keyed appendData:[NSData dataWithContentsOfFile:bindings] ?: NSData.data];   /* other keys, other caps */
    if (keyboard) [keyed appendData:[(mouse ? @"\nmouse=on\n" : @"\nmouse=off\n") dataUsingEncoding:NSUTF8StringEncoding]];
    /* A mod with its own resource size table (an HD texture pack): the tool raises that table (botw_prompts.py --mods), so a
       new table of the mods makes a new set at 2x or more. TOTK_ROMFS_MODS is set before this runs. */
    const char *mods = getenv("TOTK_ROMFS_MODS");
    NSString *modsFolder = scale > 1 && mods && *mods && strcmp(mods, "0") ? @(mods) : nil;   /* at 1x the set has no table */
    for (NSString *line in [[NSString stringWithContentsOfFile:[modsFolder stringByAppendingPathComponent:@"manifest.tsv"] encoding:NSUTF8StringEncoding error:nil] componentsSeparatedByString:@"\n"])
        if ([line hasPrefix:@"System/Resource/ResourceSizeTable.product.srsizetable\t"]) [keyed appendData:[line dataUsingEncoding:NSUTF8StringEncoding]];
#else
    /* The HD texture pack (runtime/romfs.c, TOTK_TEXTURE_PACK=1) replaces the resource size table too: the tool grows the
       pack's table (--texture-pack), and its manifest line keys the set, so pack on, off or changed each get their own set
       (a set made on the pack's table cannot load without the pack). */
    NSString *pack = nil;
    const char *packOn = getenv("TOTK_TEXTURE_PACK"), *packDir = getenv("TOTK_TEXTURE_PACK_DIR");
    if (packOn && !strcmp(packOn, "1")) {
        pack = packDir ? @(packDir) : [word(@"texture-pack-path.txt") stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        BOOL table = NO;
        for (NSString *line in [[NSString stringWithContentsOfFile:[pack stringByAppendingPathComponent:@"manifest.tsv"] encoding:NSUTF8StringEncoding error:nil] componentsSeparatedByString:@"\n"])
            if ([line hasPrefix:@"System/Resource/ResourceSizeTable."]) { [keyed appendData:[line dataUsingEncoding:NSUTF8StringEncoding]]; table = YES; }
        if (!table) pack = nil;
    }
#endif
    uint8_t digest[CC_SHA256_DIGEST_LENGTH]; CC_SHA256(keyed.bytes, (CC_LONG)keyed.length, digest);
    NSMutableString *key = [NSMutableString string];
    for (int i = 0; i < 8; i++) [key appendFormat:@"%02x", digest[i]];
    NSString *cache = [settings stringByAppendingPathComponent:@"button-icons/" TOTK_GAME_DIR];
    NSString *target = [cache stringByAppendingPathComponent:key];
    NSFileManager *fm = NSFileManager.defaultManager;
    if (![fm fileExistsAtPath:[target stringByAppendingPathComponent:@"manifest.tsv"]]) {
        NSString *temporary = [target stringByAppendingFormat:@".tmp-%d", getpid()];
        [fm removeItemAtPath:temporary error:nil];
        if (![fm createDirectoryAtPath:temporary withIntermediateDirectories:YES attributes:nil error:nil]) return @"none";
        NSMutableArray *arguments = [@[@"-B", [kit stringByAppendingPathComponent:info[@"tool"]], temporary,   /* -B: nothing written into the signed app */ @"--romfs", @"extracted/" TOTK_GAME_DIR "/romfs",
                                       @"--version", @TOTK_GAME_DIR, @"--style", @(gamepad_type_name(style)), @"--face-buttons", by_position ? @"position" : @"label"] mutableCopy];
        if ([info[@"tool"] hasSuffix:@"botw_prompts.py"])      /* only the Breath of the Wild tool draws at a scale */
            [arguments addObjectsFromArray:@[@"--scale", @(scale).stringValue]];
        if ([info[@"tool"] hasSuffix:@"make_button_icons.py"]) [arguments addObject:@"--our-art"];   /* the Breath of the Wild art on the game's own picture boxes */
#ifdef GAME_BOTW
        if (modsFolder && [info[@"tool"] hasSuffix:@"botw_prompts.py"]) [arguments addObjectsFromArray:@[@"--mods", modsFolder]];
#else
        if (pack) [arguments addObjectsFromArray:@[@"--texture-pack", pack]];
#endif
        if (layout) [arguments addObjectsFromArray:@[@"--controls", controls]];
#ifdef GAME_BOTW
        if (bindings) [arguments addObjectsFromArray:@[@"--bindings", bindings]];
        if (keyboard) [arguments addObject:mouse ? @"--mouse" : @"--no-mouse"];
#endif
        fprintf(stderr, "[launcher] button icons: making %s (%s, %dx) in %s\n", name.UTF8String, layout ? "controls.txt" : "default layout", scale, target.fileSystemRepresentation);
        NSTask *task = [NSTask new];
#ifdef GAME_BOTW
        task.executableURL = [NSURL fileURLWithPath:kit_path(info[@"python"])];
        NSString *site = info[@"pythonpath"];
        if (site.length) {   /* the fontTools folder of an app that carries its own Python */
            NSMutableDictionary *environment = [NSProcessInfo.processInfo.environment mutableCopy];
            environment[@"PYTHONPATH"] = kit_path(site);
            task.environment = environment;
        }
#else
        task.executableURL = [NSURL fileURLWithPath:info[@"python"]];
#endif
        task.arguments = arguments;
        NSDate *start = NSDate.date;
        NSError *error = nil;
        BOOL ok = [task launchAndReturnError:&error];
        if (ok) { [task waitUntilExit]; ok = task.terminationStatus == 0; }
        if (!ok || ![fm moveItemAtPath:temporary toPath:target error:&error]) {
            fprintf(stderr, "[launcher] button icons: could not make them (%s); the game's own icons this time\n",
                    error ? error.localizedDescription.UTF8String : "the tool failed, see above");
            [fm removeItemAtPath:temporary error:nil];
            return @"none";
        }
        fprintf(stderr, "[launcher] button icons: made in %.1f s\n", -start.timeIntervalSinceNow);
        /* keep the four newest sets (a few tens of MB each) */
        NSArray<NSURL *> *sets = [fm contentsOfDirectoryAtURL:[NSURL fileURLWithPath:cache] includingPropertiesForKeys:@[NSURLContentModificationDateKey] options:0 error:nil];
        sets = [sets sortedArrayUsingComparator:^NSComparisonResult(NSURL *a, NSURL *b) {
            NSDate *x = nil, *y = nil;
            [a getResourceValue:&x forKey:NSURLContentModificationDateKey error:nil]; [b getResourceValue:&y forKey:NSURLContentModificationDateKey error:nil];
            return [y compare:x ?: NSDate.distantPast]; }];
        for (NSUInteger i = 4; i < sets.count; i++) [fm removeItemAtURL:sets[i] error:nil];
    } else {
        fprintf(stderr, "[launcher] button icons: %s %dx (%s)\n", name.UTF8String, scale, target.fileSystemRepresentation);
        [fm setAttributes:@{NSFileModificationDate: NSDate.date} ofItemAtPath:target error:nil];   /* newest for the four kept */
    }
    *folder = target;
    return name;
}

static BOOL path_is_inside_bundle(const char *path, const char *bundle) {
    size_t len = strlen(bundle);
    return !strncmp(path, bundle, len) && (!path[len] || path[len] == '/');
}

static BOOL prepare_external_save(BOOL *active, NSString **errorOut) {
    *active = NO;
    const char *raw = getenv("DM_SAVE");
    if (!raw || !*raw) return YES;
    if (raw[0] != '/') {
        if (errorOut) *errorOut = @"DM_SAVE must be an absolute directory path.";
        return NO;
    }
    char normalized[1024];
    if (snprintf(normalized, sizeof normalized, "%s", raw) >= 1016) {
        if (errorOut) *errorOut = @"DM_SAVE path is too long.";
        return NO;
    }
    size_t len = strlen(normalized);
    while (len > 1 && normalized[len - 1] == '/') normalized[--len] = 0;
    for (const char *p = normalized + 1; *p;) {
        const char *slash = strchr(p, '/');
        size_t seg = slash ? (size_t)(slash - p) : strlen(p);
        if (seg == 2 && p[0] == '.' && p[1] == '.') {
            if (errorOut) *errorOut = @"DM_SAVE must not contain '..' path segments.";
            return NO;
        }
        p = slash ? slash + 1 : p + seg;
    }
    char bundleResolved[PATH_MAX];
    const char *bundlePath = NSBundle.mainBundle.bundlePath.fileSystemRepresentation;
    const char *bundle = (bundlePath && realpath(bundlePath, bundleResolved)) ? bundleResolved : bundlePath;
    if (bundle && path_is_inside_bundle(normalized, bundle)) {
        if (errorOut) *errorOut = @"DM_SAVE must be outside the app bundle.";
        return NO;
    }
    char probe[1024], resolved[PATH_MAX];
    memcpy(probe, normalized, len + 1);
    for (;;) {
        if (realpath(probe, resolved)) {
            if (bundle && path_is_inside_bundle(resolved, bundle)) {
                if (errorOut) *errorOut = @"DM_SAVE must be outside the app bundle.";
                return NO;
            }
            break;
        }
        char *slash = strrchr(probe, '/');
        if (!slash || slash == probe) break;
        *slash = 0;
    }
    setenv("DM_SAVE", normalized, 1);
    *active = YES;
    return YES;
}

/* build_app.py --mac-game-data library: the app carries no RomFS image. A folder holds it when its romfs.bin (in it, or in
   <folder>/<version>) has the size the build pinned (romfs-config.txt) and a romfs.sha256 beside it, if any, names the
   pinned image: the build copies to romfs.bin.partial first, so a romfs.bin of the full size is a complete copy. Its hash
   is checked by the runtime at the first start (TOTK_ROMFS_PROOF_DIR). Returns the folder of the image, or nil. */
static NSString *game_data_in(NSString *folder, unsigned long long size, NSString *sha) {
    if (!folder.length) return nil;
    for (NSString *place in @[[folder stringByAppendingPathComponent:@TOTK_GAME_DIR], folder]) {
        /* Only the size is needed (every version). Foundation also queries extended attributes, which can block on a
           large image on a removable volume before any UI (after PR #1473). */
        struct stat image;
        if (stat([place stringByAppendingPathComponent:@"romfs.bin"].fileSystemRepresentation, &image) ||
            !S_ISREG(image.st_mode) || image.st_size < 0 || (uint64_t)image.st_size != size) continue;
        NSString *mark = [NSString stringWithContentsOfFile:[place stringByAppendingPathComponent:@"romfs.sha256"] encoding:NSUTF8StringEncoding error:nil];
        mark = [mark stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        if (!mark.length || [mark caseInsensitiveCompare:sha] == NSOrderedSame) return place;
    }
    return nil;
}

/* No folder holds the image (the app copied to another Mac, or its disk not connected): ask where it is, and keep the
   answer for the next starts (defaults MNGGameDataFolder.<version>). The image is read where it is, never moved or
   copied. Returns its folder, or nil to quit. */
static NSString *game_data_ask(NSArray<NSString *> *looked, unsigned long long size, NSString *sha) {
    [NSApplication sharedApplication]; [NSApp activateIgnoringOtherApps:YES];
    for (;;) {
        NSAlert *alert = [NSAlert new];
        alert.messageText = @(TOTK_UI("游戏数据在哪里？", "Where is the game data?"));
        alert.informativeText = [NSString stringWithFormat:@(TOTK_UI("这个 App 不包含游戏数据（romfs.bin，%.1f GB），在 %@ 里没有找到。如果它在没有连接的磁盘上，请连接后点“重试”；否则请选择存放它的文件夹。游戏会在原处读取它，不会移动或复制。",
                                                                    "This app keeps its game data (romfs.bin, %.1f GB) outside the app, and it is not in %@. If it is on a disk that is not connected, connect it and choose Try Again; otherwise choose the folder it is in. The game reads it where it is, without moving or copying it.")),
                                     size / 1e9, [looked componentsJoinedByString:@(TOTK_UI("、", ", "))]];
        [alert addButtonWithTitle:@(TOTK_UI("选择文件夹…", "Choose Folder…"))];
        [alert addButtonWithTitle:@(TOTK_UI("重试", "Try Again"))];
        [alert addButtonWithTitle:@(TOTK_UI("退出", "Quit"))];
        NSModalResponse choice = [alert runModal];
        if (choice == NSAlertThirdButtonReturn) return nil;
        if (choice == NSAlertSecondButtonReturn) {
            for (NSString *folder in looked) { NSString *found = game_data_in(folder, size, sha); if (found) return found; }
            continue;
        }
        NSOpenPanel *panel = [NSOpenPanel openPanel];
        panel.canChooseFiles = NO; panel.canChooseDirectories = YES; panel.allowsMultipleSelection = NO;
        panel.message = @(TOTK_UI("选择存放 romfs.bin 的文件夹（或它上一层、以版本命名的文件夹所在的文件夹）。",
                                  "Choose the folder that holds romfs.bin (or the folder that holds its " TOTK_GAME_DIR " folder)."));
        panel.prompt = @(TOTK_UI("选择", "Choose"));
        if ([panel runModal] != NSModalResponseOK || !panel.URL) continue;
        NSString *found = game_data_in(panel.URL.path, size, sha);
        if (found) {
            [NSUserDefaults.standardUserDefaults setObject:found forKey:@"MNGGameDataFolder." TOTK_GAME_DIR];
            return found;
        }
        note([NSString stringWithFormat:@(TOTK_UI("%@ 里没有这个版本的 romfs.bin（应为 %llu 字节）。", "%@ holds no romfs.bin of this version (%llu bytes).")),
              panel.URL.path, size]);
    }
}

/* Returns -1 with *boot set to the arguments the game boots with (rewritten folder, "boot"), or the status to exit with. */
int runtime_app_launch(int argc, char **argv, char ***boot) {
    @autoreleasepool {
        NSString *root = [NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"totk"];
        NSString *binary = [root stringByAppendingPathComponent:@"rewritten/" TOTK_GAME_DIR "/main.patched.bin"];   // the game's code
        /* --check (build_app.py, once after packaging): only look that the game is there and exit. The first run of a new
           app is when macOS scans the whole bundle (minutes for 16 GB, with nothing on screen); doing it during the build
           makes the player's first start immediate. */
        if (argc > 1 && !strcmp(argv[1], "--check")) {
            int ok = access(binary.fileSystemRepresentation, R_OK) == 0;
            NSString *declared = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
            ok = ok && [declared isEqualToString:@TOTK_GAME_VERSION];
            printf("%s: game version %s\n", ok ? "ok" : "missing game files or version mismatch", TOTK_GAME_VERSION);
            return ok ? 0 : 1;
        }
        if (access(binary.fileSystemRepresentation, R_OK) != 0) {
            show_error(@"The game files are missing from this app. Build it again with build_app.py.");
            return 1;
        }
        NSString *home = NSHomeDirectory();
        NSString *data = [home stringByAppendingPathComponent:@"Library/Application Support/MacNativeGames/" GAME_FOLDER];   /* game_version.h: TOTK, BotW */
        NSString *logs = [home stringByAppendingPathComponent:@"Library/Logs/MacNativeGames"];
#ifdef GAME_BOTW
        /* The purple-frame watch's event log (nvn_mtl.m pw_log), kept across starts and written also without the debug log:
           one line a start and the lines of each purple episode, its screenshots beside it. */
        [NSFileManager.defaultManager createDirectoryAtPath:logs withIntermediateDirectories:YES attributes:nil error:nil];
        setenv("TOTK_EVENT_LOG", [logs stringByAppendingPathComponent:@GAME_LOG "-events.log"].fileSystemRepresentation, 0);
#endif
#ifdef GAME_BOTW
        /* The player's settings: <data>/settings.txt, one TOTK_NAME=value, MNG_NAME=value or BOTW_NAME=value per line (# starts a comment), read at
           each launch (e.g. TOTK_BOTW_COOK_COUNT=3, BOTW_FOG=1). Only TOTK_, MNG_ and BOTW_ names; a line here wins over the app's Info.plist environment. */
        { NSString *path = [data stringByAppendingPathComponent:@"settings.txt"];
          NSString *text = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil];
          NSCharacterSet *ws = NSCharacterSet.whitespaceCharacterSet;
          NSCharacterSet *bad = [NSCharacterSet characterSetWithCharactersInString:@"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"].invertedSet;
          __block int n = 0, badline = 0;
          [text enumerateLinesUsingBlock:^(NSString *raw, BOOL *stop) {
              n++;
              NSString *line = [[raw componentsSeparatedByString:@"#"].firstObject stringByTrimmingCharactersInSet:ws];
              if (!line.length) return;
              NSRange eq = [line rangeOfString:@"="];
              NSString *name = eq.location == NSNotFound ? line : [[line substringToIndex:eq.location] stringByTrimmingCharactersInSet:ws];
              if (eq.location == NSNotFound || !([name hasPrefix:@"TOTK_"] || [name hasPrefix:@"MNG_"] || [name hasPrefix:@"BOTW_"]) || [name rangeOfCharacterFromSet:bad].location != NSNotFound) { badline = n; *stop = YES; return; }
              setenv(name.UTF8String, [[line substringFromIndex:eq.location + 1] stringByTrimmingCharactersInSet:ws].UTF8String, 1);
          }];
          if (badline) { show_error([NSString stringWithFormat:@"%@, line %d: expected TOTK_NAME=value, MNG_NAME=value or BOTW_NAME=value", path, badline]); return 1; } }
#endif
        /* Folders the environment already names are kept (open --env TOTK_PROFILE_DIR=... runs a test on a save copy,
           TOTK_GAME_SLOT_DIR shares one game slot with another build of the game: one game at a time on the Mac). */
        NSString *(^env_or)(const char *, NSString *) = ^NSString *(const char *name, NSString *fallback) {
            const char *v = getenv(name); return v && *v ? @(v) : fallback; };
#ifdef TOTK_GAME_V143
        /* Local build only: the player's settings, <data>/settings.txt, one TOTK_NAME=value per line (# starts a comment), read at
           each launch. A line here wins over the app's Info.plist environment; a bad line shows an alert instead of a start. */
        { NSString *path = [data stringByAppendingPathComponent:@"settings.txt"];
          int badline = settings_txt_apply([NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil]);
          if (badline) { show_error([NSString stringWithFormat:@"%@, line %d: expected TOTK_NAME=value", path, badline]); return 1; } }
#endif
        NSString *profile = env_or("TOTK_PROFILE_DIR", [data stringByAppendingPathComponent:@"profile"]);
        NSString *slot = env_or("TOTK_GAME_SLOT_DIR", [data stringByAppendingPathComponent:@"game-slot"]);
        NSString *cache = env_or("TOTK_SHADER_CACHE", [data stringByAppendingPathComponent:[NSString stringWithFormat:@"shader-cache/rt-%s", DMSH_CHAIN]]);
        if (!make_dir(profile) || !make_dir(slot) || !make_dir(cache) || !make_dir(logs)) {
            show_error([NSString stringWithFormat:@"Cannot create %@.", data]);
            return 1;
        }
        pid_t running = running_game(slot);
        if (running) {
            [[NSRunningApplication runningApplicationWithProcessIdentifier:running] activateWithOptions:NSApplicationActivateAllWindows];
            return 0;
        }
#if defined(TOTK_GAME_V121) || defined(TOTK_GAME_V143)
        NSString *backup = [profile stringByAppendingFormat:@"-before-%s", TOTK_GAME_VERSION];
        NSFileManager *fm = NSFileManager.defaultManager;
        if (![fm fileExistsAtPath:backup]) {
            NSString *temporary = [backup stringByAppendingFormat:@".copy-%d", getpid()];
            NSError *error = nil;
            if (![fm copyItemAtPath:profile toPath:temporary error:&error] ||
                ![fm moveItemAtPath:temporary toPath:backup error:&error]) {
                [fm removeItemAtPath:temporary error:nil];
                show_error([@"Cannot back up saves before upgrading: " stringByAppendingString:error.localizedDescription ?: @"unknown error"]);
                return 1;
            }
        }
#endif
        BOOL hasExternalSave = NO;
        NSString *saveError = nil;
        if (!prepare_external_save(&hasExternalSave, &saveError)) {
            show_error(saveError);
            return 1;
        }
        if (chdir(root.fileSystemRepresentation) != 0) {   // the game reads its data relative to this folder
            show_error(@"Cannot open the game folder inside the app.");
            return 1;
        }
        /* The player's controller layout (runtime/controller_map.h), checked here: a bad line shows this alert instead of
           the game closing at start. */
        NSString *controls = env_or("TOTK_CONTROLS", [data stringByAppendingPathComponent:@"controls.txt"]);
        NSData *layout = [NSData dataWithContentsOfFile:controls];
        if (layout) {
            NSString *text = layout.length < 16384 ? [[NSString alloc] initWithData:layout encoding:NSUTF8StringEncoding] : nil;
            uint16_t map[CONTROLLER_BUTTONS]; uint8_t modes[CONTROLLER_BUTTONS]; controller_map_default(map, modes);
            const char *problem = layout.length < 16384 ? "not UTF-8 text" : "larger than 16 KB";
            int line = text ? controller_map_parse(text.UTF8String, map, modes, &problem) : 1;
            if (line) { show_error([NSString stringWithFormat:@"%@, line %d: %s", controls, line, problem]); return 1; }
        }
        setenv("TOTK_CONTROLS", controls.fileSystemRepresentation, 1);
        /* key 3's picture mode, kept between runs (runtime/game_patches_v143.c picture_mode_save / _restore) */
        setenv("TOTK_PICTURE_MODE", env_or("TOTK_PICTURE_MODE", [data stringByAppendingPathComponent:@"picture-mode.txt"]).fileSystemRepresentation, 1);
        setenv("TOTK_PROFILE_DIR", profile.fileSystemRepresentation, 1);
        setenv("TOTK_SHADER_CACHE", cache.fileSystemRepresentation, 1);
        setenv("TOTK_GAME_SLOT_DIR", slot.fileSystemRepresentation, 1);
        setenv("TOTK_ROMFS_PROOF_DIR", data.fileSystemRepresentation, 1);   // the app bundle stays read-only
        /* build_app.py --mac-game-data library: the app carries no RomFS image, so a new build is a few hundred MB and
           copies to another Mac without it. It is looked for in the folder chosen at an earlier start (game_data_ask), the
           folder the build put it in (Info.plist MNGGameDataFolder, e.g. on another disk), and <data>/GameData; with none
           holding it, the player is asked. An app with its own image uses that. */
        NSFileManager *files = NSFileManager.defaultManager;
        if (![files fileExistsAtPath:@"extracted/" TOTK_GAME_DIR "/romfs/romfs.bin"]) {
            NSString *pins = [NSString stringWithContentsOfFile:@"build/game-data/" TOTK_GAME_DIR "/romfs-config.txt" encoding:NSUTF8StringEncoding error:nil];
            unsigned long long size = 0; NSString *sha = @"";
            for (NSString *line in [pins componentsSeparatedByString:@"\n"]) {
                NSArray<NSString *> *kv = [line componentsSeparatedByString:@" "];
                if (kv.count != 2) continue;
                if ([kv[0] isEqualToString:@"ROMFS_IMAGE_SIZE"]) size = strtoull(kv[1].UTF8String, NULL, 10);
                if ([kv[0] isEqualToString:@"ROMFS_IMAGE_SHA256"]) sha = kv[1];
            }
            if (!size) { show_error(@"The game files are missing from this app. Build it again with build_app.py."); return 1; }
            NSMutableArray<NSString *> *places = [NSMutableArray array];
            for (id place in @[[NSUserDefaults.standardUserDefaults stringForKey:@"MNGGameDataFolder." TOTK_GAME_DIR] ?: @"",
                               [NSBundle.mainBundle objectForInfoDictionaryKey:@"MNGGameDataFolder"] ?: @"",
                               [data stringByAppendingPathComponent:@"GameData"]])
                if ([place isKindOfClass:NSString.class] && [place length] && ![places containsObject:place]) [places addObject:place];
            NSString *library = nil;
            for (NSString *place in places) if ((library = game_data_in(place, size, sha))) break;
            if (!library && !(library = game_data_ask(places, size, sha))) return 0;
            setenv("TOTK_ROMFS_DATA", library.fileSystemRepresentation, 1);   // runtime/romfs.c
        }
        setenv("TOTK_SHARED_FONTS", "extracted/firmware/shared-fonts", 1);
        /* The folder the game binary's own files are in (shader-service, shader-seed, shader-libs, pso lists: totk_shader_rt.m
           root_dir, nvn_mtl.m exe_rel), which is no longer the folder of the running executable (Contents/MacOS). */
        setenv("TOTK_NATIVE_DIR", [root stringByAppendingPathComponent:@"build"].fileSystemRepresentation, 1);
        /* Save sync (runtime/cloud_save.m): the folder the player chose in the app menu (runtime/frontend.m) or in
           cloud_folder_offer, which asks at a start that has none. TOTK_CLOUD_SAVE_DIR wins over it; 0 = off. */
        if (!hasExternalSave) {
            NSUserDefaults *sync = [[NSUserDefaults alloc] initWithSuiteName:@RUNTIME_SAVE_SYNC_DEFAULTS];
            NSString *chosen = [sync stringForKey:@"TOTK_CLOUD_SAVE_FOLDER"];
            if (!getenv("TOTK_CLOUD_SAVE_DIR") && !getenv("TOTK_CLOUD_SAVE_FOLDER") && !chosen.length) chosen = cloud_folder_offer(sync);
            if (!getenv("TOTK_CLOUD_SAVE_DIR") && chosen.length) setenv("TOTK_CLOUD_SAVE_FOLDER", chosen.fileSystemRepresentation, 0);
        }
        /* Player mods (runtime/romfs.c): <data>/mods/<version>/manifest.tsv, when present. With no controller connected at
           start, <data>/mods/<version>-keyboard takes its place when it holds a manifest (keyboard key icons in the prompts:
           scripts/make_button_icons.py --style keyboard or azerty). The fonts are read once, so it holds for the session. */
        NSString *mods = [data stringByAppendingPathComponent:@"mods/" TOTK_GAME_DIR];
#ifdef GAME_BOTW
        NSString *keyboardMods = [mods stringByAppendingString:@"-keyboard"];
        if (!getenv("TOTK_ROMFS_MODS") && [NSFileManager.defaultManager fileExistsAtPath:[keyboardMods stringByAppendingPathComponent:@"manifest.tsv"]] &&
            !controller_connected())
            mods = keyboardMods;
#endif
        setenv("TOTK_ROMFS_MODS", mods.fileSystemRepresentation, 0);
#ifdef GAME_BOTW
        /* Code mods at launch (runtime/pchtxt.h): <data>/mods/<version>-code.pchtxt, beside the mods folder, unless the environment names a file. */
        NSString *code_mods = nil;   /* named in the log below, after the redirect */
        { NSString *code = [data stringByAppendingPathComponent:@"mods/" TOTK_GAME_DIR "-code.pchtxt"];
          if (!getenv("TOTK_PCHTXT") && [NSFileManager.defaultManager fileExistsAtPath:code]) {
              setenv("TOTK_PCHTXT", code.fileSystemRepresentation, 1);
              code_mods = code;
          }
          /* The UI pack of an app that carries one (gfx/nvn/ui_pack.h; MNG_UI_PACK=0 turns it off) */
          NSString *pack = [NSBundle.mainBundle.bundlePath stringByAppendingPathComponent:@"Contents/Resources/ui-pack"];
          if (!getenv("MNG_UI_PACK_DIR") && [NSFileManager.defaultManager fileExistsAtPath:pack]) setenv("MNG_UI_PACK_DIR", pack.fileSystemRepresentation, 1); }
#endif
        /* The game's language (runtime/services.c): the app's first preferred language, which also follows a per-app
           language (defaults write org.macnativegames.totk AppleLanguages -array pt-BR). TOTK_LANG still overrides it. */
        NSString *preferred = NSLocale.preferredLanguages.firstObject;
        if (preferred) setenv("TOTK_LOCALE", preferred.UTF8String, 1);
        setenv("TOTK_GPU_ALLOW_SUBMIT", "1", 1);
        setenv("TOTK_GPU_WIDEN", "1", 1);
        setenv("TOTK_GPU_BLOCKRT", "1", 1);
        setenv("TOTK_GPU_MIDCLEAR", "1", 1);
        setenv("TOTK_SLOT_OWNER", "user", 0);
        setenv("TOTK_SLOT_PURPOSE", "player", 0);
        /* The commit the app was built from (build_app.py MNGSourceCommit): the HUD box shows it (runtime/frontend.m). */
        NSString *commit = [NSBundle.mainBundle objectForInfoDictionaryKey:@"MNGSourceCommit"];
        if ([commit isKindOfClass:NSString.class] && commit.length) setenv("TOTK_SOURCE_COMMIT", commit.UTF8String, 1);
        /* The MNG Launcher that built or last updated the app (build_app.py MNGLauncherVersion): before the commit in the
           HUD box's build line (shared/runtime/hud_box.h hud_build_text). */
        NSString *builder = [NSBundle.mainBundle objectForInfoDictionaryKey:@"MNGLauncherVersion"];
        if ([builder isKindOfClass:NSString.class] && builder.length) setenv("TOTK_LAUNCHER_VERSION", builder.UTF8String, 1);
        NSString *contrib = totk_inbox_claim(env_or("TOTK_SHADER_CONTRIB", contrib_file(data)));   // this game's file only
        setenv("TOTK_SHADER_CONTRIB", contrib.fileSystemRepresentation, 1);
        { const char *seed = getenv("TOTK_SHADER_SEED_DIR");   // what this app serves leaves the file before the reminder counts it
          NSString *build = [root stringByAppendingPathComponent:@"build"];
          totk_contrib_prune(contrib, seed ? @(seed) : [build stringByAppendingPathComponent:@"shader-seed"], build); }
        contrib_ask(contrib);
        setenv("TOTK_BOARD_DIR", data.fileSystemRepresentation, 0);   // the leaderboard's folder (runtime/board_upload.h); a test run keeps its own;
        //   named first: the uploads below send the board's id with the rows (totk_inbox_board_id)
        totk_inbox_start(contrib, @"mac");   // the player chose "Contribute shaders": sent while playing
        unsetenv("TOTK_AUTOLOAD");   // the game starts at its title screen, as on the console
        /* runtime command channel (runtime/input.c): echo "shot ~/Desktop/shot.png" >> "<data>/input.txt" */
        { NSString *cmds = env_or("TOTK_INPUT_FILE", [data stringByAppendingPathComponent:@"input.txt"]);
          FILE *cf = fopen(cmds.fileSystemRepresentation, "w"); if (cf) fclose(cf);
          setenv("TOTK_INPUT_FILE", cmds.fileSystemRepresentation, 1); }
        /* VR (stage 1, totk_vr.m): the control file of `basalt-streamer --wired --runtime <file>`, from
           defaults write org.macnativegames.totk TOTK_VR_CONTROL <file> or the environment, which wins. */
        { NSString *vr = [NSUserDefaults.standardUserDefaults stringForKey:@"TOTK_VR_CONTROL"];
          if (vr.length) setenv("TOTK_VR_CONTROL", vr.fileSystemRepresentation, 0);
          /* VR settings: the game runs the streamer (defaults TOTK_VR_STREAMER <basalt-streamer>) and keeps the per-eye
             size picked in the settings panel in <data>/vr.txt */
          NSString *st = [NSUserDefaults.standardUserDefaults stringForKey:@"TOTK_VR_STREAMER"];
          if (st.length) setenv("TOTK_VR_STREAMER", st.fileSystemRepresentation, 0);
          setenv("TOTK_VR_SETTINGS", [data stringByAppendingPathComponent:@"vr.txt"].fileSystemRepresentation, 0);
          setenv("TOTK_CAMERA_SETTINGS", [data stringByAppendingPathComponent:@"camera.txt"].fileSystemRepresentation, 0);   /* third-person distance */
          setenv("TOTK_HEAD_SETTINGS", [data stringByAppendingPathComponent:@"head.txt"].fileSystemRepresentation, 0);   /* Link's head shown / hidden */
          setenv("TOTK_VR_OFFSET", [data stringByAppendingPathComponent:@"vr-offset.txt"].fileSystemRepresentation, 0);   /* the camera's offset (VR page) */
          setenv("TOTK_VR_OPTIONS", [data stringByAppendingPathComponent:@"vr-options.txt"].fileSystemRepresentation, 0); }   /* the VR page's switches: Mac screen, 6DoF, snap turning */   /* the camera's offset (VR page) */   /* Link's head shown / hidden */

        /* The log is written only with debug on, as on the iPhone: defaults write org.macnativegames.totk TOTK_DEBUG_LOG
           -bool YES (org.macnativegames.botw for Breath of the Wild), or TOTK_DEBUG_LOG=1 in the environment, which wins
           (the test tools pass it). Off, the game's output goes to /dev/null and the logs of earlier runs are removed. */
        const char *debugEnv = getenv("TOTK_DEBUG_LOG");
        BOOL debugLog = debugEnv ? (*debugEnv && strcmp(debugEnv, "0")) : [NSUserDefaults.standardUserDefaults boolForKey:@"TOTK_DEBUG_LOG"];
        NSString *log = [logs stringByAppendingPathComponent:@GAME_LOG ".log"];
        NSString *previous = [logs stringByAppendingPathComponent:@GAME_LOG ".previous.log"];
        [NSFileManager.defaultManager removeItemAtPath:previous error:nil];
        if (debugLog) [NSFileManager.defaultManager moveItemAtPath:log toPath:previous error:nil];
        else [NSFileManager.defaultManager removeItemAtPath:log error:nil];
        FILE *lf = fopen(debugLog ? log.fileSystemRepresentation : "/dev/null", "w");
        if (lf) {
            dup2(fileno(lf), STDOUT_FILENO);
            dup2(fileno(lf), STDERR_FILENO);
            fclose(lf);
        }
#ifdef GAME_BOTW
        if (code_mods) fprintf(stderr, "[launcher] code mods: %s\n", code_mods.fileSystemRepresentation);
#endif
        NSString *icons = nil, *style = button_icons(root, controls, &icons);
        if (style) setenv("TOTK_BUTTON_ICONS", style.UTF8String, 1);
#ifndef GAME_BOTW
        if (style) setenv("TOTK_PROMPT_ICONS", "0", 0);   /* our art is in the font: upstream's live prompt overlay stays off (settings.txt or the environment still win) */
#endif
        if (icons) setenv("TOTK_ROMFS_BUTTON_ICONS", icons.fileSystemRepresentation, 1);
#if defined(GAME_BOTW190) || defined(GAME_BOTW160)
        /* A mod that replaces 4 GiB or more, or Pack/TitleBG.pack with 200 MiB or more, is an HD texture pack, which needs
           the HD heaps (about 6.3 GB of memory): TOTK_BOTW_HD_HEAPS=1 then, unless the environment sets it. After the
           redirect, so the line is in the log. */
        { unsigned long long title_bg, bytes = mod_bytes(getenv("TOTK_ROMFS_MODS"), &title_bg);
          const char *heaps = getenv("TOTK_BOTW_HD_HEAPS");
          if (heaps) fprintf(stderr, "[launcher] HD heaps: TOTK_BOTW_HD_HEAPS=%s from the environment (mods replace %.1f GiB)\n", heaps, bytes / 1073741824.0);
          else if (bytes >= 4ULL << 30 || title_bg >= 200ULL << 20) {
              setenv("TOTK_BOTW_HD_HEAPS", "1", 0);
              fprintf(stderr, "[launcher] HD heaps: on, the mods replace %.1f GiB, TitleBG.pack %llu MiB (an HD texture pack)\n", bytes / 1073741824.0, title_bg >> 20);
          } }
#endif
        static char *arguments[4] = {NULL, "rewritten/" TOTK_GAME_DIR, "boot", NULL};
        arguments[0] = argv[0];
        *boot = arguments;
        return -1;
    }
}
