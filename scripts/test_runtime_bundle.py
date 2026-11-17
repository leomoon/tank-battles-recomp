"""Negative controls for artifact allowlists, ABI and content integrity."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from runtime_bundle import verify, archive_members

class RuntimeBundle(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'include').mkdir()
        (self.root/'include/ppu_recomp.h').write_text('original ABI fixture')
        digest=hashlib.sha256((self.root/'include/ppu_recomp.h').read_bytes()).hexdigest()
        self.manifest=dict(schema=1,kind='game-free-runtime',source_commit='original-source',abi_sha256=digest,files={'include/ppu_recomp.h':digest})
        self.write()
    def write(self):
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
    def test_valid(self):
        verify(self.root,'original-source',self.manifest['abi_sha256'])
    def test_wrong_identity(self):
        for kwargs in ({'source':'different-source'},{'abi':'different-abi'}):
            with self.assertRaises(ValueError):verify(self.root,**kwargs)
    def test_changed_payload(self):
        (self.root/'include/ppu_recomp.h').write_text('changed')
        with self.assertRaises(ValueError):verify(self.root)
    def test_missing_payload(self):
        (self.root/'include/ppu_recomp.h').unlink()
        with self.assertRaises(ValueError):verify(self.root)
    def test_added_payload(self):
        (self.root/'forbidden.bin').write_bytes(b'fixture')
        with self.assertRaises(ValueError):verify(self.root)
    def test_archive_rejects_non_archive(self):
        path=self.root/'invalid.lib';path.write_bytes(b'not an archive')
        with self.assertRaises(ValueError):archive_members(path)
    def test_coff_archive_long_names(self):
        def member(name,payload):
            header=f'{name:<16}{0:<12}{0:<6}{0:<6}{0:<8}{len(payload):<10}`\n'.encode()
            return header+payload+(b'\n' if len(payload)%2 else b'')
        blob=b'!<arch>\n'+member('//',b'production_callback.cpp.obj\x00')+member('/0',b'original object fixture')
        path=self.root/'coff.lib';path.write_bytes(blob)
        self.assertEqual(archive_members(path),['production_callback.cpp.obj'])
    def test_path_traversal(self):
        self.manifest['files']['../outside']='hash';self.write()
        with self.assertRaises(ValueError):verify(self.root)

if __name__=='__main__':unittest.main()
