/* Observe title-specific draw and complete GPU resolve routes before asking
 * Vulkan to reserve any scaled resources. No guest address is hardcoded. */
#include "render_scale.h"
#include "render_scale_shader.h"

__attribute__((weak)) int tb_vk_scale_requested(void) { return 100; }
__attribute__((weak)) void tb_vk_scale_discard(void) {}
__attribute__((weak)) void tb_vk_scale_prepare(const tb_scale_layout* layout, unsigned percent)
{ (void)layout; (void)percent; }
__attribute__((weak)) void tb_vk_scale_unavailable(void) {}

__attribute__((weak)) void tb_vk_scale_clear(tb_scale_surface color, tb_scale_surface depth,
        tb_scale_rect rect, unsigned mask, const float rgba[4], float z, unsigned stencil)
{ (void)color; (void)depth; (void)rect; (void)mask; (void)rgba; (void)z; (void)stencil; }
__attribute__((weak)) int tb_vk_scale_bindings(tb_scale_surface color, tb_scale_surface depth,
        int stencil, tb_scale_binding* binding)
{ (void)color; (void)depth; (void)stencil; (void)binding; return 0; }
__attribute__((weak)) void tb_vk_scale_skip_frame(void) {}
__attribute__((weak)) void tb_vk_scale_capture(tb_scale_surface color, const char* path)
{ (void)color; (void)path; }

typedef struct {
    tb_scale_surface color, depth;
} tb_scale_draw;

typedef struct {
    tb_scale_route route;
    tb_scale_surface depth;
    unsigned tiles;
} tb_scale_resolve;

static struct {
    unsigned percent;
    u32 generation, waiting;
    tb_scale_draw draws[ENG_MAX_SURFACES];
    tb_scale_resolve pending[TB_SCALE_MAX_ROUTES];
    tb_scale_resolve proven[TB_SCALE_MAX_ROUTES];
} tb_scale_scene;

static tb_scale_surface tb_scale_color_id(const eng_surface* s)
{
    return (tb_scale_surface){s->location, s->offset, s->pitch, s->fmt,
                              s->scale_generation, {s->w, s->h}};
}

static tb_scale_surface tb_scale_depth_id(const eng_zdepth* z)
{
    return (tb_scale_surface){z->location, z->offset, z->pitch, z->zfmt,
                              z->scale_generation, {z->w, z->h}};
}

static int tb_scale_same_surface(tb_scale_surface a, tb_scale_surface b)
{
    return a.location == b.location && a.offset == b.offset && a.pitch == b.pitch &&
           a.format == b.format && a.generation == b.generation &&
           a.size.width == b.size.width && a.size.height == b.size.height;
}

static int tb_scale_display_matches(const eng_display_buffer* d, tb_scale_surface s)
{
    return d->valid && d->location == s.location && d->offset == s.offset &&
           d->pitch == s.pitch && d->width == s.size.width && d->height == s.size.height;
}

static int tb_scale_overlaps(tb_scale_surface surface, u32 location, u32 offset, u64 size)
{
    return surface.generation && surface.location == location && size &&
           (u64)offset < (u64)surface.offset + (u64)surface.pitch * surface.size.height &&
           (u64)surface.offset < (u64)offset + size;
}

static void tb_scale_forget_route(u32 buffer)
{
    if (tb_scale_scene.proven[buffer].tiles) tb_vk_scale_discard();
    tb_scale_scene.pending[buffer] = (tb_scale_resolve){0};
    tb_scale_scene.proven[buffer] = (tb_scale_resolve){0};
}

static void tb_scale_guest_write(u32 location, u32 offset, u32 pitch, u32 w, u32 h)
{
    if (tb_scale_scene.percent <= 100) return;
    const u64 size = (u64)(h - 1) * pitch + (u64)w * 4;
    for (u32 i = 0; i < TB_SCALE_MAX_ROUTES; ++i) {
        const tb_scale_resolve* p = &tb_scale_scene.pending[i];
        const tb_scale_resolve* r = &tb_scale_scene.proven[i];
        if (tb_scale_overlaps(r->route.color, location, offset, size) ||
            tb_scale_overlaps(r->route.display, location, offset, size) ||
            tb_scale_overlaps(r->depth, location, offset, size) ||
            tb_scale_overlaps(p->route.color, location, offset, size) ||
            tb_scale_overlaps(p->route.display, location, offset, size) ||
            tb_scale_overlaps(p->depth, location, offset, size))
            tb_scale_forget_route(i);
    }
}

static int tb_scale_scene_pair(const rsx_dsp_surface* sf, tb_scale_surface color, tb_scale_surface depth)
{
    // A matching NV3089 resolve to a registered buffer is still required.
    return sf->color_target == CELL_GCM_SURFACE_TARGET_1 &&
        sf->aa_sx == 2 && sf->aa_sy == 2 && sf->depth_format == 2 &&
        color.format == RSX_BE_FMT_R8G8B8A8 && color.location == depth.location &&
        color.size.width == 2560 && color.size.height == 1440 &&
        depth.size.width == color.size.width && depth.size.height == color.size.height &&
        color.pitch == 4 * color.size.width && depth.pitch == color.pitch;
}

static void tb_scale_note_draw(const rsx_dsp_surface* sf, u32 slot, u32 zslot)
{
    if (tb_scale_scene.percent <= 100) return;
    const tb_scale_surface color = tb_scale_color_id(&g.surfaces[slot]);
    const tb_scale_surface depth = zslot == ENG_INVALID
        ? (tb_scale_surface){0} : tb_scale_depth_id(&g.zdepths[zslot]);
    const int scene = tb_scale_scene_pair(sf, color, depth);
    tb_scale_scene.draws[slot] = scene ? (tb_scale_draw){color, depth} : (tb_scale_draw){0};
    for (u32 i = 0; i < TB_SCALE_MAX_ROUTES; ++i) {
        const tb_scale_resolve* r = &tb_scale_scene.proven[i];
        if (!r->tiles) continue;
        if (tb_scale_overlaps(r->route.display, color.location, color.offset, (u64)color.pitch * color.size.height) ||
            (!scene && (tb_scale_overlaps(r->route.color, color.location, color.offset, (u64)color.pitch * color.size.height) ||
                        tb_scale_overlaps(r->depth, depth.location, depth.offset, (u64)depth.pitch * depth.size.height))))
            tb_scale_forget_route(i);
    }
}

static void tb_scale_note_resolve(u32 ss, u32 ds, u32 sx, u32 sy, u32 dx, u32 dy,
                                  u32 kx, u32 ky, u32 width, u32 height)
{
    if (tb_scale_scene.percent <= 100) return;
    const tb_scale_surface display = tb_scale_color_id(&g.surfaces[ds]);
    for (u32 i = 0; i < TB_SCALE_MAX_ROUTES; ++i) {
        if (!tb_scale_display_matches(&g.display_buffers[i], display)) continue;
        const tb_scale_draw draw = tb_scale_scene.draws[ss];
        if (!draw.color.generation || !tb_scale_same_surface(draw.color, tb_scale_color_id(&g.surfaces[ss])) ||
            display.size.width != 1280 || display.size.height != 720 || display.pitch != 5120 ||
            kx != 2 || ky != 2 || sx != dx * 2 + 1 || sy != dy * 2 + 1 ||
            dx % 512 || dy % 512 || dx >= 1280 || dy >= 720 ||
            width != (dx == 1024 ? 256 : 512) || height != (dy == 512 ? 208 : 512)) {
            tb_scale_forget_route(i);
            return;
        }
        const unsigned tile = 1u << (dy / 512 * 3 + dx / 512);
        tb_scale_resolve* p = &tb_scale_scene.pending[i];
        const tb_scale_resolve* r = &tb_scale_scene.proven[i];
        if (r->tiles && (!tb_scale_same_surface(r->route.color, draw.color) ||
                         !tb_scale_same_surface(r->route.display, display) ||
                         !tb_scale_same_surface(r->depth, draw.depth)))
            tb_scale_forget_route(i);
        if (p->tiles && ((p->tiles & tile) || !tb_scale_same_surface(p->route.color, draw.color) ||
                         !tb_scale_same_surface(p->route.display, display) || !tb_scale_same_surface(p->depth, draw.depth)))
            tb_scale_forget_route(i);
        p->route = (tb_scale_route){draw.color, display};
        p->depth = draw.depth;
        p->tiles |= tile;
        return;
    }
}

static int tb_scale_live_color(tb_scale_surface surface)
{
    for (u32 i = 0; i < g.n_surfaces; ++i)
        if (g.surfaces[i].handle && tb_scale_same_surface(surface, tb_scale_color_id(&g.surfaces[i]))) return 1;
    return 0;
}

static int tb_scale_live_depth(tb_scale_surface surface)
{
    for (u32 i = 0; i < g.n_zdepths; ++i)
        if (g.zdepths[i].handle && tb_scale_same_surface(surface, tb_scale_depth_id(&g.zdepths[i]))) return 1;
    return 0;
}

static int tb_scale_present(u32 buffer, u32 target)
{
    if (tb_scale_scene.percent <= 100 || buffer >= TB_SCALE_MAX_ROUTES) return 0;
    int resolved = 0;
    tb_scale_resolve* pending = &tb_scale_scene.pending[buffer];
    if (pending->tiles) {
        if (pending->tiles == 0x3f &&
            tb_scale_same_surface(pending->route.display, tb_scale_color_id(&g.surfaces[target]))) {
            tb_scale_scene.proven[buffer] = *pending;
            *pending = (tb_scale_resolve){0};
            resolved = 1;
        } else tb_scale_forget_route(buffer);
    }
    tb_scale_layout layout = {0};
    unsigned registered = 0;
    for (u32 i = 0; i < TB_SCALE_MAX_ROUTES; ++i) {
        const eng_display_buffer* d = &g.display_buffers[i];
        const tb_scale_resolve* r = &tb_scale_scene.proven[i];
        registered += !!d->valid;
        if (!r->tiles) continue;
        if (!tb_scale_display_matches(d, r->route.display) || !tb_scale_live_color(r->route.color) ||
            !tb_scale_live_color(r->route.display) || !tb_scale_live_depth(r->depth) ||
            (layout.count && !tb_scale_same_surface(layout.depth, r->depth))) {
            tb_scale_forget_route(i);
            continue;
        }
        layout.depth = r->depth;
        layout.routes[layout.count++] = r->route;
    }
    if (layout.count && layout.count == registered) {
        tb_vk_scale_prepare(&layout, tb_scale_scene.percent);
        tb_scale_scene.percent = tb_vk_scale_requested();
        tb_scale_scene.waiting = 0;
    } else {
        tb_vk_scale_discard();
        if (++tb_scale_scene.waiting == 600) {
            tb_vk_scale_unavailable();
            tb_scale_scene.percent = 100;
        }
    }
    return resolved && tb_scale_scene.proven[buffer].tiles;
}
