#include "render_scale_geometry.h"
#include <stdio.h>

int main(void)
{
    check_scale_geometry();
    puts("PASS: render-scale presets, limits, shared rectangle edges and shader coordinate conversion");
    return 0;
}
