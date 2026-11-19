/* vmx_fmaf (runtime/ppu/ppu_vmx.h) against libm fmaf, and vmaddfp / vnmsubfp against fmaf lane by lane.
 *
 * vmx_fmaf must be bit-identical to fmaf for every input (NaNs compare as NaN). The inputs are random words,
 * random words limited to a mid exponent range (so sums stay normal and the double path is the one taken), and
 * products made to nearly cancel the addend (the subnormal and rounding-midpoint edges). Built with -mno-fma on
 * x86-64 so the double-rounding path is the one tested.
 *
 *   test_vmx_fmaf [count]     default 20 million cases
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <stdlib.h>
#include "../runtime/ppu/ppu_vmx.h"

static uint64_t st = 88172645463325252ull;
static uint32_t rnd(void) { st ^= st << 13; st ^= st >> 7; st ^= st << 17; return (uint32_t)st; }
static float f(uint32_t u) { float x; memcpy(&x, &u, 4); return x; }
static uint32_t U(float x) { uint32_t u; memcpy(&u, &x, 4); return u; }
static void put(uint8_t* v, int i, float x) { vmx_sw(v, i, U(x)); }

static uint32_t reference_flush(uint32_t value, uint32_t vscr) {
    if ((vscr & VMX_NJ) && (value & 0x7F800000u) == 0)
        return value & 0x80000000u;
    return value;
}

static int check_vectors(void) {
    const uint32_t edges[] = {0, 0x80000000, 1, 0x80000001, 0x007FFFFF,
        0x00800000, 0x80800000, 0x3F800000, 0xBF800000, 0x3F800001,
        0x3F7FFFFE, 0x7F7FFFFF, 0x7F800000, 0xFF800000, 0x7FC12345,
        0x7F812345, 0xFFC54321};
    for (unsigned sample = 0; sample < 100000; ++sample) {
        uint8_t inputs[3][16];
        for (unsigned source = 0; source < 3; ++source)
            for (unsigned lane = 0; lane < 4; ++lane) {
                unsigned value = rnd();
                if (sample < 4913) {
                    unsigned divisor = source == 0 ? 1 : source == 1 ? 17 : 289;
                    value = edges[(sample / divisor + lane) % 17];
                }
                vmx_sw(inputs[source], lane, value);
            }
        for (unsigned flush = 0; flush < 2; ++flush) {
            uint32_t vscr = flush ? VMX_NJ : 0;
            uint8_t expected[16];
            for (unsigned lane = 0; lane < 4; ++lane) {
                float a = f(reference_flush(vmx_w(inputs[0], lane), vscr));
                float b = f(reference_flush(vmx_w(inputs[1], lane), vscr));
                float c = f(reference_flush(vmx_w(inputs[2], lane), vscr));
                uint32_t result = U(fmaf(a, c, b));
                if ((result & 0x7FFFFFFFu) > 0x7F800000u) result = 0x7FC00000u;
                vmx_sw(expected, lane, reference_flush(result, vscr));
            }
            for (unsigned alias = 0; alias < 4; ++alias) {
                uint8_t storage[4][17];
                for (unsigned source = 0; source < 3; ++source)
                    memcpy(storage[source] + 1, inputs[source], 16);
                uint8_t* dest = storage[alias] + 1;
                vmx_vmaddfp(dest, storage[0] + 1, storage[1] + 1, storage[2] + 1, vscr);
                if (memcmp(dest, expected, 16)) {
                    printf("vmaddfp mismatch sample=%u flush=%u alias=%u\n", sample, flush, alias);
                    return 1;
                }
            }
        }
    }
    puts("vmaddfp edge/random vectors, both VSCR modes, unaligned aliases: passed");
    return 0;
}

int main(int argc, char** argv)
{
    if (check_vectors()) return 1;
    const long count = argc > 1 ? atol(argv[1]) : 20000000L;
    unsigned long long n = 0, bad = 0, lane_bad = 0;
    for (long i = 0; i < count; i++) {
        uint32_t a = rnd(), b = rnd(), c = rnd();
        if (i & 1) {
            a = (a & 0x80FFFFFF) | ((0x60 + (rnd() % 0x40)) << 23);
            b = (b & 0x80FFFFFF) | ((0x60 + (rnd() % 0x40)) << 23);
            c = (c & 0x80FFFFFF) | ((0x60 + (rnd() % 0x40)) << 23);
        }
        if ((i & 7) == 2) c = U(-f(a) * f(b)) ^ (rnd() & 0xF);   /* near cancellation */
        const float x = f(a), z = f(b), y = f(c);
        const uint32_t r1 = U(vmx_fmaf(x, z, y)), r2 = U(fmaf(x, z, y));
        n++;
        if (r1 != r2 && !(isnan(f(r1)) && isnan(f(r2)))) {
            if (bad < 5) printf("diff %08x %08x %08x: %08x vs %08x\n", a, b, c, r1, r2);
            bad++;
        }
        if ((i & 63) == 0) {
            /* vmaddfp: d = a * c + b; vnmsubfp: d = -(a * c - b). Four lanes, no flush, no NaNs. */
            uint8_t va[16], vb[16], vc[16], d1[16], d2[16];
            float xs[4], zs[4], ys[4];
            for (int l = 0; l < 4; l++) {
                xs[l] = f(rnd()); zs[l] = f(rnd()); ys[l] = f(rnd());
                if (!isfinite(xs[l]) || !isfinite(zs[l]) || !isfinite(ys[l])) { xs[l] = 1.5f; zs[l] = -2.25f; ys[l] = 0.75f; }
                put(va, l, xs[l]); put(vc, l, zs[l]); put(vb, l, ys[l]);
            }
            vmx_vmaddfp(d1, va, vb, vc, 0);
            vmx_vnmsubfp(d2, va, vb, vc, 0);
            for (int l = 0; l < 4; l++) {
                const float e1 = fmaf(xs[l], zs[l], ys[l]), e2 = -fmaf(xs[l], zs[l], -ys[l]);
                if (isnan(e1) || isnan(e2)) continue;
                if (vmx_w(d1, l) != U(e1) || vmx_w(d2, l) != U(e2)) lane_bad++;
            }
        }
    }
    printf("%llu cases, %llu differ; vmaddfp/vnmsubfp lanes: %llu differ\n", n, bad, lane_bad);
    return (bad || lane_bad) ? 1 : 0;
}
