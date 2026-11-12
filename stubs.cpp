/* Tank Battles-specific runtime hooks. No game code is included here. */

/* The starter runner calls this after registering the translated function
 * table. Add any required lifted PRX registrations here as porting proceeds.
 * Game-specific HLE overrides should be registered after ppu_sysprx_register
 * in main.cpp so they take precedence over the shared runtime handlers. */
extern "C" void ps3_load_prx_modules(void)
{
}
