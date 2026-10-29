/* Tilt mode: a controller without motion sensors (an Xbox pad) tilts and turns a virtual controller
   lying face up, for the motion-control puzzles (Breath of the Wild: Myahm Agana's maze board, Toto Sah's stairs). LT + RT pressed together
   turn it on and off (gyro_tilt_chord); runtime/gamepad.m ticks it and feeds the six-axis sensor.
   The left stick sets the tilt, not its rate, on a squared curve (half way: a quarter of the tilt): pushed fully up, the top edge goes down by the full tilt; right, the right
   edge goes down; let go, the controller lies flat again. The right stick's x turns it about the axis out of its face (yaw,
   for puzzles that also turn the platform, Toto Sah): held right it turns clockwise seen from above at up to TILT_YAW_DEG
   per second, let go it stops and keeps the angle; the tilt stays about the player's axes whatever the yaw. The pose moves toward the stick at up to TILT_RATE_DEG per second,
   so the game's sensor fusion (runtime/motion_fusion.c) sees a smooth rotation, and each 10 ms tick hands it the rotation
   rate to the next pose in the controller frame plus the gravity of that pose (the fusion's frame: x right, y top edge,
   z out of the face; flat it reads z = -1 G). The turn uses mouse mode's frame (runtime/mouse_look.h: right is clockwise
   seen from above, -z). Pure functions, tested in tests/test_gyro_tilt.c. */
#ifndef GYRO_TILT_H
#define GYRO_TILT_H
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

enum { TILT_TICK_NS = 10000000, TILT_FIX_NS = 50000000, TILT_CHORD_NS = 150000000 };
#define TILT_RATE_DEG 150.0
#define TILT_YAW_DEG 90.0

typedef struct { double pitch, roll, yaw; } GyroTilt;   /* radians: pitch about x (top edge down < 0), roll about y (right edge down > 0), yaw about z (clockwise from above < 0) */

/* TOTK_GYRO_EMU_TILT: the full tilt in degrees, 5-90, default 90 (on its edge at full stick). */
static inline double gyro_tilt_max(const char *text) {
    if (!text || !*text) return 90;
    char *end; long v = strtol(text, &end, 10);
    return *end || v < 5 || v > 90 ? 90 : (double)v;
}

static inline void gyro_tilt_mul(const double a[3][3], const double b[3][3], double q[3][3]) {
    for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) { q[i][j] = 0; for (int k = 0; k < 3; k++) q[i][j] += a[i][k] * b[k][j]; }
}

/* Controller -> reference (rows = reference axes in controller coordinates, as runtime/motion_fusion.c): Rx(pitch) Ry(roll) Rz(yaw),
   so the controller turns on the table first and tilts about the reference axes after. */
static inline void gyro_tilt_matrix(const GyroTilt *t, double q[3][3]) {
    double cp = cos(t->pitch), sp = sin(t->pitch), cr = cos(t->roll), sr = sin(t->roll), cy = cos(t->yaw), sy = sin(t->yaw), xy[3][3];
    double x[3][3] = {{1, 0, 0}, {0, cp, -sp}, {0, sp, cp}}, y[3][3] = {{cr, 0, sr}, {0, 1, 0}, {-sr, 0, cr}}, z[3][3] = {{cy, -sy, 0}, {sy, cy, 0}, {0, 0, 1}};
    gyro_tilt_mul(x, y, xy); gyro_tilt_mul(xy, z, q);
}

/* The body-frame rate (rad/s) that turns pose `from` into pose `to` over dt. */
static inline void gyro_tilt_rate(const double from[3][3], const double to[3][3], double dt, double gyro[3]) {
    double e[3][3];
    for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) { e[i][j] = 0; for (int k = 0; k < 3; k++) e[i][j] += from[k][i] * to[k][j]; }
    double c = (e[0][0] + e[1][1] + e[2][2] - 1) / 2, angle = acos(c > 1 ? 1 : c < -1 ? -1 : c), s = sin(angle);
    double axis[3] = {e[2][1] - e[1][2], e[0][2] - e[2][0], e[1][0] - e[0][1]};
    for (int i = 0; i < 3; i++) gyro[i] = angle < 1e-9 ? 0 : axis[i] / (2 * s) * angle / dt;
}

/* One tick: move *t toward the left stick (-32767..32767 per axis, y up) over dt, turn it by the right stick's x, and give
   the new pose's gravity in G and the body-frame rate: the move over dt plus the turn from `from` to the old pose over TILT_FIX_NS. `from` is the
   pose the sensor shows now (runtime/motion_fusion.c holds each rate until the next sample and drops what a late tick
   misses, so steering from what it shows keeps a late tick from leaving it off level); NULL: the old pose. */
static inline void gyro_tilt_tick(GyroTilt *t, int32_t lx, int32_t ly, int32_t rx, double max_deg, double dt, const double (*from)[3], double gyro[3], double acc[3]) {
    const double full = max_deg * M_PI / 180, step = TILT_RATE_DEG * M_PI / 180 * dt;
    double y = ly / 32767.0, x = lx / 32767.0, old[3][3], now[3][3];   /* squared, sign kept: fine tilts near the centre, full tilt at the rim */
    double want_pitch = -full * y * fabs(y), want_roll = full * x * fabs(x);
    gyro_tilt_matrix(t, old);
    double dp = want_pitch - t->pitch, dr = want_roll - t->roll;
    t->pitch += dp > step ? step : dp < -step ? -step : dp;
    t->roll += dr > step ? step : dr < -step ? -step : dr;
    t->yaw = remainder(t->yaw - TILT_YAW_DEG * M_PI / 180 * dt * rx / 32767.0, 2 * M_PI);
    gyro_tilt_matrix(t, now);
    gyro_tilt_rate(old, now, dt, gyro);   /* the move itself, over the last tick's length (the best guess of the next hold) */
    if (from) {   /* plus what the sensor is off by, spread over the fusion's longest hold, so a run of late ticks cannot overshoot */
        double fix[3]; gyro_tilt_rate(from, old, TILT_FIX_NS / 1e9, fix);
        for (int i = 0; i < 3; i++) gyro[i] += fix[i];
    }
    for (int j = 0; j < 3; j++) acc[j] = -now[2][j];
}

/* TOTK_GYRO_EMU: whether LT + RT may turn tilt mode on. Never without the game's six-axis device (TOTK_GYRO / gyro.txt);
   "0" never, "1" always, else (unset) only while no controller feeds real motion (a DualSense or Switch Pro keeps its gyro). */
static inline int gyro_tilt_allowed(const char *env, int six_axis, int motion_live) {
    if (!six_axis || (env && !strcmp(env, "0"))) return 0;
    return (env && !strcmp(env, "1")) || !motion_live;
}

/* LT + RT pressed together (physical buttons 8 and 9, controller_map.h): 1 on the press that completes the pair when the
   two presses came within TILT_CHORD_NS of each other, so ZL held to lock on and ZR pressed later to shoot does not count.
   A held pair fires once; both must be let go (one is enough) and pressed again. */
typedef struct { uint64_t at[2]; int down; } GyroTiltChord;
static inline int gyro_tilt_chord(GyroTiltChord *c, uint64_t physical, uint64_t edges, uint64_t now) {
    for (int i = 0; i < 2; i++) if (edges >> (8 + i) & 1) c->at[i] = now;
    int both = (physical >> 8 & 3) == 3, fire = 0;
    if (both && !c->down && (edges >> 8 & 3)) fire = (c->at[0] > c->at[1] ? c->at[0] - c->at[1] : c->at[1] - c->at[0]) <= TILT_CHORD_NS;
    c->down = both;
    return fire;
}
#endif
