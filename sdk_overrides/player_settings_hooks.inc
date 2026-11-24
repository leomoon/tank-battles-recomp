/* Persisted player settings (player_settings.h) from the Vulkan window code.
 * Weak no-ops keep standalone backend fixtures linking without the port. */
__attribute__((weak)) int tb_settings_window_size(int* width, int* height) { (void)width; (void)height; return 0; }
__attribute__((weak)) int tb_settings_window_preset(int* width, int* height) { (void)width; (void)height; return 0; }
__attribute__((weak)) void tb_settings_note_fullscreen(int on) { (void)on; }
__attribute__((weak)) void tb_antialiasing_toast(int on) { (void)on; }
__attribute__((weak)) void tb_settings_note_window(int width, int height) { (void)width; (void)height; }
__attribute__((weak)) int tb_settings_antialiasing(void) { const char* e=getenv("TB_FXAA"); return e && atoi(e)!=0; }
__attribute__((weak)) void tb_settings_note_antialiasing(int on) { (void)on; }

/* Window coordinates are distinct from Vulkan drawable pixels. Apply a request
 * once at creation; the compositor owns subsequent tiled/configured sizes. */
static void tb_window_fit_size(int display, int* width, int* height) {
    SDL_Rect area;
    if (display < 0) display = 0;
    if (SDL_GetDisplayUsableBounds(display, &area) != 0 || area.w < 320 || area.h < 180) return;
    if (*width > area.w) { *height = (int)((int64_t)*height * area.w / *width); *width = area.w; }
    if (*height > area.h) { *width = (int)((int64_t)*width * area.h / *height); *height = area.h; }
    if (*width < 320) *width = 320;
    if (*height < 180) *height = 180;
}
static void tb_window_initial_size(int* width, int* height) {
    if (!tb_settings_window_preset(width, height)) tb_settings_window_size(width, height);
    tb_window_fit_size(0, width, height);
}
static int tb_window_pending_size;
static Uint64 tb_window_size_changed;
static void tb_window_changed(void) {
    tb_window_pending_size = 1;
    tb_window_size_changed = SDL_GetTicks64();
}
static void tb_window_save_size(SDL_Window* window) {
    if (!window || !tb_window_pending_size || SDL_GetTicks64() - tb_window_size_changed < 200) return;
    tb_window_pending_size = 0;
    if (!(SDL_GetWindowFlags(window) & (SDL_WINDOW_FULLSCREEN_DESKTOP | SDL_WINDOW_MAXIMIZED | SDL_WINDOW_MINIMIZED))) {
        int width, height;
        SDL_GetWindowSize(window, &width, &height);
        tb_settings_note_window(width, height);
    }
}
static void tb_window_toggle_fullscreen(SDL_Window* window) {
    const Uint32 flags = SDL_GetWindowFlags(window);
    const int full = (flags & SDL_WINDOW_FULLSCREEN_DESKTOP) != 0;
    if (!full && !(flags & (SDL_WINDOW_MAXIMIZED | SDL_WINDOW_MINIMIZED))) {
        int width, height;
        SDL_GetWindowSize(window, &width, &height);
        tb_settings_note_window(width, height);
    }
    if (SDL_SetWindowFullscreen(window, full ? 0 : SDL_WINDOW_FULLSCREEN_DESKTOP) != 0) return;
    SDL_ShowCursor(full ? SDL_ENABLE : SDL_DISABLE);
    tb_settings_note_fullscreen(!full);
    tb_window_pending_size = 0; // SDL restores windowed geometry; await its configure events.
}
