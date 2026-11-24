#pragma once
/* Player settings that persist between launches include the FPS overlay,
 * fullscreen, windowed size, internal render scale and optional FXAA. TB_SETTINGS_FILE names the file (scripts/port.py run points
 * it at <gamedata root>/settings.ini); without it nothing is read or written.
 * Explicit environment settings (TB_FPS_OVERLAY, TB_FXAA, PS3RECOMP_FULLSCREEN,
 * TB_RENDER_SCALE) always
 * win, so measurement helpers stay deterministic. Nothing guest-visible. */
#ifdef __cplusplus
extern "C" {
#endif
void tb_settings_init(void);
int tb_settings_window_size(int* width, int* height);
int tb_settings_window_preset(int* width, int* height);
void tb_settings_note_overlay(int on);
void tb_settings_note_fullscreen(int on);
void tb_settings_note_window(int width, int height);
int tb_settings_antialiasing(void);
void tb_settings_note_antialiasing(int on);
/* Validated percentage: 100 (Native), 150, 200 or 300. Renderer use follows later. */
int tb_settings_render_scale(void);
#ifdef __cplusplus
}
#endif

#ifdef __cplusplus
#include <array>
#include <filesystem>
#include <string>
namespace tb::display {
enum class Mode { windowed = 0, borderless = 1 };
enum class WindowSize { remember = 0, hd = 720, full_hd = 1080, qhd = 1440 };
struct WindowPreset { WindowSize choice; int width, height; };
// Initial SDL window-coordinate requests; a tiling compositor chooses its final size.
inline constexpr std::array<WindowPreset, 4> window_presets{{
    {WindowSize::remember, 0, 0}, {WindowSize::hd, 1280, 720},
    {WindowSize::full_hd, 1920, 1080}, {WindowSize::qhd, 2560, 1440}}};
// Percentages deliberately avoid resolution labels until scene dimensions are verified.
inline constexpr std::array<int, 4> render_presets{100, 150, 200, 300};
struct Preferences {
    Mode mode = Mode::windowed;
    WindowSize window_size = WindowSize::remember;
    int render_scale = 100;
};
// File preferences, without runtime environment overrides. Missing files use defaults.
// Saving merges existing FPS, FXAA and remembered dimensions and replaces atomically.
bool load(const std::filesystem::path& path, Preferences& preferences, std::string& error);
bool save(const std::filesystem::path& path, const Preferences& preferences, std::string& error);
}
#endif
