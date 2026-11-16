/* TB_PAGE_CENSUS (page_census.h): at each guest flip, protect the guest bytes
 * of every engine colour and depth surface so the next CPU access to them is
 * recorded. Walker thread, like every other draw-engine entry point. */
#include "../page_census.h"
/* Standalone engine fixtures link this file without the port's census. */
__attribute__((weak)) int tb_page_census_on(void) { return 0; }
__attribute__((weak)) void tb_page_census_walker(void) {}
__attribute__((weak)) void tb_page_census_begin(void) {}
__attribute__((weak)) void tb_page_census_add(const void* h, uint32_t b, uint32_t k, uint32_t l, uint32_t o,
                                              uint32_t w, uint32_t hh, uint32_t p)
{ (void)h; (void)b; (void)k; (void)l; (void)o; (void)w; (void)hh; (void)p; }
__attribute__((weak)) void tb_page_census_commit(void) {}
static void tb_page_census_arm(void)
{
    if (!g.ready || !tb_page_census_on()) return;
    tb_page_census_walker();
    tb_page_census_begin();
    for (u32 i = 0; i < g.n_surfaces; i++) {
        const eng_surface* s = &g.surfaces[i];
        const u32 bytes = s->pitch * s->h;
        if (!s->handle || !bytes) continue;
        tb_page_census_add(eng_guest_ptr(NULL, s->location, s->offset, bytes), bytes, TB_CENSUS_COLOR,
                           s->location, s->offset, s->w, s->h, s->pitch);
    }
    for (u32 i = 0; i < g.n_zdepths; i++) {
        const eng_zdepth* z = &g.zdepths[i];
        const u32 bytes = z->pitch * z->h;
        if (!z->handle || !bytes) continue;
        tb_page_census_add(eng_guest_ptr(NULL, z->location, z->offset, bytes), bytes, TB_CENSUS_DEPTH,
                           z->location, z->offset, z->w, z->h, z->pitch);
    }
    tb_page_census_commit();
}
