// Shared launcher/game preferences, exercised on POSIX and Windows.
#include "player_settings.h"
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#ifdef _WIN32
#include <windows.h>
#else
#include <unistd.h>
#endif
#define CHECK(value) do { if (!(value)) throw std::runtime_error("Check failed at line " + std::to_string(__LINE__) + ": " #value); } while (0)
namespace fs = std::filesystem;
using namespace tb::display;
static std::string utf8(const fs::path& p) { const auto s=p.u8string(); return {s.begin(),s.end()}; }
static void env(const char* name, const std::string& value) {
#ifdef _WIN32
    _putenv_s(name,value.c_str());
#else
    setenv(name,value.c_str(),1);
#endif
}
static void clear(const char* name) {
#ifdef _WIN32
    _putenv_s(name,"");
#else
    unsetenv(name);
#endif
}
static std::string read(const fs::path& p) { std::ifstream f(p); return {std::istreambuf_iterator<char>(f),{}}; }
int main(int argc,char** argv) {
 try {
  CHECK(argc==2);
  const fs::path root=fs::path(argv[1])/u8"display settings 測試";
  fs::create_directories(root);
  const auto file=root/"settings.ini";
  fs::remove(file);
  std::string error;
  Preferences p;
  CHECK(load(file,p,error)); CHECK(error.empty());
  CHECK(p.mode==Mode::windowed && p.window_size==WindowSize::remember && p.render_scale==100);
  CHECK(!fs::exists(file));
  { std::ofstream f(file); f<<"overlay = 1\nfullscreen = 0\nantialiasing = 1\nwindow_width = 1366\nwindow_height = 768\n"; }
  for (const int scale:render_presets) for (const auto preset:window_presets) {
   p={Mode::borderless,preset.choice,scale};
   CHECK(save(file,p,error));
   Preferences q; CHECK(load(file,q,error));
   CHECK(q.mode==p.mode && q.window_size==p.window_size && q.render_scale==scale);
   const auto text=read(file);
   CHECK(text.find("overlay = 1")!=text.npos && text.find("antialiasing = 1")!=text.npos);
   CHECK(text.find("window_width = 1366")!=text.npos && text.find("window_height = 768")!=text.npos);
  }
  const auto good=read(file);
  for (const int scale:{0,99,101,400,-1}) { p.render_scale=scale; CHECK(!save(file,p,error)); CHECK(!error.empty()); CHECK(read(file)==good); }
  p={Mode::windowed,WindowSize::remember,100}; p.mode=static_cast<Mode>(9); CHECK(!save(file,p,error));
  p={Mode::windowed,static_cast<WindowSize>(900),100}; CHECK(!save(file,p,error)); CHECK(read(file)==good);
  { std::ofstream f(file); f<<"render_scale = 200junk\nwindow_preset = 9999999999999999999999\nfullscreen = -1\nwindow_width = 1000\nwindow_height = -1\n"; }
  CHECK(load(file,p,error)); CHECK(p.render_scale==100 && p.mode==Mode::windowed && p.window_size==WindowSize::remember);
  env("TB_SETTINGS_FILE",utf8(file)); clear("TB_RENDER_SCALE"); clear("TB_FXAA"); clear("PS3RECOMP_FULLSCREEN"); clear("PS3RECOMP_VK_FULLSCREEN"); clear("TB_FPS_OVERLAY");
  tb_settings_init(); int w=0,h=0; CHECK(!tb_settings_window_size(&w,&h)); CHECK(tb_settings_render_scale()==100);
  p={Mode::borderless,WindowSize::full_hd,200}; CHECK(save(file,p,error));
  const auto saved=read(file);
  env("TB_RENDER_SCALE","150"); env("TB_FXAA","1"); env("PS3RECOMP_FULLSCREEN","0");
  tb_settings_init(); CHECK(tb_settings_render_scale()==150 && tb_settings_antialiasing()==1);
  CHECK(std::string(getenv("PS3RECOMP_FULLSCREEN"))=="0"); CHECK(read(file)==saved);
  CHECK(load(file,p,error)); CHECK(p.render_scale==200 && p.mode==Mode::borderless);
  env("TB_RENDER_SCALE","200x"); tb_settings_init(); CHECK(tb_settings_render_scale()==100);
  clear("TB_RENDER_SCALE"); tb_settings_init(); CHECK(tb_settings_render_scale()==200);
  int requested_w=0,requested_h=0;
  CHECK(tb_settings_window_preset(&requested_w,&requested_h));
  CHECK(requested_w==1920 && requested_h==1080);
  clear("TB_SETTINGS_FILE"); env("PS3_GAMEDATA_ROOT",utf8(root));
  tb_settings_init(); CHECK(tb_settings_render_scale()==200);
  env("TB_SETTINGS_FILE",utf8(file));
  tb_settings_note_overlay(1); tb_settings_note_fullscreen(0); tb_settings_note_window(1600,900); tb_settings_note_antialiasing(0);
  CHECK(load(file,p,error)); CHECK(p.mode==Mode::windowed && p.window_size==WindowSize::full_hd && p.render_scale==200);
  CHECK(read(file).find("window_width = 1600")!=std::string::npos);
  const auto intact=read(file);
#ifdef _WIN32
  // Permit reading the original, but deny its replacement while the save writes a temporary file.
  const auto handle=CreateFileW(file.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,0,nullptr);
  CHECK(handle!=INVALID_HANDLE_VALUE);
  const bool failed=!save(file,p,error);
  CloseHandle(handle);
  CHECK(failed && !error.empty() && read(file)==intact);
#else
  if (geteuid()!=0) {
   const auto permissions=fs::status(root).permissions();
   fs::permissions(root,fs::perms::owner_read|fs::perms::owner_exec);
   const bool failed=!save(file,p,error);
   fs::permissions(root,permissions);
   CHECK(failed && !error.empty() && read(file)==intact);
  }
#endif
  env("TB_RENDER_SCALE","300");
  // Empty TB_SETTINGS_FILE disables persistence on POSIX; Windows removes empty env vars.
#ifndef _WIN32
  env("TB_SETTINGS_FILE","");
#else
  clear("TB_SETTINGS_FILE"); clear("PS3_GAMEDATA_ROOT");
#endif
  tb_settings_init(); CHECK(tb_settings_render_scale()==300); CHECK(!tb_settings_window_size(&w,&h));
  const auto blocker=root/"blocker"; { std::ofstream f(blocker); f<<"keep me"; }
  CHECK(!save(blocker/"settings.ini",p,error)); CHECK(!error.empty()); CHECK(read(blocker)=="keep me");
  const auto directory=root/"directory.ini"; fs::create_directories(directory); { std::ofstream f(directory/"marker");f<<"intact"; }
  CHECK(!save(directory,p,error)); CHECK(read(directory/"marker")=="intact");
  for(const auto& entry:fs::recursive_directory_iterator(root)) CHECK(entry.path().filename().string().find(".tmp.")==std::string::npos);
  std::cout<<"PASS: display defaults, presets, validation, Unicode paths, preservation, overrides and write failures\n";
 } catch (const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
