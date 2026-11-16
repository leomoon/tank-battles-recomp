/* Original fixture: real SDL virtual controller through the port's cellPad copy. */
#include <assert.h>
#include <sys/mman.h>
#include <unistd.h>
#include "cellPad_under_test.c"
uint8_t* vm_base;
uint32_t ppu_vm_size;
int g_resv_store_active;
uint32_t g_ww_lo, g_ww_hi;
void ppu_resv_break_store(uint64_t ea) {(void)ea;}
void ps3_ww_report_inline(uint32_t a,uint64_t v,int w) {(void)a;(void)v;(void)w;}
int spu_coh_is_reserved(uint32_t a) {(void)a;return 0;}
void spu_coh_notify_write(uint32_t a) {(void)a;}
void spu_lockline_lock(void) {}
void spu_lockline_unlock(void) {}
u32 cellGcm_flip_request_count(void) {return 0;}
void tb_input_sample(uint32_t k,uint32_t p,uint32_t b) {(void)k;(void)p;(void)b;}

#include <pthread.h>
#include <stdatomic.h>
static atomic_int stop_poll;
static void* poll_guest(void* unused) {
    (void)unused;
    while(!atomic_load(&stop_poll)){pad_poll_backend();usleep(1000);}
    return NULL;
}
int main(void) {
    setenv("PAD_NO_KEYBOARD","1",1);
    assert(SDL_Init(SDL_INIT_VIDEO|SDL_INIT_GAMECONTROLLER)==0);
    SDL_Window* window=SDL_CreateWindow("hotplug fixture",0,0,64,64,SDL_WINDOW_HIDDEN);assert(window);
    int first=SDL_JoystickAttachVirtual(SDL_JOYSTICK_TYPE_GAMECONTROLLER,6,21,1);assert(first>=0);
    int second=SDL_JoystickAttachVirtual(SDL_JOYSTICK_TYPE_GAMECONTROLLER,6,21,1);assert(second>=0);
    assert(cellPadInit(4)==CELL_OK);pthread_t guest;assert(!pthread_create(&guest,NULL,poll_guest,NULL));
    for(unsigned cycle=0;cycle<40;cycle++) {
        {
            TB_SDL_EVENT_GATE();
            assert(SDL_JoystickDetachVirtual(second)==0);
            assert(SDL_JoystickDetachVirtual(first)==0);
            SDL_Event event;while(SDL_PollEvent(&event)){}
        }
        usleep(30000);
        {
            TB_SDL_EVENT_GATE();
            first=SDL_JoystickAttachVirtual(SDL_JOYSTICK_TYPE_GAMECONTROLLER,6,21,1);assert(first>=0);
            second=SDL_JoystickAttachVirtual(SDL_JOYSTICK_TYPE_GAMECONTROLLER,6,21,1);assert(second>=0);
        }
        for(unsigned step=0;step<10;step++) {
            {TB_SDL_EVENT_GATE();SDL_Event event;while(SDL_PollEvent(&event)){} }
            usleep(10000);
        }
    }
    atomic_store(&stop_poll,1);pthread_join(guest,NULL);
    cellPadEnd();SDL_JoystickDetachVirtual(second);SDL_JoystickDetachVirtual(first);
    SDL_DestroyWindow(window);SDL_Quit();
    puts("PASS: 40 dual-controller detach/attach cycles with concurrent guest polling and serialized SDL event pumping");
}
