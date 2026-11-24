/* Real SDL window lifecycle using the production window-preference helpers. */
#ifdef NDEBUG
#undef NDEBUG
#endif
#define SDL_MAIN_HANDLED
#include <SDL.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include "sdk_overrides/player_settings_hooks.inc"
void tb_settings_init(void);
int tb_settings_render_scale(void);
static SDL_Renderer* renderer;
static void pump(SDL_Window* window, unsigned duration) {
    const Uint64 until=SDL_GetTicks64()+duration;
    do {
        SDL_Event event;
        while (SDL_PollEvent(&event))
            if (event.type==SDL_WINDOWEVENT &&
                (event.window.event==SDL_WINDOWEVENT_SIZE_CHANGED || event.window.event==SDL_WINDOWEVENT_RESTORED ||
                 event.window.event==SDL_WINDOWEVENT_MAXIMIZED || event.window.event==SDL_WINDOWEVENT_DISPLAY_CHANGED)) tb_window_changed();
        tb_window_save_size(window);
        SDL_SetRenderDrawColor(renderer,18,29,43,255);
        SDL_RenderClear(renderer);SDL_RenderPresent(renderer);
        SDL_Delay(10);
    } while (SDL_GetTicks64()<until);
}
int main(void) {
    SDL_SetMainReady();tb_settings_init();
    assert(SDL_Init(SDL_INIT_VIDEO)==0);
    int width=1280,height=720;tb_window_initial_size(&width,&height);
    const char* full=getenv("PS3RECOMP_FULLSCREEN");
    if (!full) full=getenv("PS3RECOMP_VK_FULLSCREEN");
    const int initially_full=full && atoi(full)!=0;
    SDL_Window* window=SDL_CreateWindow("Tank Battles display preferences test",SDL_WINDOWPOS_CENTERED,SDL_WINDOWPOS_CENTERED,
        width,height,SDL_WINDOW_RESIZABLE|SDL_WINDOW_ALLOW_HIGHDPI|(initially_full?SDL_WINDOW_FULLSCREEN_DESKTOP:0));
    assert(window);
    renderer=SDL_CreateRenderer(window,-1,SDL_RENDERER_SOFTWARE);assert(renderer);
    SDL_RenderClear(renderer);SDL_RenderPresent(renderer);
    // Desktop automation can isolate this test window before fullscreen changes.
    const char* gate=getenv("TB_WINDOW_TEST_GATE");
    if (gate) {
        const Uint64 deadline=SDL_GetTicks64()+15000;
        FILE* ready=NULL;
        while (!(ready=fopen(gate,"r")) && SDL_GetTicks64()<deadline) { SDL_PumpEvents();SDL_Delay(10); }
        assert(ready);fclose(ready);
    }
    pump(window,700);
    if (initially_full) {tb_window_toggle_fullscreen(window);pump(window,700);}
    int max_w,max_h;SDL_GetWindowMaximumSize(window,&max_w,&max_h);assert(!max_w && !max_h);
    SDL_SetWindowSize(window,900,600);pump(window,700);
    SDL_GetWindowSize(window,&width,&height);assert(width>=320 && height>=180);
    tb_settings_note_window(width,height);
    const int scale=tb_settings_render_scale();
    for(int iteration=0;iteration<2;++iteration) {
        tb_window_toggle_fullscreen(window);pump(window,700);
        assert(SDL_GetWindowFlags(window)&SDL_WINDOW_FULLSCREEN_DESKTOP);
        int saved_w,saved_h;assert(tb_settings_window_size(&saved_w,&saved_h));assert(saved_w==width && saved_h==height);
        tb_window_toggle_fullscreen(window);pump(window,700);
        assert(!(SDL_GetWindowFlags(window)&SDL_WINDOW_FULLSCREEN_DESKTOP));
        int actual_w,actual_h;SDL_GetWindowSize(window,&actual_w,&actual_h);
        assert(tb_settings_window_size(&saved_w,&saved_h));
        if (SDL_GetWindowFlags(window)&SDL_WINDOW_MAXIMIZED) assert(saved_w==width && saved_h==height);
        else assert(saved_w==actual_w && saved_h==actual_h);
        width=saved_w;height=saved_h;
    }
    SDL_MinimizeWindow(window);pump(window,300);
    SDL_RestoreWindow(window);pump(window,700);
    assert(tb_settings_render_scale()==scale);
    printf("PASS: driver=%s startup_fullscreen=%d window=%dx%d render_scale=%d; resize, two borderless round trips and minimize/restore\n",
        SDL_GetCurrentVideoDriver(),initially_full,width,height,scale);
    SDL_DestroyRenderer(renderer);SDL_DestroyWindow(window);SDL_Quit();return 0;
}
