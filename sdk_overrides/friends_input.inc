/* Runs only on the SDL window owner, inside the existing SDL event gate. */
static void tb_friends_sdl_sync_text(void) {
    static int active=0;
    const int wanted=tb_friends_text_requested();
    if (wanted!=active) {
        if (wanted) SDL_StartTextInput(); else SDL_StopTextInput();
        active=wanted;
    }
}
static int tb_friends_sdl_event(const SDL_Event* event) {
    if (event->type==SDL_WINDOWEVENT) {
        if (event->window.event==SDL_WINDOWEVENT_FOCUS_LOST) tb_friends_focus(0);
        if (event->window.event==SDL_WINDOWEVENT_FOCUS_GAINED) tb_friends_focus(1);
        tb_friends_sdl_sync_text();
    }
    if (event->type==SDL_TEXTINPUT && tb_friends_text_requested()) {
        tb_friends_text(event->text.text);return 1;
    }
    if (event->type==SDL_KEYDOWN && tb_friends_text_requested() &&
        event->key.keysym.sym==SDLK_v && (event->key.keysym.mod&KMOD_CTRL)) {
        char* text=SDL_GetClipboardText();
        if (text) {tb_friends_text(text);SDL_free(text);}return 1;
    }
    if (event->type==SDL_KEYDOWN || event->type==SDL_KEYUP)
        return tb_friends_key((uint32_t)event->key.keysym.scancode,event->type==SDL_KEYDOWN,
                              event->key.repeat,(uint32_t)event->key.keysym.mod);
    return 0;
}
static void tb_friends_sdl_finish(void) {
    int count=0;
    const Uint8* keys=SDL_GetKeyboardState(&count);
    tb_friends_update_keyboard(keys,count);
    tb_friends_sdl_sync_text();
}
