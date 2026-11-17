#include <assert.h>
int main(void)
{
    assert(!vk_init_all(16,16,"NV3089 exact gather fixture"));
    assert(!vk_eng_init(NULL,16,16));
    assert(tb_vk_blit_exact()==1);
    vk_eng_shutdown(NULL);
    pvkDeviceWaitIdle(s_vk.device);
    puts("PASS: actual GPU NV3089 gather matches guest-memory point sampling");
    return 0;
}
