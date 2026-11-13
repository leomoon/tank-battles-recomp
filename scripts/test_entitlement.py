#!/usr/bin/env python3
"""Verify native entitlement dispatch using entirely synthetic unlock files."""
import os
from pathlib import Path
import re
import resource
import struct
import subprocess
import unittest
import uuid
from Crypto.Cipher import AES
from Crypto.Hash import CMAC
from port import ROOT
from prepare_entitlement import CONTENT_ID
from synthetic_content import write_sfo


def mac(key, value):
    return CMAC.new(key, msg=value, ciphermod=AES).digest()


def constant(text, pattern):
    values = re.search(pattern + r'.*?\{([^}]+)\}', text, re.S)[1]
    return bytes(int(v, 16) for v in re.findall(r'0x([0-9a-fA-F]{2})\b', values))


def fixture(rif, *, outer=CONTENT_ID, inner=CONTENT_ID, magic=b'GOMA'):
    sdk = (ROOT / 'external/ps3recomp/libs/filesystem/edat.c').read_text()
    free = constant(sdk, r'"NP_KLIC_FREE"')
    omac2 = constant(sdk, r'NP_OMAC_KEY_2\[16\] =')
    omac3 = constant((ROOT / 'entitlement_edat.c').read_text(), r'unlock_title_omac\[16\] =')
    header = bytearray(256)
    header[:16] = b'NPD\0' + struct.pack('>III', 1, 2, 1)
    header[16:64] = outer.encode().ljust(48, b'\0')
    header[64:80] = bytes(range(16))
    header[80:96] = mac(omac3, header[16:64] + (CONTENT_ID + '.edat').encode())
    header[96:112] = mac(bytes(a ^ b for a, b in zip(free, omac2)), header[:96])
    header[128:144] = struct.pack('>IIQ', 0, 16384, 64)
    plain = magic + bytes(12) + inner.encode().ljust(48, b'\0')
    block_key = AES.new(rif, AES.MODE_ECB).encrypt(bytes(16))
    encrypted = AES.new(block_key, AES.MODE_CBC, iv=bytes(16)).encrypt(plain)
    return header + mac(block_key, encrypted) + encrypted + bytes(16)


class EntitlementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ROOT / 'build-tests' / ('entitlement-' + uuid.uuid4().hex)
        cls.root.mkdir(parents=True)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    def setUp(self):
        self.directory = self.root / self._testMethodName
        title = self.directory / 'dev_hdd0/game/TEST00000'
        title.mkdir(parents=True)
        write_sfo(title / 'PARAM.SFO', category='HG')
        self.exdata = self.directory / 'dev_hdd0/home/00000001/exdata'
        self.exdata.mkdir(parents=True)
        self.edat = self.exdata / (CONTENT_ID + '.edat')
        self.rif = Path(str(self.edat) + '.rifkey')
        self.key = bytes(range(16))  # Original synthetic test material.
        self.edat.write_bytes(fixture(self.key))
        self.rif.write_bytes(self.key)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('PS3_', 'TB_'))}
        self.env.update(PS3_PARAM_SFO=str(title / 'PARAM.SFO'), TB_EXPECTED_TITLE_ID='TEST00000',
                        PS3_HDD0_ROOT=str(self.directory / 'dev_hdd0'),
                        PS3_HDD1_ROOT=str(self.directory / 'dev_hdd1'),
                        PS3_GAMEDATA_ROOT=str(self.directory), PS3_VFS_ROOT=str(title),
                        PS3_DEV_FLASH=str(self.directory / 'dev_flash'), TB_ENTITLEMENT_PROBE='1',
                        SDL_AUDIODRIVER='dummy', SDL_VIDEODRIVER='dummy')

    def probe(self, expected):
        result = subprocess.run([str(ROOT / 'build-smoke/TankBattlesRecomp'),
                                 str(ROOT / 'build-smoke/synthetic/smoke.elf')],
                                env=self.env, cwd=ROOT, capture_output=True, timeout=10)
        log = (result.stdout + result.stderr).decode(errors='replace')
        (self.directory / 'probe.log').write_text(log)
        self.assertEqual(result.returncode, 0, log[-1800:])
        self.assertIn(f'[entitlement-probe] verify=0x{expected:08X} purchase=0x80028F81', log)
        self.assertFalse(list(self.exdata.glob('*.verify-*')), 'Transient plaintext was left behind')

    def test_valid_unlock(self):
        self.probe(0)

    def test_missing_unlock(self):
        self.edat.unlink()
        self.probe(0x80029521)

    def test_unrelated_filename(self):
        self.edat.rename(self.exdata / 'unrelated.edat')
        self.probe(0x80029521)

    def test_missing_rif(self):
        self.rif.unlink()
        self.probe(0x80029521)

    def test_invalid_rif_size(self):
        self.rif.write_bytes(b'invalid')
        self.probe(0x80029521)

    def test_wrong_rif_ignores_plaintext_cache_and_environment(self):
        self.rif.write_bytes(bytes(16))
        Path(str(self.edat) + '.dec').write_bytes(b'GOMA' + bytes(12) + CONTENT_ID.encode().ljust(48,b'\0'))
        self.env['PS3_RIF_KEY'] = self.key.hex()
        self.probe(0x80029521)

    def test_unrelated_outer_content_id(self):
        self.edat.write_bytes(fixture(self.key, outer='UP2006-OTHER0000_00-UNRELATED0000000'))
        self.probe(0x80029521)

    def test_unrelated_inner_content_id(self):
        self.edat.write_bytes(fixture(self.key, inner='UP2006-OTHER0000_00-UNRELATED0000000'))
        self.probe(0x80029521)

    def test_bad_payload_magic(self):
        self.edat.write_bytes(fixture(self.key, magic=b'FAIL'))
        self.probe(0x80029521)

    def test_tampered_header_title_devhash_and_data(self):
        original = self.edat.read_bytes()
        for offset in (0, 80, 96, 335):
            damaged = bytearray(original)
            damaged[offset] ^= 1
            self.edat.write_bytes(damaged)
            with self.subTest(offset=offset):
                self.probe(0x80029521)

    def test_null_argument(self):
        self.env['TB_ENTITLEMENT_NULL'] = '1'
        self.probe(0x80029502)

    def test_unterminated_argument(self):
        self.env['TB_ENTITLEMENT_CONTENT_ID'] = 'A' * 48
        self.probe(0x80029502)

    def test_unrelated_argument(self):
        self.env['TB_ENTITLEMENT_CONTENT_ID'] = '../unrelated'
        self.probe(0x80029521)

if __name__ == '__main__':
    unittest.main()
