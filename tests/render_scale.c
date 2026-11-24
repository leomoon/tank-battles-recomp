#include "render_scale_geometry.h"
#include <stdio.h>

int main(void)
{
    check_scale_geometry();
    puts("PASS: six render scales, odd extents, clipped resolve tiles, empty regions, viewport/depth bias and per-axis WPOS");
    return 0;
}
