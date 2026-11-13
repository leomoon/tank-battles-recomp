#include "execution_policy_api.h"
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <mutex>
#include <set>
#include <tuple>

extern "C" void tb_rsx_texture_capture(uint32_t location, uint32_t offset, uint32_t format,
    uint32_t width, uint32_t height, uint32_t pitch, const void* raw, uint32_t raw_bytes,
    const void* decoded, uint32_t row_bytes, uint32_t rows)
{
    static const char* directory = getenv("TB_RSX_TEXTURE_CAPTURE");
    if (!directory || !*directory || !raw || !decoded || !width || !height || width > 2048 || height > 2048 || raw_bytes > (16u << 20)) return;
    static std::mutex mutex;
    std::lock_guard<std::mutex> lock(mutex);
    static std::set<std::tuple<uint32_t,uint32_t,uint32_t,uint32_t,uint32_t>> seen;
    const auto key = std::make_tuple(location, offset, format, width, height);
    if (seen.size() >= 96 || !seen.insert(key).second) return;
    char path[4096];
    const auto n = static_cast<unsigned>(seen.size());
    const unsigned base = format & 0x9f;
    if (base >= 0x86 && base <= 0x88) {
        snprintf(path, sizeof(path), "%s/texture-%03u-%08x.dds", directory, n, offset);
        if (FILE* file = fopen(path, "wb")) {
            uint32_t header[32] = {0x20534444,124,0x81007,height,width,row_bytes*rows,0,1};
            header[19]=32; header[20]=4;
            header[21]=base==0x86 ? 0x31545844 : base==0x87 ? 0x33545844 : 0x35545844;
            header[27]=0x1000;
            fwrite(header,1,sizeof(header),file); fwrite(decoded,row_bytes,rows,file); fclose(file);
        }
    }
    if (rows == height && row_bytes == width * 4) {
        snprintf(path, sizeof(path), "%s/texture-%03u-%08x.ppm", directory, n, offset);
        if (FILE* file = fopen(path, "wb")) {
            fprintf(file, "P6\n%u %u\n255\n", width, height);
            const auto* rgba = static_cast<const unsigned char*>(decoded);
            for (uint32_t i = 0; i < width * height; ++i) fwrite(rgba + i * 4, 1, 3, file);
            fclose(file);
        }
    }
    snprintf(path, sizeof(path), "%s/texture-%03u-%08x.raw", directory, n, offset);
    if (FILE* file = fopen(path, "wb")) { fwrite(raw, 1, raw_bytes, file); fclose(file); }
    snprintf(path, sizeof(path), "%s/textures.jsonl", directory);
    if (FILE* file = fopen(path, "a")) {
        fprintf(file, "{\"n\":%u,\"location\":%u,\"offset\":%u,\"format\":%u,\"width\":%u,\"height\":%u,\"pitch\":%u,\"raw_bytes\":%u}\n",
            n,location,offset,format,width,height,pitch,raw_bytes);
        fclose(file);
    }
}
