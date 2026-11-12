#include "hle_inventory.h"
#include <cstdio>
#include <cstdlib>
#if defined(__linux__)
#include <atomic>
#include <string>
#include <vector>
struct ppu_context;
using ContextFn = void (*)(ppu_context*);
using FastFn = int (*)(ppu_context*);
struct Registration { const char* kind; uint32_t nid; std::string name; };
static std::atomic<bool> frozen{false};
static bool capture_enabled() {
    static const bool requested = getenv("TB_HLE_INVENTORY") != nullptr;
    return requested && !frozen.load();
}
static std::vector<Registration>& registrations() {
    static std::vector<Registration> records;
    return records;
}
extern "C" void __real_ps3_hle_register(uint32_t, const char*, void*);
extern "C" void __real_ps3_hle_register_ctx(uint32_t, const char*, ContextFn);
extern "C" void __real_ps3_hle_register_fast(uint32_t, FastFn);
extern "C" int ps3_hle_has(uint32_t);
extern "C" void __wrap_ps3_hle_register(uint32_t nid, const char* name, void* handler) {
    __real_ps3_hle_register(nid, name, handler);
    if (handler && capture_enabled()) registrations().push_back({"generic", nid, name ? name : "?"});
}
extern "C" void __wrap_ps3_hle_register_ctx(uint32_t nid, const char* name, ContextFn handler) {
    __real_ps3_hle_register_ctx(nid, name, handler);
    if (handler && capture_enabled()) registrations().push_back({"context", nid, name ? name : "?"});
}
extern "C" void __wrap_ps3_hle_register_fast(uint32_t nid, FastFn handler) {
    __real_ps3_hle_register_fast(nid, handler);
    if (handler && capture_enabled()) registrations().push_back({"fast", nid, "fast handler"});
}
void tank_battles_record_override(uint32_t nid, uint32_t alias) {
    if (!capture_enabled()) return;
    char name[16]; snprintf(name, sizeof name, "0x%08X", alias);
    registrations().push_back({"override", nid, name});
}
bool tank_battles_write_hle_inventory() {
    const char* output = getenv("TB_HLE_INVENTORY");
    if (!output) return true;
    frozen.store(true); // Snapshot startup registrations before guest threads are dispatched.
    FILE* stream = fopen(output, "w");
    if (!stream) { perror("[audit] inventory output"); return false; }
    for (const auto& record : registrations()) {
        std::string name = record.name;
        for (char& c : name) if (c == '\t' || c == '\n' || c == '\r') c = ' ';
        fprintf(stream, "%s\t0x%08X\t%s\n", record.kind, record.nid, name.c_str());
    }
    if (const char* input = getenv("TB_IMPORT_PROBE")) {
        FILE* probes = fopen(input, "r");
        if (!probes) { perror("[audit] probe input"); fclose(stream); return false; }
        unsigned nid;
        while (fscanf(probes, "%x", &nid) == 1)
            fprintf(stream, "probe\t0x%08X\t%d\n", nid, ps3_hle_has(nid));
        if (!feof(probes)) { fclose(probes); fclose(stream); return false; }
        fclose(probes);
    }
    return fclose(stream) == 0;
}
#else
void tank_battles_record_override(uint32_t, uint32_t) {}
bool tank_battles_write_hle_inventory() { return getenv("TB_HLE_INVENTORY") == nullptr; }
#endif
