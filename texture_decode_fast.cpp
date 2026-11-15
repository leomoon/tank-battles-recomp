/* Format-specialized decoding adapted from rsx_tex_decode.h in
 * bc160201983/GoW-Ascension-Recomp, commit 55e277c094678e695cf5e193c254708013d14cda.
 * Copyright (c) 2026 Ned Heller / sp00nznet and BilalReloaded.
 * MIT notices: licenses/ps3kit-MIT.txt, licenses/GoW-Ascension-Recomp-MIT.txt.
 *
 * Retain this port's native float/depth formats, destination pitch and shader
 * remapping. The peer's RGBA8 conversion/remap is deliberately not imported.
 */
#include "texture_decode.h"
#include <array>
#include <bit>
#include <cstring>
#if !defined(TB_TEXTURE_DECODE_NO_SIMD) && (defined(__SSE2__) || defined(_M_X64))
#include <emmintrin.h>
#define TB_DECODE_SSE2 1
#endif

namespace {
template<unsigned Format> const auto& packed_table() {
    struct Table {
        std::array<std::array<u8,4>,65536> values;
        Table() {
            for(u32 v=0;v<65536;v++) {
                auto& s=values[v];
                if constexpr(Format==0x83) {
                    s={u8(((v>>8)&15)*17),u8(((v>>4)&15)*17),u8((v&15)*17),u8(((v>>12)&15)*17)};
                } else if constexpr(Format==0x84) {
                    s={u8(((v>>11)&31)*255/31),u8(((v>>5)&63)*255/63),u8((v&31)*255/31),255};
                } else if constexpr(Format==0x97) {
                    s={u8(((v>>11)&31)*255/31),u8(((v>>6)&31)*255/31),u8(((v>>1)&31)*255/31),u8((v&1)?255:0)};
                } else {
                    s={u8(((v>>10)&31)*255/31),u8(((v>>5)&31)*255/31),u8((v&31)*255/31),u8(Format==0x9d || (v&0x8000)?255:0)};
                }
            }
        }
    };
    static const Table table; // C++ initialization is synchronized on both hosts.
    return table.values;
}

template<unsigned Source,unsigned Destination,class Decode>
void walk(u8* dst,u32 pitch,const u8* src,u32 w,u32 h,const rsx_tex_layout& tl,Decode decode) {
    if(tl.swizzled) {
        const u32 lw=rsx_log2_ceil(w),lh=rsx_log2_ceil(h);
        const u32 mask=rsx_swizzle_offset((1u<<lw)-1u,0,lw,lh);
        for(u32 y=0;y<h;y++) {
            const u32 yo=rsx_swizzle_offset(0,y,lw,lh);u32 xo=0;
            u8* row=dst+size_t(y)*pitch;
            for(u32 x=0;x<w;x++) {
                decode(src+size_t(xo|yo)*Source,row+size_t(x)*Destination);
                xo=(xo-mask)&mask;
            }
        }
    } else {
        for(u32 y=0;y<h;y++) {
            const u8* input=src+size_t(y)*tl.row_bytes;u8* output=dst+size_t(y)*pitch;
            for(u32 x=0;x<w;x++)decode(input+size_t(x)*Source,output+size_t(x)*Destination);
        }
    }
}
template<unsigned Format>
void packed(u8* dst,u32 pitch,const u8* src,u32 w,u32 h,const rsx_tex_layout& tl) {
    const auto& table=packed_table<Format>();
    walk<2,4>(dst,pitch,src,w,h,tl,[&](const u8* s,u8* d){std::memcpy(d,table[(u32(s[0])<<8)|s[1]].data(),4);});
}
}
extern "C" int tb_texture_decode_specialized(void* dst,u32 pitch,const u8* src,u32 w,u32 h,const rsx_tex_layout* tl,int argb) {
    if(!dst || !src || !tl || !w || !h || tl->compressed)return 0;
    // Keep original row copies for compressed, R8 and linear R8G8 paths.
    if constexpr(std::endian::native!=std::endian::little)return 0;
    auto* d=static_cast<u8*>(dst);
    const auto sb=tl->bytes_per_texel,db=tl->dst_bytes_per_texel;
    if(w>4096 || h>4096)return 0;
    const auto source=reinterpret_cast<uintptr_t>(src),target=reinterpret_cast<uintptr_t>(dst);
    const uint64_t source_size=tl->face_bytes,target_size=uint64_t(h-1)*pitch+uint64_t(w)*db;
    if(source<=target ? target-source<source_size : source-target<target_size)return 0;
    switch(tl->base_format) {
    case 0x92: case 0x94:
        if(sb!=2 || db!=2 || tl->fmt!=RSX_TEXFMT_R16)return 0;
        if(tl->swizzled) {
            walk<2,2>(d,pitch,src,w,h,*tl,[](const u8* s,u8* out){out[0]=s[1];out[1]=s[0];});
        } else {
            // Select the native format once, like the peer's specialized loops.
            // SSE2 is baseline on x86-64; no AVX2/FMA or precision conversion.
            for(u32 y=0;y<h;y++) {
                const u8* input=src+size_t(y)*tl->row_bytes;u8* output=d+size_t(y)*pitch;u32 x=0;
#ifdef TB_DECODE_SSE2
                for(;x+8<=w;x+=8) {
                    const __m128i v=_mm_loadu_si128(reinterpret_cast<const __m128i*>(input+size_t(x)*2));
                    const __m128i swapped=_mm_or_si128(_mm_slli_epi16(v,8),_mm_srli_epi16(v,8));
                    _mm_storeu_si128(reinterpret_cast<__m128i*>(output+size_t(x)*2),swapped);
                }
#endif
                for(;x<w;x++){output[x*2]=input[x*2+1];output[x*2+1]=input[x*2];}
            }
        }
        return 1;
    case 0x82: case 0x83: case 0x84: case 0x97: case 0x9d:
        if(sb!=2 || db!=4 || tl->fmt!=RSX_TEXFMT_R8G8B8A8)return 0;
        switch(tl->base_format) {
        case 0x82:packed<0x82>(d,pitch,src,w,h,*tl);break;
        case 0x83:packed<0x83>(d,pitch,src,w,h,*tl);break;
        case 0x84:packed<0x84>(d,pitch,src,w,h,*tl);break;
        case 0x97:packed<0x97>(d,pitch,src,w,h,*tl);break;
        default:packed<0x9d>(d,pitch,src,w,h,*tl);break;
        }
        return 1;
    case 0x85: case 0x9e: {
        if(sb!=4 || db!=4 || tl->fmt!=RSX_TEXFMT_R8G8B8A8)return 0;
        // Preserve the already-vectorized linear path in the pinned decoder.
        if(!tl->swizzled)return 0;
        const u32 alpha=tl->base_format==0x9e?0xff000000u:0;
        walk<4,4>(d,pitch,src,w,h,*tl,[&](const u8* s,u8* out){u32 v;std::memcpy(&v,s,4);if(!argb)v=(v>>8)|(v<<24);v|=alpha;std::memcpy(out,&v,4);});
        return 1;
    }
    case 0x90:
        if(sb!=4 || db!=4 || tl->fmt!=RSX_TEXFMT_R32F)return 0;
        walk<4,4>(d,pitch,src,w,h,*tl,[](const u8* s,u8* out){const u32 depth=(u32(s[0])<<16)|(u32(s[1])<<8)|s[2];const float f=float(depth)/16777215.0f;std::memcpy(out,&f,4);});
        return 1;
    default:return 0; // Native half/float/endian, remaps and unknowns stay exact.
    }
}
