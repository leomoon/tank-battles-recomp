#pragma once
#include "rsx_texture_layout.h"
#ifdef __cplusplus
extern "C" {
#endif
int tb_texture_decode_specialized(void*,u32,const u8*,u32,u32,const rsx_tex_layout*,int);
void tb_texture_decode_reference(void*,u32,const u8*,u32,u32,const rsx_tex_layout*,int);
#ifdef __cplusplus
}
#endif
