#pragma once
/* Player settings that persist between launches: FPS overlay, fullscreen and
 * windowed size. TB_SETTINGS_FILE names the file (scripts/port.py run points
 * it at <gamedata root>/settings.ini); without it nothing is read or written.
 * Explicit environment settings (TB_FPS_OVERLAY, PS3RECOMP_FULLSCREEN) always
 * win, so measurement helpers stay deterministic. Nothing guest-visible. */
#ifdef __cplusplus
extern "C" {
#endif
void tb_settings_init(void);
int tb_settings_window_size(int* width, int* height);
void tb_settings_note_overlay(int on);
void tb_settings_note_fullscreen(int on);
void tb_settings_note_window(int width, int height);
#ifdef __cplusplus
}
#endif
