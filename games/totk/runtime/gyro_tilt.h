/* Tilt mode: a controller without motion sensors (an Xbox pad) tilts and turns a virtual controller lying face up, for the
   motion-control puzzles (Breath of the Wild: Myahm Agana's maze board, Toto Sah's stairs). LT + RT pressed together turn
   it on and off (gyro_tilt_chord); runtime/gamepad.m ticks it and feeds the six-axis sensor.
   Every stick axis turns it at a speed, about an axis fixed to the player, never to the controller: left stick up tips the
   far edge down (about x), right tips the right edge down (about y), right stick right turns it clockwise seen from above
   (about the vertical, mouse mode's sign, runtime/mouse_look.h). Let go and it stays where it is; push again and it moves on
   from there. Speed is up to TILT_SPEED_DEG per second on a squared curve (half way: a quarter), for fine moves. Turning
   tilt mode on lays it flat. Each 10 ms tick hands the sensor fusion (runtime/motion_fusion.c) the rotation rate in the
   controller frame plus the gravity of the new pose (the fusion's frame: x right, y top edge, z out of the face; flat it
   reads z = -1 G). Pure functions, tested in tests/test_gyro_tilt.c. */
#ifndef GYRO_TILT_H
#define GYRO_TILT_H
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

enum { TILT_TICK_NS = 10000000, TILT_FIX_NS = 50000000, TILT_CHORD_NS = 150000000 };
#define TILT_RESET_NS 500000000ull   /* B: the pose holds this long, then lies flat (runtime/gamepad.m) */
#define TILT_SPEED_DEG 45.0   /* owner, 2026-10-29: 90 moved too far per push; half that, for fine moves */

/* The pose, controller -> reference (rows = reference axes in controller coordinates, as runtime/motion_fusion.c);
   {0} is flat. */
typedef struct { double q[3][3]; int set; } GyroTilt;

static inline void gyro_tilt_mul(const double a[3][3], const double b[3][3], double q[3][3]) {
    for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) { q[i][j] = 0; for (int k = 0; k < 3; k++) q[i][j] += a[i][k] * b[k][j]; }
}

static inline void gyro_tilt_matrix(const GyroTilt *t, double q[3][3]) {
    for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) q[i][j] = t->set ? t->q[i][j] : i == j;
}

/* The body-frame rate (rad/s) that turns pose `from` into pose `to` over dt. */
static inline void gyro_tilt_rate(const double from[3][3], const double to[3][3], double dt, double gyro[3]) {
    double e[3][3];
    for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) { e[i][j] = 0; for (int k = 0; k < 3; k++) e[i][j] += from[k][i] * to[k][j]; }
    double c = (e[0][0] + e[1][1] + e[2][2] - 1) / 2, angle = acos(c > 1 ? 1 : c < -1 ? -1 : c), s = sin(angle);
    double axis[3] = {e[2][1] - e[1][2], e[0][2] - e[2][0], e[1][0] - e[0][1]};
    for (int i = 0; i < 3; i++) gyro[i] = angle < 1e-9 ? 0 : axis[i] / (2 * s) * angle / dt;
}

/* One tick: turn *t by the sticks (-32767..32767 per axis, y up) over dt about the player's axes, and give the new pose's
   gravity in G and the body-frame rate: the move over dt plus the turn from `from` to the old pose over TILT_FIX_NS.
   `from` is the pose the sensor shows now (runtime/motion_fusion.c holds each rate until the next sample and drops what a
   late tick misses, so steering from what it shows keeps late ticks from leaving it off); NULL: the old pose. */
static inline void gyro_tilt_tick(GyroTilt *t, int32_t lx, int32_t ly, int32_t rx, double dt, const double (*from)[3], double gyro[3], double acc[3]) {
    const double speed = TILT_SPEED_DEG * M_PI / 180;
    double x = lx / 32767.0, y = ly / 32767.0, z = rx / 32767.0, old[3][3], now[3][3];
    double w[3] = {-speed * y * fabs(y), speed * x * fabs(x), -speed * z * fabs(z)};   /* reference frame, rad/s; squared, sign kept */
    gyro_tilt_matrix(t, old);
    double a = sqrt(w[0] * w[0] + w[1] * w[1] + w[2] * w[2]) * dt, k[3] = {0, 0, 0}, kk[3][3], r[3][3];
    if (a > 0) for (int i = 0; i < 3; i++) k[i] = w[i] * dt / a;
    double K[3][3] = {{0, -k[2], k[1]}, {k[2], 0, -k[0]}, {-k[1], k[0], 0}};
    gyro_tilt_mul(K, K, kk);
    for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) r[i][j] = (i == j) + sin(a) * K[i][j] + (1 - cos(a)) * kk[i][j];
    gyro_tilt_mul(r, old, now);   /* on the left: about the reference's (the player's) axes */
    for (int i = 0; i < 3; i++) {   /* keep it a rotation: Gram-Schmidt on the columns */
        for (int j = 0; j < i; j++) { double d = 0; for (int m = 0; m < 3; m++) d += now[m][i] * now[m][j]; for (int m = 0; m < 3; m++) now[m][i] -= d * now[m][j]; }
        double n = sqrt(now[0][i] * now[0][i] + now[1][i] * now[1][i] + now[2][i] * now[2][i]); for (int m = 0; m < 3; m++) now[m][i] /= n;
    }
    memcpy(t->q, now, sizeof now); t->set = 1;
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
