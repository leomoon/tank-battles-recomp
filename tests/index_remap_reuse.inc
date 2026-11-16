/* Pure index-result reuse must never retain live guest vertex values or the
 * topology expansion selected by a later draw. Runs with and without cache. */
static void test_remap_reuse_fresh_vertices(void)
{
    engine_up();
    u8 saved[8 * 16];
    memcpy(saved, vm_base + VTX_OFFSET, sizeof saved);
    const u32 indices[] = {0, 1, 2, 0, 2, 3};
    for (u32 i = 0; i < 8; ++i) {
        float vertex[4] = {(float)i, 0, 0, 1};
        for (u32 c = 0; c < 4; ++c) {
            u32 word; memcpy(&word, vertex + c, 4);
            put_be(vm_base + VTX_OFFSET + i * 16 + c * 4, word, 4);
        }
    }
    for (u32 i = 0; i < 6; ++i) put_be(vm_base + IDX_OFFSET + i * 2, indices[i], 2);
    m(0x181C, IDX_OFFSET); m(0x1820, 1u | (1u << 4));
    m(0x1DAC, 0);
    for (u32 repeat = 0; repeat < 3; ++repeat) {
        float x = 10.0f + repeat; u32 word; memcpy(&word, &x, 4);
        put_be(vm_base + VTX_OFFSET, word, 4);
        m(M_BEGIN_END, RSX_PRIMITIVE_TRIANGLES); m(0x1824, 5u << 24); m(M_BEGIN_END, 0);
        CHECK(stub.draw_indices == 6 && stub.draw_vertices == 4 &&
              drawn_x(0) == x && drawn_x(3) == x && drawn_x(5) == 3,
              "repeated remap fetches current guest vertices (%u)", repeat);
    }
    m(M_BEGIN_END, RSX_PRIMITIVE_TRIANGLE_STRIP); m(0x1824, 5u << 24); m(M_BEGIN_END, 0);
    CHECK(stub.draw_indices == 12 && drawn_x(0) == 12 && drawn_x(3) == 2 && drawn_x(11) == 3,
          "identical remap inputs still use the current topology");
    put_be(vm_base + IDX_OFFSET, 3, 2);
    m(M_BEGIN_END, RSX_PRIMITIVE_TRIANGLES); m(0x1824, 5u << 24); m(M_BEGIN_END, 0);
    CHECK(drawn_x(0) == 3 && drawn_x(3) == 12,
          "same-size changed guest indices cannot reuse a stale result");
    memcpy(vm_base + VTX_OFFSET, saved, sizeof saved);
    memset(vm_base + IDX_OFFSET, 0, 64);
    engine_down();
}
