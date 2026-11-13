#!/usr/bin/env python3
"""Verify independent cache fixtures without any purchased files."""
import os
from pathlib import Path
import tempfile
import unittest
from compare_asset_cache_boots import ASSETS, prepare_profile


class ProfileTests(unittest.TestCase):
    def test_modes_never_share_writable_files(self):
        with tempfile.TemporaryDirectory(prefix='tank-cache-profiles-') as directory:
            root=Path(directory);source=root/'source';usr=source/'USRDIR';usr.mkdir(parents=True)
            (source/'PARAM.SFO').write_bytes(b'original synthetic metadata')
            (usr/'EBOOT.elf').write_bytes(b'original synthetic code placeholder')
            for name in ASSETS:
                (usr/name).write_bytes(b'original synthetic encrypted bytes')
                (usr/(name+'.dec')).write_bytes(b'original synthetic complete plaintext')
                (usr/(name+'.dec.complete')).write_bytes(b'original synthetic completion stamp')
            original={p.name:p.read_bytes() for p in usr.iterdir()}
            for mode in ('warm','fresh','interrupted'):
                title=root/mode;prepare_profile(source,title,mode)
                for name in ASSETS:
                    encrypted=title/'USRDIR'/name;cache=Path(str(encrypted)+'.dec')
                    self.assertFalse(encrypted.is_symlink())
                    self.assertNotEqual(encrypted.stat().st_ino,(usr/name).stat().st_ino)
                    if mode=='fresh':self.assertFalse(cache.exists())
                    else:
                        self.assertNotEqual(cache.stat().st_ino,(usr/(name+'.dec')).stat().st_ino)
                        cache.write_bytes(b'fixture mutation')
                    if mode=='interrupted':
                        self.assertGreater(cache.stat().st_mtime_ns,encrypted.stat().st_mtime_ns)
                        self.assertFalse(Path(str(cache)+'.complete').exists())
                with self.assertRaises(ValueError):prepare_profile(source,title,mode)
            self.assertEqual(original,{p.name:p.read_bytes() for p in usr.iterdir()})


if __name__=='__main__':unittest.main()
