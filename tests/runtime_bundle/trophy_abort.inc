/* Real trophy module with original synthetic guest memory. No trophy files
 * are created: the synthetic context is never registered. */
#include <assert.h>
uint8_t* vm_base;
uint32_t ppu_vm_size;
int g_resv_store_active;
uint32_t g_ww_lo, g_ww_hi;
void ppu_resv_break_store(uint64_t ea) { (void)ea; }
void ps3_ww_report_inline(uint32_t a, uint64_t v, int w) { (void)a; (void)v; (void)w; }
int spu_coh_is_reserved(uint32_t a) { (void)a; return 0; }
void spu_coh_notify_write(uint32_t a) { (void)a; }
void spu_lockline_lock(void) {}
void spu_lockline_unlock(void) {}
ps3_guest_caller_fn g_ps3_guest_caller;
static unsigned queued;
static unsigned installed;
static void abort_registration(u32 opd, u64 context, u64 status, u64 completed,
                               u64 total, u64 arg, u64 a, u64 b, u64 c)
{
    (void)completed; (void)total; (void)arg; (void)a; (void)b; (void)c;
    assert(opd==0x300 && context==1 && status==SCE_NP_TROPHY_STATUS_INSTALLED);
    ++installed;
    assert(tb_trophy_abort_handle(1)==CELL_OK);
}
s32 cellSysutilQueueGuestCallbackArgs(u32 opd, const u64* args)
{
    (void)opd; (void)args; ++queued; return CELL_OK;
}
int main(void)
{
    vm_base=calloc(1,65536); ppu_vm_size=65536;
    assert(vm_base);
    assert(tb_trophy_abort_handle(1)==(s32)SCE_NP_TROPHY_ERROR_NOT_INITIALIZED);
    assert(sceNpTrophyInit(NULL,0,0,0)==CELL_OK);
    assert(tb_trophy_abort_handle(0)==(s32)SCE_NP_TROPHY_ERROR_INVALID_HANDLE);
    assert(tb_trophy_abort_handle(1)==(s32)SCE_NP_TROPHY_ERROR_INVALID_HANDLE);
    assert(tb_trophy_abort_handle(SCE_NP_TROPHY_MAX_HANDLES+1)==(s32)SCE_NP_TROPHY_ERROR_INVALID_HANDLE);
    memcpy(vm_base+0x100,"NPWRTEST",9);
    assert(sceNpTrophyCreateContext((void*)0x200,(void*)0x100,NULL,0)==CELL_OK);
    assert(sceNpTrophyCreateHandle((void*)0x204)==CELL_OK && vm_read32(0x204)==1);
    assert(tb_trophy_abort_handle(1)==CELL_OK);
    assert(tb_trophy_abort_handle(1)==CELL_OK);
    assert(sceNpTrophyRegisterContext(1,1,0x300,0,0)==(s32)SCE_NP_TROPHY_ERROR_ABORT);
    assert(sceNpTrophyGetRequiredDiskSpace(1,1,NULL,0)==(s32)SCE_NP_TROPHY_ERROR_ABORT);
    assert(sceNpTrophyGetGameInfo(1,1,NULL,NULL)==(s32)SCE_NP_TROPHY_ERROR_ABORT);
    assert(sceNpTrophyGetTrophyInfo(1,1,0,NULL,NULL)==(s32)SCE_NP_TROPHY_ERROR_ABORT);
    assert(sceNpTrophyUnlockTrophy(1,1,0,NULL)==(s32)SCE_NP_TROPHY_ERROR_ABORT);
    assert(sceNpTrophyGetTrophyUnlockState(1,1,NULL,NULL)==(s32)SCE_NP_TROPHY_ERROR_ABORT);
    assert(sceNpTrophyGetGameProgress(1,1,NULL)==(s32)SCE_NP_TROPHY_ERROR_ABORT);
    assert(!queued);
    assert(sceNpTrophyDestroyHandle(1)==CELL_OK);
    assert(tb_trophy_abort_handle(1)==(s32)SCE_NP_TROPHY_ERROR_INVALID_HANDLE);
    assert(sceNpTrophyCreateHandle((void*)0x204)==CELL_OK && vm_read32(0x204)==1);
    assert(sceNpTrophyGetRequiredDiskSpace(0,1,NULL,0)==(s32)SCE_NP_TROPHY_ERROR_INVALID_CONTEXT);
    assert(sceNpTrophyGetRequiredDiskSpace(1,1,NULL,0)==(s32)SCE_NP_TROPHY_ERROR_INVALID_ARGUMENT);
    g_ps3_guest_caller=abort_registration;
    assert(sceNpTrophyRegisterContext(1,1,0x300,0,0)==(s32)SCE_NP_TROPHY_ERROR_ABORT);
    assert(installed==1 && !queued);
    assert(sceNpTrophyDestroyContext(1)==CELL_OK);
    assert(sceNpTrophyDestroyHandle(1)==CELL_OK);
    assert(sceNpTrophyTerm()==CELL_OK);
    assert(tb_trophy_abort_handle(1)==(s32)SCE_NP_TROPHY_ERROR_NOT_INITIALIZED);
    free(vm_base);
    puts("PASS: trophy abort validates handles, rejects later work and resets on handle recreation");
    return 0;
}
