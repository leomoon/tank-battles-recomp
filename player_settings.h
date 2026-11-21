#pragma once
/* Player settings that persist between launches include the FPS overlay,
 * fullscreen, windowed size and optional FXAA. TB_SETTINGS_FILE names the file (scripts/port.py run points
 * it at <gamedata root>/settings.ini); without it nothing is read or written.
 * Explicit environment settings (TB_FPS_OVERLAY, TB_FXAA, PS3RECOMP_FULLSCREEN) always
 * win, so measurement helpers stay deterministic. Nothing guest-visible. */
#ifdef __cplusplus
extern "C" {
#endif
void tb_settings_init(void);
int tb_settings_window_size(int* width, int* height);
void tb_settings_note_overlay(int on);
void tb_settings_note_fullscreen(int on);
void tb_settings_note_window(int width, int height);
int tb_settings_antialiasing(void);
void tb_settings_note_antialiasing(int on);
#ifdef __cplusplus
}
#endif
