/* Test tilt mode (games/totk/runtime/gyro_tilt.h): the virtual controller's tilt, turn and reported rates, the LT + RT
   chord and when tilt mode is allowed. */
#define _DEFAULT_SOURCE   /* M_PI */
#include "../games/totk/runtime/gyro_tilt.h"
#include <stdio.h>
#include <string.h>

static int failures;
static void expect(int ok, const char *what) { if (!ok) { fprintf(stderr, "FAIL: %s\n", what); failures++; } }
static int near(double a, double b, double eps) { return fabs(a - b) < eps; }

/* The pose the reported rate turns old into over dt (Rodrigues), against the pose gyro_tilt_matrix gives. */
static double rate_error(const GyroTilt *before, const GyroTilt *after, const double w[3], double dt) {
    double old[3][3], now[3][3], r[3][3], q[3][3], err = 0;
    gyro_tilt_matrix(before, old); gyro_tilt_matrix(after, now);
    double a = sqrt(w[0] * w[0] + w[1] * w[1] + w[2] * w[2]) * dt, k[3] = {0, 0, 0};
    if (a > 0) for (int i = 0; i < 3; i++) k[i] = w[i] * dt / a;
    double K[3][3] = {{0, -k[2], k[1]}, {k[2], 0, -k[0]}, {-k[1], k[0], 0}}, KK[3][3];
    gyro_tilt_mul(K, K, KK);
    for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) r[i][j] = (i == j) + sin(a) * K[i][j] + (1 - cos(a)) * KK[i][j];
    gyro_tilt_mul(old, r, q);
    for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) err = fmax(err, fabs(q[i][j] - now[i][j]));
    return err;
}

/* n ticks of 10 ms; returns the worst rate error and leaves the last gyro and acc. */
static double run(GyroTilt *t, int n, int32_t lx, int32_t ly, int32_t rx, double gyro[3], double acc[3]) {
    double worst = 0;
    for (int i = 0; i < n; i++) {
        GyroTilt before = *t;
        gyro_tilt_tick(t, lx, ly, rx, 30, 0.01, NULL, gyro, acc);
        worst = fmax(worst, rate_error(&before, t, gyro, 0.01));
    }
    return worst;
}

/* runtime/motion_fusion.c as tilt mode sees it: each sample's rate is held until the next sample, at most 50 ms after it,
   and integrated in the body frame. hold_ms[i] is the gap after tick i (a late timer tick: a long gap). Returns the angle
   in degrees between the pose shown at the end and the virtual controller's. */
static double fusion_error(int closed, const int *hold_ms, int ticks, int32_t ly_until, int ly_ticks) {
    GyroTilt t = {0};
    double R[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}}, g[3], a[3];
    for (int i = 0; i < ticks; i++) {
        double dt = i ? hold_ms[i - 1] / 1000.0 : 0.01;
        gyro_tilt_tick(&t, 0, i < ly_ticks ? ly_until : 0, i < ly_ticks ? 20000 : 0, 30, dt, closed ? (const double (*)[3])R : NULL, g, a);
        double held = fmin(hold_ms[i], 50) / 1000.0, w = sqrt(g[0] * g[0] + g[1] * g[1] + g[2] * g[2]) * held, k[3] = {0, 0, 0}, E[3][3], KK[3][3], out[3][3];
        if (w > 0) for (int j = 0; j < 3; j++) k[j] = g[j] * held / w;
        double K[3][3] = {{0, -k[2], k[1]}, {k[2], 0, -k[0]}, {-k[1], k[0], 0}};
        gyro_tilt_mul(K, K, KK);
        for (int r = 0; r < 3; r++) for (int c = 0; c < 3; c++) E[r][c] = (r == c) + sin(w) * K[r][c] + (1 - cos(w)) * KK[r][c];
        gyro_tilt_mul(R, E, out); memcpy(R, out, sizeof R);
    }
    double q[3][3], e = 0; gyro_tilt_matrix(&t, q);
    for (int k = 0; k < 3; k++) e += R[k][0] * q[k][0] + R[k][1] * q[k][1] + R[k][2] * q[k][2];
    return acos(fmax(-1, fmin(1, (e - 1) / 2))) * 180 / M_PI;
}

int main(void) {
    double g[3], a[3], deg = M_PI / 180;
    GyroTilt t = {0};
    run(&t, 10, 0, 0, 0, g, a);
    expect(g[0] == 0 && g[1] == 0 && g[2] == 0 && near(a[2], -1, 1e-12) && near(a[0], 0, 1e-12), "at rest: flat, no rate, z = -1 G");

    /* Right stick right for one second: clockwise from above at TILT_YAW_DEG, gravity unchanged. */
    expect(run(&t, 100, 0, 0, 32767, g, a) < 1e-9, "yaw: the rate turns the old pose into the new one");
    expect(near(t.yaw, -TILT_YAW_DEG * deg, 1e-9), "one second of full right stick turns 90 degrees clockwise");
    expect(near(g[2], -TILT_YAW_DEG * deg, 1e-6) && near(g[0], 0, 1e-9) && near(g[1], 0, 1e-9), "yaw reads as a rate about -z only");
    expect(near(a[2], -1, 1e-12), "yaw leaves gravity alone while flat");

    /* Let go: it stops and keeps the angle. */
    run(&t, 50, 0, 0, 0, g, a);
    expect(near(t.yaw, -TILT_YAW_DEG * deg, 1e-9) && g[2] == 0, "let go: the angle stays, no rate");

    /* Another second to 180 degrees, then on past it: the angle wraps, the rate stays smooth. */
    expect(run(&t, 150, 0, 0, 32767, g, a) < 1e-9, "past 180 degrees the rate stays right");
    expect(near(t.yaw, 135 * deg, 1e-9) && near(g[2], -TILT_YAW_DEG * deg, 1e-6), "the angle wraps at 180 degrees");
    expect(run(&t, 100, 0, 0, -16384, g, a) < 1e-9 && near(g[2], TILT_YAW_DEG * deg * 16384 / 32767, 1e-6), "half left stick: half the rate, counter-clockwise");

    /* Turned 90 degrees, the left stick still tilts about the player's axes: up = the far edge down. */
    t = (GyroTilt){.yaw = 90 * deg};
    expect(run(&t, 100, 0, 32767, 0, g, a) < 1e-9, "tilt on a turned controller: the rate matches the pose");
    double q[3][3]; gyro_tilt_matrix(&t, q);
    /* Turned 90 degrees counter-clockwise, the controller's +x points away from the player; its height in the reference: */
    double z_of_far_edge = q[2][0];
    expect(near(t.pitch, -30 * deg, 1e-9) && z_of_far_edge < -0.49, "stick up: the edge facing away goes down, whatever the yaw");
    expect(near(a[0] * a[0] + a[1] * a[1] + a[2] * a[2], 1, 1e-12), "gravity stays 1 G");

    /* The squared curve: half stick a quarter of the tilt, a tenth of the stick a hundredth, the sign kept. */
    t = (GyroTilt){0}; run(&t, 100, 16384, -16384, 0, g, a);
    expect(near(t.roll, 30 * deg * 0.25, 1e-3) && near(t.pitch, 30 * deg * 0.25, 1e-3), "half stick: a quarter of the full tilt");
    t = (GyroTilt){0}; run(&t, 100, -3277, 0, 0, g, a);
    expect(near(t.roll, -30 * deg * 0.01, 1e-4), "a tenth of the stick: a hundredth of the tilt, to the left");

    /* Full tilt at 90 degrees: on its edge, the rate still right. */
    t = (GyroTilt){0};
    for (int i = 0; i < 100; i++) { GyroTilt before = t; gyro_tilt_tick(&t, 32767, 0, 0, 90, 0.01, NULL, g, a); expect(rate_error(&before, &t, g, 0.01) < 1e-9, "90 degrees: the rate matches the pose"); }
    expect(near(t.roll, 90 * deg, 1e-9) && near(a[2], 0, 1e-9) && near(fabs(a[0]), 1, 1e-9), "full right at 90: on its right edge, gravity along x");

    /* Full tilt at 180 degrees, turning at the same time: face down (gravity out of the face), the rate still right. */
    t = (GyroTilt){0};
    for (int i = 0; i < 200; i++) { GyroTilt before = t; gyro_tilt_tick(&t, 0, 32767, 32767, 180, 0.01, NULL, g, a); expect(rate_error(&before, &t, g, 0.01) < 1e-9, "180 degrees and a turn: the rate matches the pose"); }
    expect(near(t.pitch, -M_PI, 1e-9) && near(a[2], 1, 1e-9), "full up at 180: face down");
    expect(run(&t, 200, 0, 0, 0, g, a) < 1e-9 && near(t.pitch, 0, 1e-9) && near(a[2], -1, 1e-9), "let go from face down: back to flat through the far edge");

    /* Tilt and turn at once. */
    t = (GyroTilt){0};
    expect(run(&t, 200, 20000, -12000, -32767, g, a) < 1e-9, "tilt and yaw together: the rate matches the pose");

    expect(gyro_tilt_max(NULL) == 180 && gyro_tilt_max("90") == 90 && gyro_tilt_max("180") == 180 && gyro_tilt_max("181") == 180 && gyro_tilt_max("4") == 180 && gyro_tilt_max("30") == 30, "TOTK_GYRO_EMU_TILT: 5-180, else 180");

    /* LT + RT (physical 8, 9): together within 150 ms, in either order; once per press. */
    enum { LT = 1 << 8, RT = 1 << 9 }; const uint64_t ms = 1000000;
    GyroTiltChord c = {0};
    expect(!gyro_tilt_chord(&c, LT, LT, 1000 * ms) && gyro_tilt_chord(&c, LT | RT, RT, 1100 * ms), "LT then RT 100 ms later toggles");
    expect(!gyro_tilt_chord(&c, LT | RT, 0, 1200 * ms) && !gyro_tilt_chord(&c, LT | RT, 0, 3000 * ms), "a held pair toggles once");
    expect(!gyro_tilt_chord(&c, LT, 0, 3100 * ms) && !gyro_tilt_chord(&c, LT | RT, RT, 3200 * ms), "LT still held from the pair, RT let go and pressed again: no toggle");
    c = (GyroTiltChord){0};
    expect(!gyro_tilt_chord(&c, RT, RT, 5000 * ms) && gyro_tilt_chord(&c, LT | RT, LT, 5150 * ms), "RT then LT at exactly 150 ms toggles");
    c = (GyroTiltChord){0};
    expect(!gyro_tilt_chord(&c, LT, LT, 7000 * ms) && !gyro_tilt_chord(&c, LT | RT, RT, 7151 * ms), "151 ms apart: no toggle");
    c = (GyroTiltChord){0};
    expect(!gyro_tilt_chord(&c, LT, LT, 9000 * ms) && !gyro_tilt_chord(&c, LT | RT, RT, 11000 * ms), "LT held to lock on, RT 2 s later to shoot: no toggle");
    expect(!gyro_tilt_chord(&c, 0, 0, 12000 * ms) && !gyro_tilt_chord(&c, LT, LT, 13000 * ms) && gyro_tilt_chord(&c, LT | RT, RT, 13050 * ms),
           "after both are let go, a new pair toggles");
    c = (GyroTiltChord){0};
    expect(gyro_tilt_chord(&c, LT | RT, LT | RT, 15000 * ms), "both in one report toggle");

    /* When LT + RT may turn it on. */
    expect(gyro_tilt_allowed(NULL, 1, 0) && !gyro_tilt_allowed(NULL, 1, 1), "unset: only without a controller gyro");
    expect(gyro_tilt_allowed("1", 1, 1) && !gyro_tilt_allowed("0", 1, 0), "TOTK_GYRO_EMU=1 always, =0 never");
    expect(!gyro_tilt_allowed("1", 0, 0) && !gyro_tilt_allowed(NULL, 0, 0), "never without the game's six-axis device");
    expect(gyro_tilt_allowed("", 1, 0) && !gyro_tilt_allowed("yes", 1, 1), "another value counts as unset");

    /* A late timer tick (90 ms, the fusion holds 50 ms of it) while tilting and turning, then the stick let go: steering
       from the pose the sensor shows ends level and on the controller's angle; the open-loop rate would stay off (control). */
    int hold[200]; for (int i = 0; i < 200; i++) hold[i] = 10;
    hold[20] = 90; hold[35] = 3; hold[36] = 17;
    expect(fusion_error(1, hold, 200, 32767, 50) < 0.1, "a late tick: the sensor ends on the controller's pose");
    expect(fusion_error(0, hold, 200, 32767, 50) > 1, "negative control: the open-loop rate stays off after a late tick");
    /* A long run of late ticks (30 ms each, 20 ms on the first): the correction settles, it does not swing wider. */
    for (int i = 0; i < 200; i++) hold[i] = i < 100 ? 10 : 30;
    hold[100] = 20; hold[60] = 90;
    expect(fusion_error(1, hold, 200, 32767, 150) < 0.1, "late ticks in a row while turning: the sensor still ends on the pose");
    for (int i = 0; i < 200; i++) hold[i] = 10;
    expect(fusion_error(1, hold, 200, 32767, 50) < 1e-6 && fusion_error(0, hold, 200, 32767, 50) < 1e-6, "even ticks: both end on the pose");

    if (failures) return 1;
    puts("gyro tilt: ok");
    return 0;
}
