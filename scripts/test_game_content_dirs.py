"""cellGameContentPermit must not create directories inside the read-only title folder.

Host paths longer than the 128-byte guest limit must not be truncated either:
that created stray prefix directories (USR, USRD) through measurement profiles.

The pinned SDK also created <VFS root>/game/<title>/USRDIR, which is right for a
disc-style VFS root but, with this port's HDD layout (VFS root = the installed
title folder, /dev_hdd0 = PS3_HDD0_ROOT), made NPUB30154/game/NPUB30154/USRDIR
inside the game. Runs the instrumented source copy game-free.
"""
import subprocess
import sys
import tempfile
from pathlib import Path
from port import ROOT

sdk = ROOT / 'external/ps3recomp'
out = ROOT / 'build-tests/game-content-dirs'
out.mkdir(parents=True, exist_ok=True)
subprocess.run([sys.executable, str(ROOT / 'scripts/instrument_sdk.py'), '--sdk', str(sdk), '--output', str(out / 'overlay')],
               check=True)
source = r'''
#include "@OVERLAY@/libs/system/cellGame.c"
#include <assert.h>
#include <stdlib.h>
#include <sys/stat.h>
static int is_dir(const char* p) { struct stat st; return stat(p, &st) == 0 && S_ISDIR(st.st_mode); }
int main(int argc, char** argv) {
    char title[1024], hdd0[1024], inside[1100], usrdir[1100];
    snprintf(title, sizeof title, "%s/title/NPUB30154", argv[1]);
    snprintf(hdd0, sizeof hdd0, "%s/dev_hdd0", argv[1]);
    snprintf(inside, sizeof inside, "%s/game", title);
    snprintf(usrdir, sizeof usrdir, "%s/game/NPUB30154/USRDIR", hdd0);
    ensure_dirs(title);
    ppu_vfs_root = title;
    s_check_is_disc = 0;
    strcpy(s_check_dir, "NPUB30154");
    snprintf(s_content_info_path, sizeof s_content_info_path, "%s/game/NPUB30154", hdd0);
    snprintf(s_usrdir_path, sizeof s_usrdir_path, "%s", usrdir);
    if (!strcmp(argv[2], "hdd0")) {
        setenv("PS3_HDD0_ROOT", hdd0, 1);
        assert(cellGameContentPermit(NULL, NULL) == CELL_OK);
        assert(is_dir(usrdir));                 /* the real game-data USRDIR */
        assert(!is_dir(inside));                /* nothing inside the title folder */
        puts("PASS: with an HDD0 root, ContentPermit creates game data under it and nothing inside the title folder");
    } else {
        unsetenv("PS3_HDD0_ROOT");
        assert(cellGameContentPermit(NULL, NULL) == CELL_OK);
        char vpath[1200];
        snprintf(vpath, sizeof vpath, "%s/game/NPUB30154/USRDIR", title);
        assert(is_dir(vpath));                  /* disc-style VFS root keeps the SDK behaviour */
        puts("PASS: without an HDD0 root, the SDK's VFS-root game-data directory is still created");
    }
    return 0;
}
'''.replace('@OVERLAY@', str(out / 'overlay'))
(out / 'fixture.c').write_text(source)
# Link-only stand-ins for runtime symbols the file references; no SDK headers, so no type clashes.
(out / 'stubs.c').write_text('''#include <stdint.h>
void* g_ps3_guest_caller; int g_resv_store_active; uint32_t g_ww_hi, g_ww_lo;
const char* ppu_vfs_root; uint8_t* vm_base;
void ppu_resv_break_store(void) {} void ps3_ww_report_inline(void) {} void spu_coh_notify_write(void) {}
void spu_lockline_lock(void) {} void spu_lockline_unlock(void) {}
''')
includes = ['-I' + str(ROOT), '-I' + str(sdk / 'include'), '-I' + str(sdk / 'libs/system'),
            '-I' + str(sdk / 'runtime/memory'), '-I' + str(sdk / 'runtime/platform'), '-I' + str(sdk / 'runtime/ppu')]
subprocess.run(['cc', '-std=gnu17', '-O1', *includes, str(out / 'fixture.c'), str(out / 'stubs.c'), '-o', str(out / 'fixture')], check=True)
for mode in ('hdd0', 'disc'):
    for deep in (False, True):
        with tempfile.TemporaryDirectory(prefix='tb-game-dirs-') as temporary:
            # A repository cloned deep in the filesystem: host paths far past the 128-byte guest limit.
            base = Path(temporary) / ('long-install-path-' * 8 if deep else 'short')
            base.mkdir()
            result = subprocess.run([str(out / 'fixture'), str(base), mode], capture_output=True, text=True, timeout=10)
            if result.returncode:
                raise SystemExit(result.stdout + result.stderr)
            if mode == 'hdd0':
                created = sorted(p.name for p in (base / 'dev_hdd0/game/NPUB30154').iterdir())
                if created != ['USRDIR']:
                    raise SystemExit(f'unexpected directories beside USRDIR (truncated host path?): {created}')
            print([line for line in result.stdout.splitlines() if line.startswith('PASS')][0],
                  f'(host path {len(str(base)) + 40} bytes)' if deep else '')
