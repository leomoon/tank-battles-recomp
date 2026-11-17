/* Reuse the pinned SDK codec privately for strict unlock verification.
 * This instance has no guest keys or persistent plaintext cache. */
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#include <stdio.h>
/* std::filesystem paths cross this bridge as UTF-8 on both platforms. */
static FILE* unlock_fopen(const char* path, const char* mode) {
    int size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, NULL, 0);
    if (!size) return NULL;
    wchar_t* wide = (wchar_t*)malloc((size_t)size * sizeof(wchar_t));
    if (!wide) return NULL;
    FILE* file = NULL;
    wchar_t wide_mode[8];
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, wide, size) &&
        MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, mode, -1, wide_mode, 8))
        file = _wfopen(wide, wide_mode);
    free(wide);
    return file;
}
#define fopen unlock_fopen
#endif
static char* unlock_getenv(const char* name) {
    return strcmp(name, "PS3_RIF_KEY") == 0 ? NULL : getenv(name);
}
#define getenv unlock_getenv
#define edat_add_klicensee tb_unlock_add_klicensee
#define edat_selftest tb_unlock_selftest
#define edat_is_npd tb_unlock_is_npd
#define edat_decrypt_file tb_unlock_decrypt_file
#define edat_resolve tb_unlock_resolve
#include "edat.c"
#undef getenv

/* Public platform format constant, also documented in RPCS3 Crypto/key_vault.h.
 * This is not a title or purchaser key and grants no entitlement. */
static const uint8_t unlock_title_omac[16] = {
    0x9B,0x51,0x5F,0xEA,0xCF,0x75,0x06,0x49,
    0x81,0xAA,0x60,0x4D,0x91,0xA5,0x4E,0x97
};
int tank_battles_validate_unlock(const char* path, const char* content_id)
{
    unsigned char file[352], expected[16], key[16], title[256];
    const unsigned char* header = file;
    FILE* input = fopen(path, "rb");
    if (!input) return 0;
    size_t count = fread(file, 1, sizeof file, input);
    const int trailing = fgetc(input);
    const int read_error = ferror(input);
    fclose(input);
    /* The observed C00 unlock is a single authenticated 64-byte block.
     * Restrict the supported format before the shared codec sees its sizes. */
    const uint32_t license = count >= 12 ? be32(header+8) : 0;
    if (count != sizeof file || trailing != EOF || read_error || memcmp(header, "NPD\0", 4) ||
        be32(header+4) != 1 || (license != 2 && license != 3) || be32(header+12) != 1 ||
        be32(header+0x80) != 0 || be32(header+0x84) != 0x4000 || be64(header+0x88) != 64)
        return 0;
    const size_t id_length = strlen(content_id);
    if (id_length >= 48 || memcmp(header+16, content_id, id_length) || header[16+id_length]) return 0;
    for (unsigned i=0; i<16; ++i) key[i] = KLIC_CANDIDATES[1].key[i] ^ NP_OMAC_KEY_2[i];
    aes128_cmac(key, header, 0x60, expected);
    if (memcmp(expected, header+0x60, 16)) return 0;
    const char* filename = strrchr(path, '/');
#ifdef _WIN32
    const char* backslash = strrchr(path, '\\');
    if (backslash && (!filename || backslash > filename)) filename = backslash;
#endif
    filename = filename ? filename+1 : path;
    const size_t name_length = strlen(filename);
    if (48+name_length > sizeof title) return 0;
    memcpy(title, header+16, 48); memcpy(title+48, filename, name_length);
    aes128_cmac(unlock_title_omac, title, 48+name_length, expected);
    if (memcmp(expected, header+0x50, 16)) return 0;

    /* Never accept a previous .dec/.unedat file or an ambient RIF key.
     * License 2 uses the purchased EDAT's own .rifkey sidecar. License 3 uses
     * the platform free key, still authenticating the header and data block. */
    if (tb_unlock_selftest() != 0) return 0;
    memcpy(key, KLIC_CANDIDATES[1].key, sizeof key);
    if (license == 2) {
        char sidecar[1200];
        if (snprintf(sidecar, sizeof sidecar, "%s.rifkey", path) >= (int)sizeof sidecar) return 0;
        FILE* rif = fopen(sidecar, "rb");
        if (!rif) return 0;
        count = fread(key, 1, sizeof key, rif);
        const int extra = fgetc(rif);
        const int error = ferror(rif);
        fclose(rif);
        if (count != sizeof key || extra != EOF || error) {
            memset(key, 0, sizeof key);
            return 0;
        }
    }
    /* This bridge accepts only the version-1, flags-0 single-block C00
     * layout checked above. Its block index and CBC IV are both zero.
     * Use the pinned codec's tested AES/CMAC primitives directly so the
     * authenticated 64-byte payload never needs a plaintext disk file. */
    unsigned char expanded[176], zero[16] = {0}, block[16], plain[64] = {0};
    aes128_expand(key, expanded);
    aes128_encrypt_block(expanded, zero, block);
    aes128_cmac(block, file+0x110, sizeof plain, expected);
    int ok = memcmp(expected, file+0x100, 16) == 0;
    if (ok) aes128_cbc_decrypt(block, zero, file+0x110, plain, sizeof plain);
    ok = ok && !memcmp(plain, "GOMA", 4) &&
         !memcmp(plain+16, content_id, id_length) && !plain[16+id_length];
    memset(key, 0, sizeof key); memset(plain, 0, sizeof plain);
    memset(expanded, 0, sizeof expanded); memset(block, 0, sizeof block);
    return ok;
}
