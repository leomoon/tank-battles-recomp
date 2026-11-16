"""Check the page-protection census on real faults, without game data.

Threads standing in for translated PPU code, SPU DMA (memcpy) and the walker
read and write protected surface memory; every access must complete with the
exact bytes, be recorded once per arm with the right classification, and
leave unrelated faults to the previously installed handler.
"""
import os
import subprocess
from port import ROOT
from page_census_report import last_snapshot

out = ROOT / 'build-tests/page-tracking'
out.mkdir(parents=True, exist_ok=True)
source = r'''
#include "page_census.h"
#include <assert.h>
#include <setjmp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <thread>
#include <vector>

static sigjmp_buf jump;
static volatile int previous_called;
static void previous(int) { previous_called = 1; siglongjmp(jump, 1); }

int main() {
    // The crash handler installed first, as boot_diagnostics does.
    struct sigaction old {};
    old.sa_handler = previous;
    sigemptyset(&old.sa_mask);
    sigaction(SIGSEGV, &old, nullptr);
    setenv("TB_PAGE_CENSUS", "1", 1);
    assert(tb_page_census_on());

    const size_t page = 4096, size = 16 * page;
    unsigned char* arena = (unsigned char*)mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(arena != MAP_FAILED);
    for (size_t i = 0; i < size; i++) arena[i] = (unsigned char)(i * 7);
    // Surface A: pages 1..3 (not page aligned at its end); surface B starts on A's last page.
    unsigned char* a = arena + page;
    unsigned char* b = arena + 3 * page + 2048;
    auto arm = [&] {
        tb_page_census_walker();
        tb_page_census_begin();
        tb_page_census_add(a, 2 * page + 1024, TB_CENSUS_COLOR, 0, 0x1000, 32, 32, 128);
        tb_page_census_add(b, 4 * page, TB_CENSUS_DEPTH, 0, 0x3800, 64, 64, 256);
        tb_page_census_add(arena + 12 * page, 2 * page - 100, TB_CENSUS_COLOR, 1, 0xC000, 16, 16, 64);
        tb_page_census_commit();
    };
    arm();
    // Reader thread (translated PPU load): exact bytes.
    std::thread([&] { assert(a[5] == (unsigned char)((page + 5) * 7)); }).join();
    // Repeated accesses after the first fault do not fault again.
    for (int i = 0; i < 100; i++) assert(a[100 + i] == (unsigned char)((page + 100 + i) * 7));
    // Writer thread (SPU DMA style memcpy) into B; contents land exactly.
    std::thread([&] { unsigned char row[64]; memset(row, 0xAB, sizeof row); memcpy(b + 4096, row, sizeof row); }).join();
    assert(b[4096] == 0xAB && b[4096 + 63] == 0xAB);
    // A neighbour on surface C's page but outside its bytes is a shared-page access.
    arena[12 * page - 1] = 1;           // page 11: unprotected, no fault
    std::thread([&] { (void)*(volatile unsigned char*)(arena + 14 * page - 1); }).join();
    // The walker's own access is classified as walker; re-arming protects again.
    arm();
    assert(a[7] == (unsigned char)((page + 7) * 7));
    std::thread([&] { a[9] = 42; }).join();
    assert(a[9] == 42);
    // Many threads hitting one surface at once.
    arm();
    std::vector<std::thread> crowd;
    for (int t = 0; t < 8; t++) crowd.emplace_back([&, t] { for (int i = 0; i < 1000; i++) (void)*(volatile unsigned char*)(b + (t * 997 + i) % (4 * page)); });
    for (auto& t : crowd) t.join();
    // A fault outside every surface still reaches the previous handler.
    if (!sigsetjmp(jump, 1)) *(volatile int*)8 = 1;
    assert(previous_called);
    tb_page_census_commit();            // flush the counts
    return 0;
}
'''
(out / 'test.cpp').write_text(source)
subprocess.run(['c++', '-std=c++20', '-O1', '-I' + str(ROOT), str(out / 'test.cpp'), str(ROOT / 'page_census.cpp'),
                '-pthread', '-o', str(out / 'test')], check=True)
result = subprocess.run([str(out / 'test')], capture_output=True, text=True, timeout=30,
                        env={k: v for k, v in os.environ.items() if not k.startswith('TB_')})
if result.returncode:
    raise SystemExit('page tracking fixture failed:\n' + result.stderr)
rows = last_snapshot(result.stderr)
def count(**want):
    return sum(r['count'] for r in rows if all((r['who'] == 'walker') == (v == 'walker') if k == 'who' else r[k] == v
                                                for k, v in want.items()))
assert count(who='other', access='read', where='inside', offset=0x1000) == 1, rows
assert count(who='other', access='write', where='inside', offset=0x3800) == 1, rows
assert count(who='other', access='read', where='shared-page', offset=0xC000) == 1, rows
assert count(who='walker', access='read', offset=0x1000) == 1, rows
assert count(who='other', offset=0x3800, kind='depth') >= 2, rows   # second arm: crowd, at most once per arm
assert count(who='other', offset=0x3800) <= 3, rows
print('PASS: protected surface reads/writes from other threads, walker and memcpy return exact bytes, are recorded once per arm,')
print('PASS: shared pages are classified apart, concurrent faults are safe and unrelated faults reach the previous handler')
