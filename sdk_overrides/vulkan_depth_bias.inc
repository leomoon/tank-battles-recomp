/* Preserve the draw engine's per-draw polygon offset in Vulkan. The D32
 * attachment uses floating-point depth-bias units. Match the SDK Metal
 * backend's conversion over [0.5, 1), where one D32 step is 2^-24. Guest
 * Z16 units therefore need a factor of 256. The slope term is independent
 * of the attachment's depth precision. */
static void tb_vk_set_depth_bias(void* user, float slope, float units, u32 depth_bits)
{
    (void)user;
    s_e2.depth_slope = slope;
    s_e2.depth_bias = depth_bits == 16u ? units * 256.0f : units;
}
