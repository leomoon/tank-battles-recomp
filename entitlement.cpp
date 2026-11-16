#include "entitlement.h"
#include "stubs.h"
#include "ppu_recomp.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <string>
namespace fs = std::filesystem;
extern "C" int tank_battles_validate_unlock(const char*, const char*);
extern "C" void ps3_hle_call(uint32_t, ppu_context*);
namespace {
constexpr uint32_t invalid_param=0x80029502u, license_missing=0x80029521u;
constexpr uint32_t purchase_unavailable=0x80028F81u;
constexpr char unlock_id[]="UP2006-NPUB30154_00-TANKBATTLES00KEY";
constexpr uint32_t verify_nid=0x2ECD48EDu, purchase_nid=0xF283C143u;
uint32_t verify(uint64_t address) {
    // Guest space is fully backed on this runner, with a guard past 4 GiB.
    if (!address || address > UINT32_MAX-48) return invalid_param;
    if (ppu_vm_size && address+48 > ppu_vm_size) return invalid_param;
    char content[49]{};
    unsigned length=0;
    for (; length<48; ++length) {
        content[length]=static_cast<char>(vm_read8(address+length));
        if (!content[length]) break;
    }
    if (length==48) return invalid_param;
    if (strcmp(content,unlock_id)) return license_missing;
    const char* hdd=getenv("PS3_HDD0_ROOT");
    if (!hdd || !*hdd) return license_missing;
    // Match the port's selected local profile, without searching other accounts.
    const auto file=fs::path(hdd)/"home/00000001/exdata"/(std::string(unlock_id)+".edat");
    std::error_code error;
    if (!fs::is_regular_file(file,error)) {
        fprintf(stderr,"[entitlement] unlock missing; trial result\n");
        return license_missing;
    }
    // The verifier requires an exact 16-byte sidecar for license 2 and
    // authenticates license 3 directly with its platform free key.
    if (!tank_battles_validate_unlock(file.string().c_str(),unlock_id)) {
        fprintf(stderr,"[entitlement] unlock verification failed; trial result\n");
        return license_missing;
    }
    fprintf(stderr,"[entitlement] purchased C00 unlock verified\n");
    return 0;
}
void verify_handler(ppu_context* c) { c->gpr[3]=verify(c->gpr[3]); }
void purchase_handler(ppu_context* c) {
    fprintf(stderr,"[entitlement] purchase unavailable offline\n");
    c->gpr[3]=purchase_unavailable;
}
}
void tank_battles_register_entitlement_overrides() {
#if defined(__linux__)
    tank_battles_register_override(verify_nid,verify_handler);
    tank_battles_register_override(purchase_nid,purchase_handler);
#endif
}
int tank_battles_probe_entitlement() {
    const char* requested=getenv("TB_ENTITLEMENT_CONTENT_ID");
    if (!requested) requested=unlock_id;
    constexpr uint32_t ea=0x02000000;
    if (strlen(requested)>64) return 1;
    for (size_t i=0;i<=strlen(requested);++i) vm_write8(ea+i,requested[i]);
    ppu_context c{};c.thread_id=61;c.gpr[1]=0x03fff000;
    c.gpr[3]=getenv("TB_ENTITLEMENT_NULL") ? 0 : ea;
    ps3_hle_call(verify_nid,&c);
    const uint32_t result=c.gpr[3];
    ps3_hle_call(purchase_nid,&c);
    printf("[entitlement-probe] verify=0x%08X purchase=0x%08X\n",result,(uint32_t)c.gpr[3]);
    return 0;
}
