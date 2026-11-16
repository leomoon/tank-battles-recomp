/* Reuse the pinned SDK codec privately for strict unlock verification.
 * This instance has no guest keys or persistent plaintext cache. */
#include <stdlib.h>
#include <string.h>
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
#ifdef _WIN32
#include <io.h>
#include <fcntl.h>
static int unlock_temp(char* path, size_t capacity) {
    if (_mktemp_s(path, capacity)) return -1;
    return _open(path, _O_RDWR | _O_CREAT | _O_EXCL | _O_BINARY, _S_IREAD | _S_IWRITE);
}
#define close _close
#else
#include <unistd.h>
static int unlock_temp(char* path, size_t capacity) {
    (void)capacity;
    return mkstemp(path);
}
#endif

/* Public platform format constant, also documented in RPCS3 Crypto/key_vault.h.
 * This is not a title or purchaser key and grants no entitlement. */
static const uint8_t unlock_title_omac[16] = {
    0x9B,0x51,0x5F,0xEA,0xCF,0x75,0x06,0x49,
    0x81,0xAA,0x60,0x4D,0x91,0xA5,0x4E,0x97
};
int tank_battles_validate_unlock(const char* path, const char* content_id)
{
    unsigned char header[0x100], expected[16], key[16], title[256];
    FILE* input = fopen(path, "rb");
    if (!input) return 0;
    size_t count = fread(header, 1, sizeof header, input);
    fseek(input, 0, SEEK_END);
    long length = ftell(input);
    fclose(input);
    /* The observed C00 unlock is a single authenticated 64-byte block.
     * Restrict the supported format before the shared codec sees its sizes. */
    const uint32_t license = count >= 12 ? be32(header+8) : 0;
    if (count != sizeof header || length != 352 || memcmp(header, "NPD\0", 4) ||
        be32(header+4) != 1 || (license != 2 && license != 3) || be32(header+12) != 1 ||
        be32(header+0x80) != 0 || be32(header+0x84) != 0x4000 || be64(header+0x88) != 64)
        return 0;
    if (license == 2) {
        char sidecar[1200];
        if (snprintf(sidecar, sizeof sidecar, "%s.rifkey", path) >= (int)sizeof sidecar) return 0;
        FILE* rif = fopen(sidecar, "rb");
        if (!rif) return 0;
        const int seek_ok = fseek(rif, 0, SEEK_END) == 0;
        const long rif_size = seek_ok ? ftell(rif) : -1;
        fclose(rif);
        if (rif_size != 16) return 0;
    }
    const size_t id_length = strlen(content_id);
    if (id_length >= 48 || memcmp(header+16, content_id, id_length) || header[16+id_length]) return 0;
    for (unsigned i=0; i<16; ++i) key[i] = KLIC_CANDIDATES[1].key[i] ^ NP_OMAC_KEY_2[i];
    aes128_cmac(key, header, 0x60, expected);
    if (memcmp(expected, header+0x60, 16)) return 0;
    const char* filename = strrchr(path, '/');
    filename = filename ? filename+1 : path;
    const size_t name_length = strlen(filename);
    if (48+name_length > sizeof title) return 0;
    memcpy(title, header+16, 48); memcpy(title+48, filename, name_length);
    aes128_cmac(unlock_title_omac, title, 48+name_length, expected);
    if (memcmp(expected, header+0x50, 16)) return 0;

    /* Never accept a previous .dec/.unedat file or an ambient RIF key.
     * License 2 uses the purchased EDAT's own .rifkey sidecar. License 3 uses
     * the platform free key, still authenticating the header and data block. */
    char output[1200];
    if (snprintf(output, sizeof output, "%s.verify-XXXXXX", path) >= (int)sizeof output) return 0;
    int descriptor = unlock_temp(output, sizeof output);
    if (descriptor < 0) return 0;
    close(descriptor);
    int ok = tb_unlock_decrypt_file(path, output) == 0;
    unsigned char plain[65] = {0};
    input = ok ? fopen(output, "rb") : NULL;
    count = input ? fread(plain, 1, sizeof plain, input) : 0;
    if (input) fclose(input);
    remove(output);
    ok = ok && count == 64 && !memcmp(plain, "GOMA", 4) &&
         !memcmp(plain+16, content_id, id_length) && !plain[16+id_length];
    memset(key, 0, sizeof key); memset(plain, 0, sizeof plain);
    return ok;
}
