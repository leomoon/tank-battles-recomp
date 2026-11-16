/* Persisted player settings (player_settings.h) from the Vulkan window code.
 * Weak no-ops keep standalone backend fixtures linking without the port. */
__attribute__((weak)) int tb_settings_window_size(int* width, int* height) { (void)width; (void)height; return 0; }
__attribute__((weak)) void tb_settings_note_fullscreen(int on) { (void)on; }
__attribute__((weak)) void tb_settings_note_window(int width, int height) { (void)width; (void)height; }
