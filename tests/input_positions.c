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
static unsigned report(int offset) {
    usleep(20000);SDL_PumpEvents();
    assert(cellPadGetData(0,(CellPadData*)(uintptr_t)0x2000)==CELL_OK);
    unsigned a=0x2004+2*offset;return (vm_base[a]<<8)|vm_base[a+1];
}
int main(void) {
    setenv("PAD_NO_KEYBOARD","1",1);
    vm_base=mmap(NULL,0x10000,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    assert(vm_base!=MAP_FAILED);
    assert(SDL_Init(SDL_INIT_GAMECONTROLLER)==0);
    int idx=SDL_JoystickAttachVirtual(SDL_JOYSTICK_TYPE_GAMECONTROLLER,6,21,1);assert(idx>=0);
    SDL_Joystick* joy=SDL_JoystickOpen(idx);assert(joy);
    assert(cellPadInit(4)==CELL_OK);
    assert(SDL_GetHintBoolean("SDL_GAMECONTROLLER_USE_BUTTON_LABELS",SDL_TRUE)==SDL_FALSE);
    const int buttons[]={SDL_CONTROLLER_BUTTON_A,SDL_CONTROLLER_BUTTON_B,SDL_CONTROLLER_BUTTON_X,SDL_CONTROLLER_BUTTON_Y};
    const unsigned expected[]={0x40,0x20,0x80,0x10};
    for(int i=0;i<4;i++) {
        assert(SDL_JoystickSetVirtualButton(joy,buttons[i],1)==0);
        assert(report(CELL_PAD_BTN_OFFSET_DIGITAL2)==expected[i]);
        assert(SDL_JoystickSetVirtualButton(joy,buttons[i],0)==0);
        assert(report(CELL_PAD_BTN_OFFSET_DIGITAL2)==0);
    }
    SDL_JoystickSetVirtualAxis(joy,SDL_CONTROLLER_AXIS_LEFTX,32767);
    assert(report(CELL_PAD_BTN_OFFSET_ANALOG_LEFT_X)==255);
    SDL_JoystickSetVirtualButton(joy,SDL_CONTROLLER_BUTTON_A,1);
    assert(report(CELL_PAD_BTN_OFFSET_DIGITAL2)==0x40);
    SDL_JoystickClose(joy);assert(SDL_JoystickDetachVirtual(idx)==0);
    assert(report(CELL_PAD_BTN_OFFSET_DIGITAL2)==0);
    assert(report(CELL_PAD_BTN_OFFSET_ANALOG_LEFT_X)==128);
    idx=SDL_JoystickAttachVirtual(SDL_JOYSTICK_TYPE_GAMECONTROLLER,6,21,1);assert(idx>=0);
    joy=SDL_JoystickOpen(idx);assert(joy);
    SDL_JoystickSetVirtualButton(joy,SDL_CONTROLLER_BUTTON_B,1);
    assert(report(CELL_PAD_BTN_OFFSET_DIGITAL2)==0x20);
    assert(s_sdl_controllers[0]);
    cellPadEnd();SDL_JoystickClose(joy);SDL_JoystickDetachVirtual(idx);SDL_Quit();
    puts("PASS: positional faces, releases, axis, held disconnect, neutral state, reconnect on port 0");
}
