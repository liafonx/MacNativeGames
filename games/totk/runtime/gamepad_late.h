/* Tears of the Kingdom's (and Breath of the Wild's, Donkey Kong Bananza's and Pokopia's) code that needs the state of
   gamepad_core.h: tilt mode and the report of the button icons. Included once, by the shared runtime/gamepad.m, after
   gamepad_core.h (GAMEPAD_GAME_LATE, gamepad_game.h). */
#ifndef TOTK_GAMEPAD_LATE_H
#define TOTK_GAMEPAD_LATE_H

/* The button icons app/launcher.m loaded for this start (TOTK_BUTTON_ICONS: xbox-position, playstation-label, switch-label,
   none, or mod when the player's mods hold their own; unset: the launcher picks none, as in a Tears of the Kingdom app), at
   the size TOTK_BUTTON_ICONS_SCALE (switch only at 2 or more: gamepad_icon_style). They change only
   at the next start (runtime/romfs.c reads them once), so a new type or controller is logged for then. Main thread. */
static void button_icons_report(const char *why) {
    const char *loaded = getenv("TOTK_BUTTON_ICONS");
    if (!loaded || !strcmp(loaded, "mod")) return;
    int detected = -1, type = runtime_gamepad_type(&detected), by_position = 1;
    if (type == GAMEPAD_TYPE_AUTO && detected < 0) return;   /* no controller now: the launcher takes the last one seen */
    const char *scale = getenv("TOTK_BUTTON_ICONS_SCALE");
    int style = gamepad_icon_style(type, detected, scale ? atoi(scale) : 1, &by_position);
    char wanted[40];
    snprintf(wanted, sizeof wanted, "%s%s%s", style < 0 ? "none" : gamepad_type_name(style), style < 0 ? "" : "-", style < 0 ? "" : by_position ? "position" : "label");
    if (strcmp(wanted, loaded)) fprintf(stderr, "[totk-input] button icons: %s now, %s from the next start (%s)\n", loaded, wanted, why);
}
static void gamepad_game_pad_attached(int type) {
    if (getenv("TOTK_BUTTON_ICONS")) {   /* the launcher's pick for auto at the next start, when no controller is on yet (a HID pad is Switch-style) */
        setting_write("controller-detected.txt", gamepad_type_name(type), "connected controller");
        button_icons_report("controller connected");
    }
}
static void gamepad_game_type_picked(void) { button_icons_report("key 6"); }

/* ---- Tilt mode (runtime/gyro_tilt.h) ----
   For the motion-control puzzles on a controller without motion sensors. L3 + R3 (both sticks) clicked together turn it on and off,
   when gyro_tilt_allowed says so (TOTK_GYRO_EMU; by default only while no controller feeds real motion). While on, a
   10 ms timer turns a virtual controller lying face up by the sticks (left: tilt, right x: turn, each about the player's
   axes at a speed; let go and it stays, gyro_tilt.h) and feeds the pose to the sensor in the mouse's place
   (runtime_motion_mouse); the game gets neither stick meanwhile (runtime/input.c). While the settings panel or a text
   field has the controller, the pose holds still. B (leaving the device) lays it flat again. Off: the sensor
   goes back to the mouse mode's gyro or to the controller. */
static GyroTilt tilt;                          /* tilt queue */
static uint64_t tiltLast;
static dispatch_source_t tiltTimer;
static dispatch_queue_t tiltQueue;
static _Atomic uint64_t tiltReset;             /* B pressed: turn it back to flat from this time (0: none) */
static int tiltHoming;                         /* tilt queue: on the way back to flat */
int runtime_tilt_active(void) { return atomic_load(&tiltMode); }   /* tiltMode and tiltChord: gamepad_game.h */
/* The settings panel's tilt rows (runtime/settings_panel.h): tilt.txt (gyro_tilt_config) read once at the first use, a pick
   saved and in force at the next tick, also while tilt mode is on. */
static _Atomic int tiltSpeed, tiltInvert;      /* 0: tilt.txt not read yet */
static void tilt_settings_load(void) {
    if (atomic_load(&tiltSpeed)) return;
    char text[1024] = "", path[4096] = "";
    GyroTiltConfig c;
    int found = setting_read("tilt.txt", text, sizeof text, path, sizeof path), bad = gyro_tilt_config(found ? text : "", &c);
    if (bad) fprintf(stderr, "[totk-input] %s: %d line(s) not understood (speed = 60, 90, 180 or 360; invert = off, all, lx, ly or rx): kept the default for them\n", path, bad);
    int none = 0;
    if (atomic_compare_exchange_strong(&tiltSpeed, &none, c.speed)) atomic_store(&tiltInvert, c.invert);
}
static void tilt_settings_save(void) {
    GyroTiltConfig c = {atomic_load(&tiltSpeed), atomic_load(&tiltInvert)};
    char text[64];
    gyro_tilt_config_text(&c, text, sizeof text);
    setting_write("tilt.txt", text, "tilt settings");
}
int runtime_tilt_speed(void) { tilt_settings_load(); return atomic_load(&tiltSpeed); }
void runtime_tilt_speed_set(int speed) {
    if (!gyro_tilt_speed_ok(speed)) return;
    tilt_settings_load(); atomic_store(&tiltSpeed, speed); tilt_settings_save();
    fprintf(stderr, "[totk-input] tilt speed %d (settings)\n", speed);
}
int runtime_tilt_invert(void) { tilt_settings_load(); return atomic_load(&tiltInvert); }
void runtime_tilt_invert_set(int invert) {
    if (!gyro_tilt_invert_ok(invert)) return;
    tilt_settings_load(); atomic_store(&tiltInvert, invert); tilt_settings_save();
    fprintf(stderr, "[totk-input] tilt reversed: %s (settings)\n", gyro_tilt_invert_name(invert));
}
static void tilt_tick(void) {
    int32_t lx = 0, ly = 0, rx = 0;
    /* B leaves a motion-control device, and the game puts the platform back where it started; the virtual controller goes
       back to flat too, so the next entry starts level. Not at once: the game is still on the device for a few frames after
       the press and turned the platform toward a jump (owner, 2026-10-29), so the pose holds still for TILT_RESET_NS, then
       turns back with a real rate (gyro_tilt_home), never a jump the game's own pose would miss. The sticks wait meanwhile. */
    uint64_t now = runtime_now_ns(), reset = atomic_load(&tiltReset);
    if (reset && now >= reset) { atomic_store(&tiltReset, 0); reset = 0; tiltHoming = 1; }
    pthread_mutex_lock(&gamepadLock);
    if (!textCapture && !reset && !tiltHoming) { lx = gamepadState.lx; ly = gamepadState.ly; rx = gamepadState.rx; }
    pthread_mutex_unlock(&gamepadLock);
    double dt = tiltLast && now > tiltLast && now - tiltLast < 100000000ull ? (now - tiltLast) / 1e9 : TILT_TICK_NS / 1e9, gyro[3], acc[3];
    tiltLast = now;
    RuntimeMotionState shown; double from[3][3];   /* the pose the sensor shows now (gyro_tilt_tick) */
    int have = runtime_motion_read(&shown, 1) == 1;
    if (have) for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) from[j][i] = shown.dir[i * 3 + j];
    if (tiltHoming) tiltHoming = !gyro_tilt_home(&tilt, dt, have ? (const double (*)[3])from : NULL, gyro, acc);
    else {
        int invert = atomic_load(&tiltInvert);   /* reverse tilt: one axis or all three */
        gyro_tilt_tick_at(&tilt, invert & TILT_INVERT_LX ? -lx : lx, invert & TILT_INVERT_LY ? -ly : ly, invert & TILT_INVERT_RX ? -rx : rx, atomic_load(&tiltSpeed), dt, have ? (const double (*)[3])from : NULL, gyro, acc);
    }
    if (atomic_load(&tiltMode)) runtime_motion_feed_mouse(acc, gyro, now);
}
static void tilt_toggle(const char *why) {   /* main queue */
    int on = !atomic_load(&tiltMode);
    if (on && !gyro_tilt_allowed(getenv("TOTK_GYRO_EMU"), runtime_gyro_enabled(), runtime_motion_controller_live())) {
        fprintf(stderr, "[totk-input] tilt mode (%s): not available: %s\n", why, !runtime_gyro_enabled() ? "the game has no six-axis device in this run (TOTK_GYRO / gyro.txt)"
                : "a controller with a gyro is in use or TOTK_GYRO_EMU=0 (TOTK_GYRO_EMU=1 allows it anyway)");
        return;
    }
    if (!tiltQueue) tiltQueue = dispatch_queue_create("totk.tilt", DISPATCH_QUEUE_SERIAL);
    if (on) tilt_settings_load();   /* before the first tick */
    dispatch_sync(tiltQueue, ^{
        if (on) {
            tilt = (GyroTilt){0}; tiltLast = 0; tiltHoming = 0; atomic_store(&tiltReset, 0);   /* on: flat again */
            runtime_motion_mouse(1);
            atomic_store(&tiltMode, 1);
            tiltTimer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, tiltQueue);
            dispatch_source_set_timer(tiltTimer, dispatch_time(DISPATCH_TIME_NOW, 0), TILT_TICK_NS, 1000000);
            dispatch_source_set_event_handler(tiltTimer, ^{ tilt_tick(); });
            dispatch_resume(tiltTimer);
        } else {
            atomic_store(&tiltMode, 0);
            if (tiltTimer) { dispatch_source_cancel(tiltTimer); tiltTimer = nil; }
            pthread_mutex_lock(&gamepadLock); int mouse = mouseGyro; pthread_mutex_unlock(&gamepadLock);
            runtime_motion_mouse(mouse);
        }
    });
    if (on) fprintf(stderr, "[totk-input] tilt mode on (%s): the left stick tilts the controller, the right stick turns it; let go and it stays (up to %d degrees a second, reversed: %s)\n",
                    why, runtime_tilt_speed(), gyro_tilt_invert_name(runtime_tilt_invert()));
    else fprintf(stderr, "[totk-input] tilt mode off (%s)\n", why);
}
/* refresh_gamepads, after the unlock: the chord toggles tilt mode, the last controller going or one with a gyro of its own ends it, B lays the controller flat. */
static void tilt_after_refresh(int pressed, int connected, int cancel) {
    if (pressed) tilt_toggle("L3 + R3");
    else if (!connected && atomic_load(&tiltMode)) tilt_toggle("controller disconnected");
    else if (atomic_load(&tiltMode) && !gyro_tilt_allowed(getenv("TOTK_GYRO_EMU"), runtime_gyro_enabled(), runtime_motion_controller_live()))
        tilt_toggle("a controller with a gyro of its own is in use");   /* L3 + R3 is off for those too (gyro_tilt_allowed) */
    else if (cancel && atomic_load(&tiltMode)) { atomic_store(&tiltReset, runtime_now_ns() + TILT_RESET_NS); fprintf(stderr, "[totk-input] tilt mode: B pressed, the controller turns back to flat in %.1f s\n", TILT_RESET_NS / 1e9); }
}

#endif
