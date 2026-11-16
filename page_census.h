#pragma once
/* Page-protection census of guest accesses to RSX surface memory (Linux).
 *
 * TB_PAGE_CENSUS=1 protects the guest bytes of every draw-engine colour and
 * depth surface (PROT_NONE) at each guest flip. The first access to a
 * protected surface after that faults; the handler records which thread
 * touched which surface (read or write, inside the surface or only on a page
 * it shares), restores access to that surface and lets the access retry. Guest
 * memory is never left stale by this mode, so the run renders exactly as an
 * ordinary one. Counts go to stderr as "[page-census]" lines every few
 * seconds and at exit; scripts/page_census_report.py summarizes them.
 *
 * It answers one question with page-level coverage of every CPU path at once
 * (translated PPU code, SPU DMA, HLE/syscall copies, the walker's own
 * transfers): who reads or writes render-target memory between frames. Kernel
 * accesses (a read() into a protected page) would fail with EFAULT instead of
 * faulting, which is why this is a diagnostic mode only. */
#include <stdint.h>

#define TB_CENSUS_COLOR 1u
#define TB_CENSUS_DEPTH 2u

#ifdef __cplusplus
extern "C" {
#endif
int tb_page_census_on(void);
/* Walker only: replace the protected set with these surfaces. */
void tb_page_census_begin(void);
void tb_page_census_add(const void* host, uint32_t bytes, uint32_t kind, uint32_t location,
                        uint32_t offset, uint32_t w, uint32_t h, uint32_t pitch);
void tb_page_census_commit(void);
/* The walker thread's id, so its own accesses are classified apart. */
void tb_page_census_walker(void);
#ifdef __cplusplus
}
#endif
