// Persisted player settings: player_settings.h.
#include "player_settings.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>

namespace {
struct Settings {
    int overlay = -1, fullscreen = -1, width = 0, height = 0;
    std::string path;
    std::mutex mutex;
};
Settings& settings() { static Settings* s = new Settings; return *s; }   // outlives exit()

void load(Settings& s) {
    FILE* f = fopen(s.path.c_str(), "r");
    if (!f) return;
    char line[256];
    while (fgets(line, sizeof line, f)) {
        char key[64];
        int value;
        if (sscanf(line, " %63[a-z_] = %d", key, &value) != 2) continue;
        if (!strcmp(key, "overlay")) s.overlay = value != 0;
        else if (!strcmp(key, "fullscreen")) s.fullscreen = value != 0;
        else if (!strcmp(key, "window_width") && value >= 320 && value <= 16384) s.width = value;
        else if (!strcmp(key, "window_height") && value >= 180 && value <= 16384) s.height = value;
    }
    fclose(f);
}

// Write a temporary file and rename it over the old one, so a crash mid-write
// never leaves a truncated settings file.
void save(Settings& s) {
    if (s.path.empty()) return;
    const std::string tmp = s.path + ".tmp";
    FILE* f = fopen(tmp.c_str(), "w");
    if (!f) return;
    fprintf(f, "# Tank Battles Recomp player settings, written by the game.\n");
    if (s.overlay >= 0) fprintf(f, "overlay = %d\n", s.overlay);
    if (s.fullscreen >= 0) fprintf(f, "fullscreen = %d\n", s.fullscreen);
    if (s.width && s.height) fprintf(f, "window_width = %d\nwindow_height = %d\n", s.width, s.height);
    const bool ok = fclose(f) == 0;
    if (ok) std::rename(tmp.c_str(), s.path.c_str());
    else std::remove(tmp.c_str());
}
}

extern "C" void tb_settings_init(void) {
    const char* path = getenv("TB_SETTINGS_FILE");
    if (!path || !*path) return;
    Settings& s = settings();
    std::lock_guard<std::mutex> lock(s.mutex);
    s.path = path;
    load(s);
    if (s.overlay >= 0 && !getenv("TB_FPS_OVERLAY")) setenv("TB_FPS_OVERLAY", s.overlay ? "1" : "0", 1);
    if (s.fullscreen > 0 && !getenv("PS3RECOMP_FULLSCREEN") && !getenv("PS3RECOMP_VK_FULLSCREEN"))
        setenv("PS3RECOMP_FULLSCREEN", "1", 1);
}

extern "C" int tb_settings_window_size(int* width, int* height) {
    Settings& s = settings();
    std::lock_guard<std::mutex> lock(s.mutex);
    if (s.path.empty() || !s.width || !s.height) return 0;
    *width = s.width;
    *height = s.height;
    return 1;
}

extern "C" void tb_settings_note_overlay(int on) {
    Settings& s = settings();
    std::lock_guard<std::mutex> lock(s.mutex);
    if (s.path.empty() || s.overlay == (on != 0)) return;
    s.overlay = on != 0;
    save(s);
}

extern "C" void tb_settings_note_fullscreen(int on) {
    Settings& s = settings();
    std::lock_guard<std::mutex> lock(s.mutex);
    if (s.path.empty() || s.fullscreen == (on != 0)) return;
    s.fullscreen = on != 0;
    save(s);
}

extern "C" void tb_settings_note_window(int width, int height) {
    Settings& s = settings();
    std::lock_guard<std::mutex> lock(s.mutex);
    if (s.path.empty() || width < 320 || height < 180 || (s.width == width && s.height == height)) return;
    s.width = width;
    s.height = height;
    save(s);
}
