/*
 * ps3recomp game project -- entry point template.
 *
 * This is the runner. It boots a lifted title through the toolkit's PPU
 * scaffold: allocate the flat guest VM, load the PPU ELF named on the command
 * line, register the lifted function table and the HLE NID handlers, start a
 * frame clock, and dispatch the entry OPD.
 *
 * The scaffold does the work; nothing below reimplements it. ppu_load_elf,
 * ppu_recomp_register, ppu_hle_init, ppu_sysprx_register, ppu_fs_register,
 * lv2_init_syscalls and ppu_run all come from the toolkit, compiled into this
 * project from PS3RECOMP_DIR (see CMakeLists.txt). What is left here is the
 * part a port owns: which backend to present through, how the frame clock is
 * paced, and whatever diagnostics the title turns out to need.
 *
 * runtime/ppu/tests/boot_main.cpp in the toolkit is the same boot in its
 * fully-instrumented form -- crash filter, hang watchdog, guest-PC sampling
 * profiler. Read it when a boot goes wrong; copy from it what the title
 * needs. It is deliberately not what a fresh project starts with.
 *
 * The one part that IS duplicated from it is the frame clock below, because a
 * port is expected to change its pacing and a shared one would be the wrong
 * shape for that. It was copied faithfully, comments and all. If a pacing bug
 * is fixed in one of the two, look at the other.
 *
 * This builds on Windows, macOS and Linux. CI builds and runs it against the
 * boot smoke title on the last two, and compile-checks it against clang-cl on
 * the first (tools/check_ppu_scaffold.py).
 */

/* The lifter's generated header, which declares ppu_context and the function
 * table. It comes first: everything below is written against that struct. */
#include "ppu_recomp.h"
#include "boot_diagnostics.h"
#include "stubs.h"
#include "hle_inventory.h"
#include "content_paths.h"
#include "entitlement.h"
#include "offline_network.h"
#include "guest_layout.h"
#include "execution_policy_api.h"
#include "frame_budget.h"

/* The Win32 names -- Sleep, GetTickCount64, CreateThread, InterlockedIncrement,
 * VirtualAlloc, the scalar typedefs -- from one place. On Windows this is a
 * passthrough to <windows.h>; off Windows the toolkit's shim supplies the same
 * names over pthreads and mmap, so the call sites below are written once. */
#include "win32_compat.h"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <thread>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef _WIN32
#include <sys/mman.h>   /* mmap: the guest arena, see alloc_guest_vm */
#endif
#if defined(__linux__)
#include <sys/prctl.h>  /* walker/ticker names and timer slack */
#endif

#if defined(__APPLE__)
#include <CoreFoundation/CoreFoundation.h>   /* the main thread's run loop, see run_guest */
#include <pthread.h>
#endif

#ifdef _WIN32
/* timeBeginPeriod: <windows.h> arrives with WIN32_LEAN_AND_MEAN set, which
 * excludes the multimedia timer API, so it is asked for by name -- and after
 * windows.h, since timeapi.h uses UINT. No shim exists off Windows because
 * there is nothing to shim: the 15.6 ms default timer granularity this works
 * around is a Windows problem. */
#include <timeapi.h>
#endif

/* ---------------------------------------------------------------------------
 * What the scaffold gives this file
 * -----------------------------------------------------------------------*/
extern "C" {
uint32_t ppu_load_elf(const char* path);      /* ELF -> guest RAM, returns entry OPD */
void     ppu_recomp_register(void);           /* generated: lifted table -> address map */
void     ppu_hle_init(void);                  /* firmware import NID -> HLE handler */
void     ppu_sysprx_register(void);           /* boot-critical CRT (sys_initialize_tls, ...) */
void     ppu_fs_register(void);               /* cellFs over the real game directory */
void     lv2_init_syscalls(void);             /* the lv2 syscall table */
int      ppu_run(uint32_t entry_opd, uint32_t stack_top);

/* This project's own: load whatever real system PRX modules the title needs
 * into guest RAM and register their exports. stubs.cpp has the empty version
 * a game with no lifted PRX wants. */
void     ps3_load_prx_modules(void);

extern const char* ppu_vfs_root;              /* host dir the PS3 mount points map into */
void     ps3_host_quit(const char* why);      /* cellSysutil.c: REQUEST_EXITGAME, then exit 0 */

/* sys_ppu_thread.c: the main thread's stack, as sys_ppu_thread_get_stack_information
 * reports it. Left at 0 it names VM_STACK_BASE, the first created thread's stack. */
extern uint32_t g_ppu_main_stack_addr;
extern uint32_t g_ppu_main_stack_size;

/* The status the guest handed to sys_process_exit. A title that exits that way
 * never returns through ppu_run, so this is only read on the path where the
 * entry function unwound instead. */
extern int     g_sys_process_exit_called;
extern int32_t g_sys_process_exit_code;

/* Host-provided symbols the runtime and the HLE libraries link against. */
uint8_t* vm_base = nullptr;
extern uint32_t ppu_vm_size;                  /* ppu_loader.cpp: the OOB guard */
extern uint32_t ppu_hle_inject_base;          /* cellGcmSys.c: the GCM window      */

/* Guest-callback dispatch. g_ps3_guest_caller is the hook the HLE runtime
 * calls back into recompiled code through -- cellSysutil events and the GCM
 * vblank/flip handlers. ppu_guest_call does the OPD -> dispatch. */
typedef void (*ps3_guest_caller_fn)(uint32_t, uint64_t, uint64_t, uint64_t, uint64_t,
                                    uint64_t, uint64_t, uint64_t, uint64_t);
extern ps3_guest_caller_fn g_ps3_guest_caller;
uint64_t ppu_guest_call(uint32_t, uint64_t, uint64_t, uint64_t, uint64_t,
                        uint64_t, uint64_t, uint64_t, uint64_t);

/* The GCM half of the frame clock. */
void     cellGcmTickVBlank(void);
void     cellGcmTickFlip(void);
int      cellGcm_take_flip_pending(void);
void*    cellGcm_fifo_kick_event(void);     /* signalled by guest fence polls */
void     cellGcm_rsx_process_fifo(void);      /* drain get -> put */
unsigned cellGcm_flip_request_count(void);
}

/* ---------------------------------------------------------------------------
 * RSX present backend
 * -----------------------------------------------------------------------*/
/* D3D12 on Windows, Metal on Apple, and the null backend's headless software
 * path anywhere else -- the same selection runtime/host/host_posix.c and the
 * toolkit's boot harness make. All three expose the same three entry points,
 * so the frame clock below is backend-agnostic. */
#if defined(_WIN32)
extern "C" int  rsx_d3d12_backend_init(uint32_t w, uint32_t h, const char* title);
extern "C" void rsx_d3d12_backend_present(void);
extern "C" int  rsx_d3d12_backend_pump_messages(void);
#  define rsx_backend_init    rsx_d3d12_backend_init
#  define rsx_backend_present rsx_d3d12_backend_present
#  define rsx_backend_pump    rsx_d3d12_backend_pump_messages
#  define RSX_BACKEND_NAME    "D3D12"
#elif defined(__APPLE__)
extern "C" int  rsx_metal_backend_init(uint32_t w, uint32_t h, const char* title);
extern "C" void rsx_metal_backend_present(void);
extern "C" int  rsx_metal_backend_pump_messages(void);
#  define rsx_backend_init    rsx_metal_backend_init
#  define rsx_backend_present rsx_metal_backend_present
#  define rsx_backend_pump    rsx_metal_backend_pump_messages
#  define RSX_BACKEND_NAME    "Metal"
#elif defined(PS3RECOMP_RSX_VULKAN)
extern "C" int  rsx_vulkan_backend_init(uint32_t w, uint32_t h, const char* title);
extern "C" void rsx_vulkan_backend_present(void);
extern "C" int  rsx_vulkan_backend_pump_messages(void);
#  define rsx_backend_init    rsx_vulkan_backend_init
#  define rsx_backend_present rsx_vulkan_backend_present
#  define rsx_backend_pump    rsx_vulkan_backend_pump_messages
#  define RSX_BACKEND_NAME    "Vulkan"
#else
extern "C" int  rsx_null_backend_init(uint32_t w, uint32_t h, const char* title);
extern "C" void rsx_null_backend_present(void);
extern "C" int  rsx_null_backend_pump_messages(void);
#  define rsx_backend_init    rsx_null_backend_init
#  define rsx_backend_present rsx_null_backend_present
#  define rsx_backend_pump    rsx_null_backend_pump_messages
#  define RSX_BACKEND_NAME    "null (headless software)"
#endif

/* ---------------------------------------------------------------------------
 * Configuration
 * -----------------------------------------------------------------------*/
/* The flat VM must span every region the PS3 memory map uses, not just the
 * game image: sys_ppu_thread_create puts thread stacks at 0xD0000000, so a
 * smaller arena makes every spawned thread's stack access out of bounds. */
#define VM_SIZE       0x100010000ull   /* the full 32-bit guest space + a 64K guard */
#define STACK_TOP     TB_MAIN_STACK_TOP
#define STACK_SIZE    TB_MAIN_STACK_SIZE
#define WINDOW_WIDTH  1280
#define WINDOW_HEIGHT 720

/* ---------------------------------------------------------------------------
 * Frame clock
 * -----------------------------------------------------------------------*/
/* On real hardware the RSX raises a vblank interrupt about 60 times a second
 * and that is what drives the game's frame loop. With no RSX it is synthesized
 * here: a host thread calls cellGcmTickVBlank/TickFlip, which invoke the
 * handlers the guest registered, and drains the GCM FIFO. Without this the
 * title initialises, registers its handlers and then waits forever.
 *
 * The ticks are driven off real elapsed time rather than off how long present()
 * took. A hidden or occluded window makes present block hard, and pacing the
 * ticks behind it paces the whole game behind it. */
static volatile LONG g_frames_presented = 0;

/* Frames handed to the backend at a guest FLIP boundary -- one per frame the
 * guest actually finished. The presents made before the guest's first flip, so
 * a fresh window is not left blank through a long boot, carry no guest frame
 * and are deliberately not counted. */
extern "C" unsigned ppu_boot_frames_presented(void)
{
    return (unsigned)g_frames_presented;
}

/* One FIFO drain, recorded for the frame-budget timeline with the put it saw. */
static void drain_fifo(void)
{
    TB_RENDER_SCOPE(10);
    if (!tb_frame_budget_on()) { cellGcm_rsx_process_fifo(); return; }
    const uint64_t t0 = tb_frame_budget_now();
    const uint32_t put = vm_base ? __builtin_bswap32(*(const uint32_t*)(vm_base + ppu_hle_inject_base + 0x2000u)) : 0;
    cellGcm_rsx_process_fifo();
    tb_frame_budget_span(TB_FT_DRAIN_CALL, put, t0, tb_frame_budget_now());
}

/* Presentation is split in two (rsx_vulkan_backend.h). The walker records
 * and submits the copy of the flipped surface -- it reads the frame, so it
 * must happen in order before anything later is drained -- and a present
 * thread waits for that copy, blits it into the swapchain and presents it.
 * The walker therefore never waits for the GPU or the display to show a
 * frame. A present arriving before the last one finished completes the last
 * one first, so at most one copy and one window present are in flight.
 * TB_PRESENT_THREAD=0 keeps the whole present on the walker for A/B runs. */
#if defined(PS3RECOMP_RSX_VULKAN)
extern "C" void rsx_vulkan_backend_present_begin(void);
extern "C" void rsx_vulkan_backend_present_finish(void);
/* Process lifetime on purpose: exit() must not destroy a condition variable
 * the present thread is still waiting on (glibc blocks in that destructor). */
struct PresentQueue { std::mutex mu; std::condition_variable cv; unsigned requested = 0, finished = 0; };
static PresentQueue& present_queue(void) { static PresentQueue* q = new PresentQueue; return *q; }

static bool present_thread_on(void)
{
    static const bool on = [] { const char* e = getenv("TB_PRESENT_THREAD"); return !e || strcmp(e, "0") != 0; }();
    return on;
}

static DWORD WINAPI present_worker(LPVOID)
{
#if defined(__linux__)
    prctl(PR_SET_NAME, "tb-present", 0, 0, 0);
#endif
    for (;;) {
        PresentQueue& q = present_queue();
        {
            std::unique_lock<std::mutex> lock(q.mu);
            q.cv.wait(lock, [&q] { return q.requested != q.finished; });
            q.finished = q.requested;   /* later requests complete earlier ones */
        }
        const uint64_t t0 = tb_frame_budget_on() ? tb_frame_budget_now() : 0;
        rsx_vulkan_backend_present_finish();
        if (t0) tb_frame_budget_span(TB_FT_PRESENT_FINISH, 0, t0, tb_frame_budget_now());
    }
    return 0;
}
#endif

static void present_frame_now(void)
{
#if defined(PS3RECOMP_RSX_VULKAN)
    if (present_thread_on()) {
        rsx_vulkan_backend_present_begin();
        PresentQueue& q = present_queue();
        { std::lock_guard<std::mutex> lock(q.mu); q.requested++; }
        q.cv.notify_one();
        return;
    }
#endif
    rsx_backend_present();
}

static void present_guest_frame(void)
{
    TB_RENDER_SCOPE(11);
    const uint64_t t0 = tb_frame_budget_on() ? tb_frame_budget_now() : 0;
    present_frame_now();
    if (t0) tb_frame_budget_span(10 /* PS3_FT_PRESENT */, 0, t0, tb_frame_budget_now());
    /* This thread increments and guest threads read, so interlocked rather
     * than a volatile ++, which on arm64 is neither atomic nor a fence. */
    if (InterlockedIncrement(&g_frames_presented) == 1)
        tank_battles_milestone("first guest frame presented");
}

/* The guest's FIFO control registers (big-endian in the GCM window). */
static uint32_t gcm_control_word(uint32_t offset)
{
    if (!vm_base) return 0;
    uint32_t v;
    memcpy(&v, vm_base + ppu_hle_inject_base + 0x2000u + offset, sizeof v);
    return __builtin_bswap32(v);
}

/* 0 until the walker has tried to bring the backend up, then 1 (up) or -1. */
static std::atomic<int> g_rsx_state{0};

/* Sleep for about `us` microseconds, recorded on the frame-budget timeline. */
static void walker_sleep_us(unsigned us)
{
    const uint64_t t0 = tb_frame_budget_on() ? tb_frame_budget_now() : 0;
    std::this_thread::sleep_for(std::chrono::microseconds(us));
    if (t0) tb_frame_budget_span(TB_FT_CLOCK_SLEEP, us, t0, tb_frame_budget_now());
}

/* The vblank beat: cellGcmTickVBlank/TickFlip on a steady clock, on a thread
 * of its own so a long drain or a blocking present cannot delay it. Only
 * pending-work flags change here; guest handlers still run on guest threads
 * (ppu_gcm_pump). The period stays the 16 ms the frame clock always used. */
static DWORD WINAPI vblank_ticker(LPVOID)
{
#if defined(__linux__)
    prctl(PR_SET_TIMERSLACK, 1UL, 0, 0, 0);
    prctl(PR_SET_NAME, "tb-vblank", 0, 0, 0);
#endif
    while (g_rsx_state.load(std::memory_order_acquire) == 0) Sleep(1);
    using clock = std::chrono::steady_clock;
    const auto period = std::chrono::milliseconds(16);
    auto next_tick = clock::now();
    for (;;) {
        std::this_thread::sleep_until(next_tick);
        int fired = 0;
        while (clock::now() >= next_tick && fired < 240) {
            cellGcmTickVBlank();
            cellGcmTickFlip();
            if (tb_frame_budget_on()) {
                const uint64_t t = tb_frame_budget_now();
                tb_frame_budget_span(TB_FT_VBLANK, 0, t, t);
            }
            next_tick += period;
            fired++;
        }
        if (fired >= 240) next_tick = clock::now() + period;   /* fell too far behind -- resync */
    }
    return 0;
}

/* The RSX walker: owns the backend and the window, drains the guest FIFO as
 * soon as `put` moves and presents each guest flip once.
 *
 * It used to share one loop with the vblank beat and sleep 4 ms between
 * passes, so every label or fence the guest waited on paid up to a 4 ms poll
 * on top of the work, and frames that missed a beat were paced to two.
 * Idle, it polls `put` and the toolkit's fence kick at sub-millisecond
 * intervals instead. A drain that stops at a flip leaves the frame to present
 * before anything after it is drained. */
static DWORD WINAPI rsx_walker(LPVOID)
{
#if defined(__linux__)
    prctl(PR_SET_TIMERSLACK, 1UL, 0, 0, 0);
    prctl(PR_SET_NAME, "tb-rsx-walker", 0, 0, 0);
#endif
    const char* title = getenv("PS3_TITLE");
    if (!title || !*title) title = "Tank Battles Recomp";

    int rsx_ok = (rsx_backend_init(WINDOW_WIDTH, WINDOW_HEIGHT, title) == 0);
    fprintf(stderr, "[rsx] %s backend init %s\n", RSX_BACKEND_NAME,
            rsx_ok ? "OK" : "FAILED");

#if defined(PS3RECOMP_RSX_VULKAN)
    if (!rsx_ok) { tank_battles_report("Vulkan initialization failed"); _exit(87); }
#endif
    g_rsx_state.store(rsx_ok ? 1 : -1, std::memory_order_release);
    if (!rsx_ok) return 0;
#if defined(PS3RECOMP_RSX_VULKAN)
    if (present_thread_on()) CreateThread(NULL, 1u * 1024 * 1024, present_worker, NULL, 0, NULL);
#endif

    using clock = std::chrono::steady_clock;
    HANDLE kick = (HANDLE)cellGcm_fifo_kick_event();
    auto last_pump = clock::now(), last_boot_present = last_pump;
    unsigned idle = 0;

    for (;;) {
        const auto now = clock::now();
        /* Events at least every 2 ms, however busy the FIFO keeps the walker. */
        if (now - last_pump >= std::chrono::milliseconds(2)) {
            last_pump = now;
            if (rsx_backend_pump() != 0) {
                /* Quit, as the console's Quit Game does, rather than leave
                 * the game running with no window. */
                ps3_host_quit("window closed");
                return 0;
            }
        }
        /* A flip the last drain stopped at: show exactly that frame before
         * draining any of the next one. */
        if (cellGcm_take_flip_pending()) {
            present_guest_frame();
            idle = 0;
            continue;
        }
        /* Before the first flip present on a 16 ms cadence, so a fresh
         * window is not left blank through a long boot. */
        if (cellGcm_flip_request_count() == 0 &&
            now - last_boot_present >= std::chrono::milliseconds(16)) {
            last_boot_present = now;
            present_frame_now();
        }

        const uint32_t put = gcm_control_word(0), get = gcm_control_word(4);
        const int kicked = kick && WaitForSingleObject(kick, 0) == WAIT_OBJECT_0;
        if (put != get || kicked || idle == 0) {
            drain_fifo();
            /* Progress, or a flip to present: go round at once. A drain
             * that moved nothing (an ACQUIRE still unreleased) backs off
             * like an idle walker so it does not spin a core. */
            if (gcm_control_word(4) != get || gcm_control_word(0) != put) { idle = 1; continue; }
        }
        /* Idle: yield briefly, then poll at 100 us, and at 250 us after 2 ms. */
        idle++;
        if (idle < 16)       std::this_thread::yield();
        else if (idle < 36)  walker_sleep_us(100);
        else                 walker_sleep_us(250);
    }
    return 0;
}

/* ---------------------------------------------------------------------------
 * VFS root
 * -----------------------------------------------------------------------*/
/* Derive the directory holding PS3_GAME from the EBOOT path
 * <root>/PS3_GAME/USRDIR/EBOOT.elf -> <root>. $PS3_VFS_ROOT overrides. */
static char s_vfs_root[1024];

static void derive_vfs_root(const char* eboot)
{
    const char* env = getenv("PS3_VFS_ROOT");
    if (env && *env) { ppu_vfs_root = env; return; }

    strncpy(s_vfs_root, eboot, sizeof s_vfs_root - 1);
    for (char* p = s_vfs_root; *p; p++) if (*p == '\\') *p = '/';
    /* strip EBOOT.elf, USRDIR and PS3_GAME */
    for (int i = 0; i < 3; i++) { char* s = strrchr(s_vfs_root, '/'); if (s) *s = 0; }
    if (!s_vfs_root[0]) strcpy(s_vfs_root, ".");
    ppu_vfs_root = s_vfs_root;
}

/* ---------------------------------------------------------------------------
 * Guest VM
 * -----------------------------------------------------------------------*/
#ifdef _WIN32
/* Demand-paging for the flat VM: reserve the whole 4 GB guest space, which
 * costs no commit, and commit each 64 KB page on first access. Every 32-bit
 * guest offset is then valid, so a garbage guest pointer reads zero instead of
 * killing the process. Faults outside the arena fall through untouched. */
static LONG WINAPI vm_commit_veh(EXCEPTION_POINTERS* ep)
{
    if (ep->ExceptionRecord->ExceptionCode == EXCEPTION_ACCESS_VIOLATION) {
        ULONG_PTR fault = ep->ExceptionRecord->ExceptionInformation[1];
        uintptr_t base  = (uintptr_t)vm_base;
        if (vm_base && fault >= base && fault < base + VM_SIZE) {
            void* page = (void*)(fault & ~(uintptr_t)0xFFFF);
            if (VirtualAlloc(page, 0x10000, MEM_COMMIT, PAGE_READWRITE))
                return EXCEPTION_CONTINUE_EXECUTION;
        }
    }
    return EXCEPTION_CONTINUE_SEARCH;
}
#endif

static bool alloc_guest_vm(void)
{
#ifdef _WIN32
    AddVectoredExceptionHandler(1, vm_commit_veh);
    vm_base = (uint8_t*)VirtualAlloc(NULL, VM_SIZE, MEM_RESERVE, PAGE_READWRITE);
    ppu_vm_size = 0;              /* the whole space is backed; no OOB guard needed */
#else
    /* No vectored exception handlers off Windows, so the arena is mapped up
     * front and lazily backed by the OS: only pages the title touches cost
     * anything. It spans the whole 32-bit space plus the guard, as on Windows.
     * It used to stop below 0xE0000000, but the raw SPU windows (local store
     * and problem-state registers, runtime/spu/spu_raw.c) live from there up.
     * Every load and store a title made into one was reported as an
     * out-of-bounds access and dropped, so a raw SPU was never loaded or
     * started, and sys_raw_spu_create, which clears the registers through
     * vm_base directly, wrote past the end of the mapping. With the whole
     * space backed, ppu_vm_size 0 also gives the loader's bounds check the
     * one-compare fast path Windows already has.
     *
     * mmap, not calloc: vm_commit() hands vm_base + addr to mprotect, which
     * takes page-aligned addresses only. glibc serves an allocation this big
     * from its own mmap chunk and returns a pointer 16 bytes past the page
     * start, so every commit failed with EINVAL and sys_memory_allocate
     * answered ENOMEM to every call. mmap's base is page-aligned and zeroed. */
    void* p = mmap(NULL, VM_SIZE, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    vm_base = (p == MAP_FAILED) ? nullptr : (uint8_t*)p;
    ppu_vm_size = 0;              /* the whole space is backed; no OOB guard needed */
#endif
    return vm_base != nullptr;
}

/* ---------------------------------------------------------------------------
 * Main
 * -----------------------------------------------------------------------*/
static void harness_guest_caller(uint32_t opd, uint64_t a0, uint64_t a1,
                                 uint64_t a2, uint64_t a3, uint64_t a4,
                                 uint64_t a5, uint64_t a6, uint64_t a7)
{
    ppu_guest_call(opd, a0, a1, a2, a3, a4, a5, a6, a7);
}

/* ---------------------------------------------------------------------------
 * Running the guest
 * -----------------------------------------------------------------------*/
#if defined(__APPLE__)
/* AppKit only runs on the process's main thread. The Metal backend creates
 * its window and pumps its events with dispatch_sync onto the main queue
 * when it is called from any other thread, as it is from the frame clock.
 * With the guest on the main thread nothing ever serviced that queue: the
 * frame clock blocked in its first rsx_backend_init, no vblank or flip was
 * ever delivered and the FIFO never drained. A title that waits for a flip
 * hangs there. So the guest runs on a thread of its own, with the same
 * 256 MB host stack every guest thread gets (sys_ppu_thread.c), and the
 * main thread turns the run loop until ppu_run returns. Headless runs never
 * touch the main queue, which is why CI does not see the difference. */
struct guest_run { uint32_t entry, stack_top; int rc; volatile int done; };

static void* guest_thread(void* arg)
{
    guest_run* g = (guest_run*)arg;
    g->rc = ppu_run(g->entry, g->stack_top);
    __atomic_store_n(&g->done, 1, __ATOMIC_RELEASE);
    CFRunLoopStop(CFRunLoopGetMain());
    return nullptr;
}

static int run_guest(uint32_t entry, uint32_t stack_top)
{
    static guest_run g;
    g.entry = entry; g.stack_top = stack_top; g.rc = 0; g.done = 0;
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setstacksize(&attr, 256u * 1024u * 1024u);
    pthread_t th;
    int err = pthread_create(&th, &attr, guest_thread, &g);
    pthread_attr_destroy(&attr);
    if (err != 0) {
        fprintf(stderr, "[boot] could not start the guest thread (%d)\n", err);
        return -1;
    }
    /* Short slices: CFRunLoopStop only ends a run that is already going. */
    while (!__atomic_load_n(&g.done, __ATOMIC_ACQUIRE))
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.05, false);
    pthread_join(th, nullptr);
    return g.rc;
}
#else
static int run_guest(uint32_t entry, uint32_t stack_top) { return ppu_run(entry, stack_top); }
#endif

int main(int argc, char** argv)
{
    if (argc < 2) {
        printf("usage: %s <PPU ELF>\n", argv[0]);
        return 2;
    }

#ifdef _WIN32
#pragma comment(lib, "winmm.lib")
    /* 1 ms timer resolution. The default granularity is about 15.6 ms, which
     * inflates every shorter wait the title makes and throttles the whole
     * thing. POSIX timers are already fine-grained. */
    timeBeginPeriod(1);
    setvbuf(stdout, NULL, _IONBF, 0);   /* unbuffered: do not lose prints on a kill */
#endif

    setvbuf(stdout, nullptr, _IONBF, 0);
    tb_frame_budget_init();
    tank_battles_diagnostics_init();
    tank_battles_milestone("runner initialized");
    printf("=== Tank Battles Recomp (experimental) ===\n");

    if (!alloc_guest_vm()) {
        tank_battles_report("guest VM allocation failed");
        fprintf(stderr, "ERROR: could not allocate the guest address space\n");
        return 1;
    }

    uint32_t entry = ppu_load_elf(argv[1]);
    if (!entry) {
        tank_battles_report("ELF load failed");
        fprintf(stderr, "ERROR: could not load %s\n", argv[1]);
        return 1;
    }

    tank_battles_milestone("ELF loaded");
    derive_vfs_root(argv[1]);
    if (!tank_battles_init_content(argv[1])) {
        tank_battles_report("content initialization failed");
        return 1;
    }
    printf("[boot] VFS root: %s\n", ppu_vfs_root);

    tank_battles_init_offline_network();
    ppu_recomp_register();   /* lifted function table -> address map */
    tank_battles_milestone("function table registered");
    ps3_load_prx_modules();  /* this game's lifted system PRX, if it has any */
    ppu_hle_init();          /* firmware import NID -> HLE handlers */
    tank_battles_milestone("HLE initialized");
    ppu_sysprx_register();   /* boot-critical CRT */
    ppu_fs_register();       /* cellFs over the game directory */
    tank_battles_milestone("filesystem registered");
    lv2_init_syscalls();     /* the lv2 syscall table */

    tank_battles_register_overrides();
    tank_battles_milestone("port overrides registered");
    if (!tank_battles_write_hle_inventory()) return 1;
    if (getenv("TB_EXECUTION_PROBE")) return tank_battles_execution_probe();
    if (getenv("TB_OFFLINE_PROBE")) return tank_battles_probe_offline_network();
    if (getenv("TB_ENTITLEMENT_PROBE")) return tank_battles_probe_entitlement();
    if (getenv("TB_ASSET_PROBE")) return tank_battles_probe_assets();
    if (getenv("TB_HLE_AUDIT_ONLY")) {
        fprintf(stderr, "[audit] registration-only startup; guest not dispatched\n");
        return 0;
    }
#ifdef TB_DIAGNOSTIC_TESTS
    if (!tank_battles_verify_test_override()) return 1;
    if (getenv("TB_DIAGNOSTIC_STALL")) {
        tank_battles_milestone("diagnostic test stall");
        for (;;) Sleep(100);
    }
#endif

#if defined(PS3RECOMP_RSX_VULKAN)
    // Normal Vulkan launches request a real window and translated guest programs.
    if (!getenv("PS3RECOMP_VK_WINDOW")) setenv("PS3RECOMP_VK_WINDOW", "1", 0);
    if (!getenv("PS3RECOMP_VK_GUEST_PROGRAMS")) setenv("PS3RECOMP_VK_GUEST_PROGRAMS", "1", 0);
#endif

    /* Install the guest-callback hook, then start the frame clock. It no-ops
     * until the title registers its vblank and flip handlers during init. */
    g_ps3_guest_caller = harness_guest_caller;
    CreateThread(NULL, 4u * 1024 * 1024, rsx_walker, NULL, 0, NULL);
    CreateThread(NULL, 1u * 1024 * 1024, vblank_ticker, NULL, 0, NULL);

    printf("\n[boot] dispatching entry OPD 0x%08X (stack top 0x%08X)\n\n",
           entry, STACK_TOP);

    /* A title that reads its own stack bounds (to find data its parent left
     * below the top, say) gets the stack it really runs on. */
    g_ppu_main_stack_addr = STACK_TOP - STACK_SIZE;
    g_ppu_main_stack_size = STACK_SIZE;

    tank_battles_milestone("first guest dispatch");
    int rc = run_guest(entry, STACK_TOP);
    if (rc != 0) tank_battles_report("guest entry returned failure");
    printf("\n[boot] ppu_run returned %d (entry function unwound)\n", rc);

    /* A title that called sys_process_exit never reaches this line: that path
     * ends in the host exit(). Getting here means the entry function returned
     * instead, so hand back the status the guest published if it published
     * one. Returning a hardcoded 0 would report success for a run that never
     * got anywhere. */
    return g_sys_process_exit_called ? (int)g_sys_process_exit_code : 0;
}
