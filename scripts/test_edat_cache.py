#!/usr/bin/env python3
"""Test cache recovery/publication with original synthetic EDAT bytes only."""
import os
from pathlib import Path
import signal
import subprocess
import time
import unittest
import uuid
from port import ROOT


class EdatCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ROOT / 'build-tests' / ('edat-cache-' + uuid.uuid4().hex)
        cls.root.mkdir(parents=True)
        fixture = cls.root / 'fixture.c'
        fixture.write_text(r'''
#define edat_resolve original_edat_resolve
#define main upstream_main
#include "external/ps3recomp/libs/filesystem/tests/test_edat_guest_klicensee.c"
#undef main
#undef edat_resolve
#include <signal.h>
static int test_decrypt(const char* source,const char* output) {
    const char* mode=getenv("TB_CACHE_TEST_MODE");
    if(mode && !strcmp(mode,"pause")) {
        FILE* f=fopen(output,"wb");fwrite("unfinished",1,10,f);fclose(f);
        raise(SIGSTOP);
    }
    int rc=edat_decrypt_file(source,output);
    if(mode && !strcmp(mode,"change-source")) {
        FILE* f=fopen(source,"ab");fputc(7,f);fclose(f);
    }
    return rc;
}
#define edat_decrypt_file test_decrypt
#include "sdk_overrides/edat_cache.inc"
#undef edat_decrypt_file
int main(int argc,char** argv) {
    if(argc!=3)return 10;
    uint8_t plain[FSIZE];for(unsigned i=0;i<FSIZE;i++)plain[i]=(i*7+(i>>8))&255;
    if(!strcmp(argv[1],"init"))return build_edat(argv[2],plain);
    if(strcmp(argv[1],"wrong-key"))edat_add_klicensee(KLIC);
    char buf[4096];const char* p=!strcmp(argv[1],"old")?original_edat_resolve(argv[2],buf,sizeof buf):edat_resolve(argv[2],buf,sizeof buf);
    if(p==argv[2])return 2;
    FILE* f=fopen(p,"rb");uint8_t actual[FSIZE+1];size_t n=f?fread(actual,1,sizeof actual,f):0;if(f)fclose(f);
    return n==FSIZE && !memcmp(actual,plain,FSIZE)?0:3;
}
''')
        cls.binary = cls.root / 'fixture'
        subprocess.run(['cc', '-std=gnu17', '-w', '-I', str(ROOT), '-I', str(ROOT/'external/ps3recomp/include'),
                        str(fixture), str(ROOT/'external/ps3recomp/libs/filesystem/tests/test_edat_guest_klicensee_np.c'),
                        '-o', str(cls.binary)], check=True)

    def setUp(self):
        self.directory = self.root / self._testMethodName
        self.directory.mkdir()
        self.source = self.directory / 'data.edat'
        self.cache = Path(str(self.source) + '.dec')
        self.marker = Path(str(self.cache) + '.complete')
        self.invoke('init')

    def invoke(self, mode='resolve', code=0):
        result = subprocess.run([str(self.binary), mode, str(self.source)], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, code, result.stderr.decode(errors='replace')[-1600:])

    def test_old_resolver_accepts_newer_partial_cache(self):
        self.cache.write_bytes(b'partial')
        os.utime(self.cache, (time.time()+10, time.time()+10))
        self.invoke('old', code=3)  # It adopted the incomplete cache.
        self.invoke()

    def test_completed_cache_reused_without_rewriting(self):
        self.invoke()
        stamp = self.cache.stat().st_mtime_ns
        self.invoke()
        self.assertEqual(stamp, self.cache.stat().st_mtime_ns)
        self.assertEqual(self.marker.stat().st_size, 56)

    def test_truncated_and_same_size_corruption_recovered(self):
        self.invoke()
        plain = self.cache.read_bytes()
        for bad in (plain[:30], b'x'*len(plain)):
            self.cache.write_bytes(bad)
            os.utime(self.cache, (time.time()+10, time.time()+10))
            self.invoke()
            self.assertEqual(self.cache.read_bytes(), plain)

    def test_unmarked_full_size_cache_is_not_trusted(self):
        self.cache.write_bytes(b'x'*2500)
        self.invoke()
        self.assertTrue(self.marker.exists())

    def test_wrong_key_does_not_publish(self):
        self.invoke('wrong-key', code=2)
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.marker.exists())

    def test_changed_encrypted_source_invalidates_cache(self):
        self.invoke()
        content = self.source.read_bytes()
        damaged = bytearray(content); damaged[-5] ^= 1
        self.source.write_bytes(damaged)
        self.invoke(code=2)
        self.source.write_bytes(content)
        self.invoke()

    def test_failed_marker_publication_never_adopted(self):
        self.marker.mkdir()
        self.invoke(code=2)
        self.assertTrue(self.cache.exists())
        self.marker.rmdir()
        self.invoke()

    def test_killed_writer_preserves_previous_final_and_recovers(self):
        self.invoke()
        old = self.cache.read_bytes()
        self.marker.unlink()
        env = dict(os.environ, TB_CACHE_TEST_MODE='pause')
        with (self.directory/'writer.log').open('wb') as log:
            child = subprocess.Popen([str(self.binary), 'resolve', str(self.source)],env=env,stdout=log,stderr=log)
            try:
                deadline = time.monotonic()+3
                while time.monotonic()<deadline:
                    temps=list(self.directory.glob('*.dec.tmp-*'))
                    if temps and temps[0].stat().st_size==10: break
                    time.sleep(.01)
                else:self.fail('writer did not reach unpublished partial output')
                self.assertEqual(self.cache.read_bytes(), old)
                child.kill();child.wait(timeout=2)
            finally:
                if child.poll() is None:child.kill();child.wait()
        self.invoke()
        self.assertEqual(self.cache.read_bytes(), old)

    def test_source_changed_during_creation_is_not_published(self):
        env=dict(os.environ, TB_CACHE_TEST_MODE='change-source')
        result=subprocess.run([str(self.binary),'resolve',str(self.source)],env=env,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,2)
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.marker.exists())

    def test_concurrent_writers_publish_one_consistent_cache(self):
        children=[subprocess.Popen([str(self.binary),'resolve',str(self.source)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) for _ in range(2)]
        for child in children:self.assertEqual(child.wait(timeout=5),0)
        self.invoke()


if __name__=='__main__':unittest.main()
