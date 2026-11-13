/* Linux cache publication for the pinned EDAT resolver. SHA-1 here detects
 * accidental stale/corrupt cache contents; EDAT authentication remains in the
 * original decryptor. No key material is written to the completion record. */
#include <unistd.h>
#include <fcntl.h>
#include <sys/file.h>

static int tb_cache_digest(const char* path, uint8_t digest[20], uint64_t* size)
{
    FILE* f = fopen(path, "rb");
    if (!f) return 0;
    sha1_ctx state; sha1_init(&state);
    uint8_t block[65536]; size_t n; uint64_t total = 0;
    while ((n = fread(block, 1, sizeof block, f)) != 0) {
        sha1_update(&state, block, n); total += n;
    }
    int ok = !ferror(f);
    if (fclose(f)) ok = 0;
    if (ok) { sha1_final(&state, digest); *size = total; }
    return ok;
}

static int tb_cache_record(const char* source, const char* plain, uint8_t out[56])
{
    uint8_t header[0x100];
    FILE* input = fopen(source, "rb");
    if (!input) return 0;
    size_t n = fread(header, 1, sizeof header, input); fclose(input);
    if (n != sizeof header || memcmp(header, "NPD\0", 4)) return 0;
    uint64_t source_size, plain_size;
    memset(out, 0, 56); memcpy(out, "TBEDAT01", 8);
    if (!tb_cache_digest(source, out + 8, &source_size) ||
        !tb_cache_digest(plain, out + 28, &plain_size) ||
        plain_size != be64(header + 0x88)) return 0;
    for (unsigned i = 0; i < 8; ++i) out[48+i] = (uint8_t)(plain_size >> (56u-8u*i));
    return 1;
}

static int tb_cache_valid(const char* source, const char* plain, const char* marker)
{
    uint8_t expected[57], actual[56];
    FILE* f = fopen(marker, "rb");
    if (!f) return 0;
    size_t n = fread(expected, 1, sizeof expected, f); fclose(f);
    return n == sizeof actual && tb_cache_record(source, plain, actual) &&
           memcmp(expected, actual, sizeof actual) == 0;
}

const char* edat_resolve(const char* host_path, char* buf, size_t cap)
{
    const char* enabled = getenv("PS3_EDAT");
    if ((enabled && enabled[0] == '0') || !host_path || !edat_is_npd(host_path)) return host_path;
    if ((size_t)snprintf(buf, cap, "%s.dec", host_path) >= cap) return host_path;
    char lock_path[4096], marker[4096], temp[4096], stamp_temp[4096];
    if (snprintf(lock_path,sizeof lock_path,"%s.lock",buf) >= (int)sizeof lock_path ||
        snprintf(marker,sizeof marker,"%s.complete",buf) >= (int)sizeof marker ||
        snprintf(temp,sizeof temp,"%s.tmp-XXXXXX",buf) >= (int)sizeof temp ||
        snprintf(stamp_temp,sizeof stamp_temp,"%s.tmp-XXXXXX",marker) >= (int)sizeof stamp_temp) return host_path;
    int lock = open(lock_path, O_RDWR | O_CREAT | O_CLOEXEC, 0600);
    if (lock < 0) return host_path;
    if (flock(lock, LOCK_EX)) { close(lock); return host_path; }
    int ok = tb_cache_valid(host_path, buf, marker);
    if (!ok) {
        /* Orphan .tmp files from a killed writer are never cache candidates.
         * A lock released by process death allows a new producer to retry. */
        uint8_t before[20], after[20], record[56]; uint64_t a, b;
        int fd = mkstemp(temp), stamp = -1;
        if (fd >= 0) close(fd);
        ok = fd >= 0 && tb_cache_digest(host_path,before,&a) &&
             edat_decrypt_file(host_path,temp) == 0 &&
             tb_cache_record(host_path,temp,record) &&
             tb_cache_digest(host_path,after,&b) && a == b && !memcmp(before,after,20);
        if (ok) {
            fd = open(temp,O_RDONLY | O_CLOEXEC);
            ok = fd >= 0 && fsync(fd) == 0;
            if (fd >= 0) close(fd);
        }
        if (ok) {
            stamp = mkstemp(stamp_temp);
            ok = stamp >= 0 && write(stamp,record,sizeof record) == sizeof record && fsync(stamp) == 0;
            if (stamp >= 0 && close(stamp)) ok = 0;
        }
        /* The marker is last: a crash between renames causes regeneration,
         * never adoption of a half-written plaintext. */
        if (ok) ok = rename(temp,buf) == 0 && rename(stamp_temp,marker) == 0;
        unlink(temp); unlink(stamp_temp);
        if (!ok) fprintf(stderr,"[edat-cache] no completed cache published: %s\n",host_path);
    }
    flock(lock, LOCK_UN); close(lock);
    return ok ? buf : host_path;
}
