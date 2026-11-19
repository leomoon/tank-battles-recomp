#!/usr/bin/env python3
"""Compile the lifter's 32-bit load path and check its guarded accesses."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

from ppu_lifter import HEADER_PREAMBLE


def main():
    compilers = [name for name in ("g++", "clang++") if shutil.which(name)]
    if not compilers:
        raise RuntimeError("The load32 fixture requires g++ or clang++")
    start = HEADER_PREAMBLE.index("typedef struct ppu_mm")
    end = HEADER_PREAMBLE.index("static inline uint64_t vm_read64_mm")
    helpers = HEADER_PREAMBLE[start:end]
    fixture = r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
static uint8_t memory[0x20008];
static uint8_t* vm_base = memory;
static uint32_t ppu_vm_size = 0x20000, ppu_hle_inject_base = 0x10000;
static int vm_inline_ok = 1, vm_inline_st_ok = 1;
static uint32_t g_barrier_sync_watch, g_null_sweep_hi;
static const unsigned char* g_spu_coh_bits;
static unsigned calls;
static uint64_t last_address;
static uint8_t vm_read8(uint64_t a) { (void)a; return 0; }
static uint16_t vm_read16(uint64_t a) { (void)a; return 0; }
static uint32_t vm_read32(uint64_t a) {
    ++calls;
    last_address = a;
    return 0xdecafbad;
}
'''
    checks = r'''
static void fallback(const ppu_mm* m, uint64_t a) {
    unsigned before = calls;
    assert(vm_read32_mm(m, a) == 0xdecafbad);
    assert(calls == before + 1 && last_address == a);
}
int main(void) {
    for (unsigned i = 0; i < sizeof memory; ++i) memory[i] = (i * 37) & 255;
    ppu_mm m = ppu_mm_take();
    for (unsigned a = 0x10000; a <= 0x1fffc; ++a) {
        if (a == m.poll) continue;
        uint32_t expected = ((uint32_t)memory[a] << 24) |
            ((uint32_t)memory[a+1] << 16) |
            ((uint32_t)memory[a+2] << 8) | memory[a+3];
        assert(vm_read32_mm(&m, a) == expected);
    }
    assert(calls == 0);
    fallback(&m, 0); fallback(&m, 0xffff); fallback(&m, 0x1fffd);
    fallback(&m, m.poll); fallback(&m, 0xe0044014); fallback(&m, 0xffffffff);
    /* Preserve the original full address when the runtime handles a miss. */
    fallback(&m, 0x10001ffffULL);
    vm_inline_ok = 0; m = ppu_mm_take(); fallback(&m, 0x10000);
    vm_inline_ok = 1; ppu_vm_size = 0x10002;
    m = ppu_mm_take(); fallback(&m, 0x10000);
    ppu_vm_size = 0; m = ppu_mm_take();
    assert(m.ld_span == 0xe0000000LL - 0x10000);
    fallback(&m, 0xe0000000);
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="ppu-load32-") as directory:
        source = Path(directory) / "load32.cpp"
        binary = Path(directory) / "load32"
        source.write_text(fixture + helpers + checks)
        for compiler in compilers:
            subprocess.run([compiler, "-O3", "-fsanitize=undefined,address",
                            "-fno-sanitize-recover=all",
                            str(source), "-o", str(binary)], check=True)
            environment = dict(os.environ, ASAN_OPTIONS="detect_leaks=0")
            subprocess.run([str(binary)], check=True, env=environment)
    print("PPU load32 boundaries, alignment, byte order and fallback checks passed")


if __name__ == "__main__":
    main()
