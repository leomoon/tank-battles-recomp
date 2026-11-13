#include "content_paths.h"
#include "stubs.h"
#include "ppu_recomp.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <string>
namespace fs = std::filesystem;
extern "C" {
void cellGame_init_from_paramsfo(const char*);
const char* cellGame_get_title_id();
const char* cellGame_get_hdd_title_id();
void cellfs_set_root_path(const char*);
void cellfs_add_path_mapping(const char*, const char*);
int cellfs_translate_path(const char*, char*, size_t);
void sys_fs_translate_path(const char*, char*, int);
int edat_is_npd(const char*);
const char* edat_resolve(const char*, char*, size_t);
extern const char* ppu_vfs_root;
}
static std::string vfs_storage;
static void default_env(const char* key, const std::string& value) {
    if (const char* existing = getenv(key); existing && *existing) return;
#ifdef _WIN32
    _putenv_s(key, value.c_str());
#else
    setenv(key, value.c_str(), 1);
#endif
}
static std::string configured(const char* key, const fs::path& fallback) {
    default_env(key, fallback.string());
    return fs::absolute(fs::path(getenv(key))).lexically_normal().string();
}
bool tank_battles_init_content(const char* elf) {
    fs::path sfo;
    if (const char* path = getenv("PS3_PARAM_SFO"); path && *path) sfo = path;
    else { const auto folder=fs::path(elf).parent_path();
           sfo=(folder.filename()=="USRDIR" ? folder.parent_path() : folder)/"PARAM.SFO"; }
    std::error_code error;
    if (!fs::is_regular_file(sfo, error)) {
        fprintf(stderr, "[content] Missing PARAM.SFO: %s\n", sfo.string().c_str());
        return false;
    }
    sfo = fs::absolute(sfo).lexically_normal();
    cellGame_init_from_paramsfo(sfo.string().c_str());
    const char* id = cellGame_get_title_id();
    if (!id || std::string(id) == "BLES00000") {
        fprintf(stderr, "[content] Could not initialize title metadata\n"); return false;
    }
    if (const char* expected = getenv("TB_EXPECTED_TITLE_ID"); expected && strcmp(expected, id)) {
        fprintf(stderr, "[content] PARAM.SFO title does not match configured title\n"); return false;
    }
    const auto title = sfo.parent_path();
    const auto hdd = cellGame_get_hdd_title_id() ? title.parent_path().parent_path() : title/"gamedata/dev_hdd0";
    vfs_storage = configured("PS3_VFS_ROOT", title);
    ppu_vfs_root = vfs_storage.c_str();
    const auto hdd0 = configured("PS3_HDD0_ROOT", hdd);
    const auto data = configured("PS3_GAMEDATA_ROOT", fs::path(hdd0).parent_path());
    const auto hdd1 = configured("PS3_HDD1_ROOT", fs::path(data) / "dev_hdd1");
    const auto flash = configured("PS3_DEV_FLASH", fs::path(data) / "dev_flash");
    default_env("PS3_BOOT_PATH", cellGame_get_hdd_title_id()
                ? "/dev_hdd0/game/" + std::string(id) + "/USRDIR/EBOOT.BIN"
                : "/dev_bdvd/PS3_GAME/USRDIR/EBOOT.BIN");
    // Configure the generic C layer too: its defaults bypass the PPU/syscall mounts.
    cellfs_set_root_path((title / "USRDIR").string().c_str());
    cellfs_add_path_mapping("/dev_hdd0/", hdd0.c_str());
    cellfs_add_path_mapping("/dev_hdd1/", hdd1.c_str());
    cellfs_add_path_mapping("/dev_bdvd/", vfs_storage.c_str());
    cellfs_add_path_mapping("/app_home/", (title / "USRDIR").string().c_str());
    cellfs_add_path_mapping("/dev_flash/", flash.c_str());
    cellfs_add_path_mapping("/dev_usb000/", (fs::path(data) / "dev_usb000").string().c_str());
    fprintf(stderr, "[content] title=%s hdd=%d boot=%s\n", id,
            cellGame_get_hdd_title_id() != nullptr, getenv("PS3_BOOT_PATH"));
    return true;
}
#if defined(__linux__)
extern "C" void __real_ps3_hle_call(uint32_t, ppu_context*);
static void asset_available(uint32_t nid, ppu_context* context) {
    const auto path_ea = static_cast<uint32_t>(context->gpr[4]);
    __real_ps3_hle_call(nid, context);
    if (static_cast<uint32_t>(context->gpr[3]) != 0 || !path_ea) return;
    char path[1024]; size_t length = 0;
    for (; length + 1 < sizeof path; ++length) {
        path[length] = static_cast<char>(vm_read8(path_ea + length));
        if (!path[length]) break;
    }
    if (length + 1 >= sizeof path) return;
    char host[1200], cache[1200];
    sys_fs_translate_path(path, host, sizeof host);
    if (!edat_is_npd(host)) return;
    const char* plain = edat_resolve(host, cache, sizeof cache);
    if (plain == host) return; // Resolver did not provide a plaintext cache.
    // The C file layer does not call edat_resolve. Adopt only the verified
    // plaintext cache after the game's own availability call registered its key.
    // Existing caches follow the SDK mtime policy; fresh decryptions verify blocks.
    cellfs_add_path_mapping(path, plain);
    char actual[1200];
    if (cellfs_translate_path(path, actual, sizeof actual) != 0 || strcmp(actual, plain))
        fprintf(stderr, "[content] EDAT mapping capacity exhausted: %s\n", path);
}
static void available1(ppu_context* context) { asset_available(0xAD218FAFu, context); }
static void available2(ppu_context* context) { asset_available(0xF042B14Fu, context); }
#endif
void tank_battles_register_asset_overrides() {
#if defined(__linux__)
    tank_battles_register_override(0xAD218FAFu, available1);
    tank_battles_register_override(0xF042B14Fu, available2);
#endif
}
