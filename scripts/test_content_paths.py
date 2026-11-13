#!/usr/bin/env python3
"""Exercise title discovery and EDAT IO using only original synthetic data."""
from pathlib import Path
import os
import re
import resource
import subprocess
import unittest
import uuid
from port import ROOT
from synthetic_content import write_sfo

class ContentPathsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = ROOT / "build-tests" / ("content-" + uuid.uuid4().hex)
        cls.directory.mkdir(parents=True)
        cls.sdk = ROOT / "external/ps3recomp"
        source = cls.sdk / "libs/filesystem/tests/test_edat_guest_klicensee.c"
        fixture = cls.directory / "fixture.c"
        # Reuse the upstream fixture generator, with no game bytes or private key.
        fixture.write_text('#define main upstream_test_main\n#include "' + str(source) + '"\n'
                           '#undef main\nint main(int argc, char** argv) {\n'
                           'unsigned char plain[FSIZE];\n'
                           'for (unsigned i=0;i<FSIZE;++i) plain[i]=(unsigned char)(i*7+(i>>8));\n'
                           'return argc==2 ? build_edat(argv[1],plain) : 1; }\n')
        cls.generator = cls.directory / "fixture"
        subprocess.run(["cc", "-std=gnu17", "-w", "-I" + str(cls.sdk / "include"),
                        str(fixture), str(source.with_name("test_edat_guest_klicensee_np.c")),
                        "-o", str(cls.generator)], check=True, capture_output=True)
        synthetic_key = re.search(r"KLIC\[16\].*?\{(.*?)\}", source.read_text(), re.S)[1]
        cls.key = bytes(int(v, 16) for v in re.findall(r"0x([0-9A-Fa-f]{2})", synthetic_key))
        cls.executable = ROOT / "build-smoke/TankBattlesRecomp"
        cls.elf = ROOT / "build-smoke/synthetic/smoke.elf"
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    def setUp(self):
        self.root = self.directory / self._testMethodName
        self.title = self.root / "dev_hdd0/game/TEST00000"
        self.usrdir = self.title / "USRDIR"
        self.usrdir.mkdir(parents=True)
        write_sfo(self.title / "PARAM.SFO", category="HG")
        self.encrypted = self.usrdir / "data.edat"
        subprocess.run([str(self.generator), str(self.encrypted)], check=True)
        self.reference = self.root / "reference.bin"
        self.reference.write_bytes(bytes((i * 7 + (i >> 8)) & 255 for i in range(2500)))
        self.manifest = self.root / "assets.manifest"
        self.manifest.write_text(f"/dev_hdd0/game/TEST00000/USRDIR/data.edat\t{self.reference}\n")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("PS3_", "TB_"))}
        self.env.update(PS3_PARAM_SFO=str(self.title / "PARAM.SFO"),
                        TB_EXPECTED_TITLE_ID="TEST00000", TB_ASSET_PROBE=str(self.manifest),
                        TB_PATH_PROBE_TOKEN=uuid.uuid4().hex, PS3_VFS_ROOT=str(self.title),
                        PS3_GAMEDATA_ROOT=str(self.root), PS3_HDD0_ROOT=str(self.root / "dev_hdd0"),
                        PS3_HDD1_ROOT=str(self.root / "dev_hdd1"),
                        PS3_DEV_FLASH=str(self.root / "dev_flash"),
                        SDL_AUDIODRIVER="dummy", SDL_VIDEODRIVER="dummy")

    def probe(self, key=None):
        result = subprocess.run([str(self.executable), str(self.elf)], cwd=ROOT,
                                env=self.env, input=self.key if key is None else key,
                                capture_output=True, timeout=30)
        self.log = (result.stdout + result.stderr).decode(errors="replace")
        (self.root / "probe.log").write_text(self.log)
        return result.returncode

    def test_plaintext_all_layers_and_cache_refresh(self):
        cache = Path(str(self.encrypted) + ".dec")
        self.assertFalse(cache.exists())
        self.assertEqual(self.probe(), 0, self.log[-1500:])
        self.assertIn("[asset-probe] ALL PASS files=1 title=TEST00000", self.log)
        self.assertEqual(cache.read_bytes(), self.reference.read_bytes())
        original_mtime = cache.stat().st_mtime_ns
        self.assertEqual(self.probe(), 0)
        self.assertEqual(cache.stat().st_mtime_ns, original_mtime)
        cache.write_bytes(b"stale synthetic cache")
        os.utime(cache, (1, 1))
        self.assertEqual(self.probe(), 0, self.log[-1500:])
        self.assertEqual(cache.read_bytes(), self.reference.read_bytes())
        self.assertGreater(cache.stat().st_mtime_ns, 1000000000)

    def test_wrong_key_never_adopts_plaintext_cache(self):
        self.assertNotEqual(self.probe(bytes(16)), 0)
        self.assertNotIn("[asset-probe] ALL PASS", self.log)
        self.assertFalse(Path(str(self.encrypted) + ".dec").exists())

    def test_wrong_title_is_rejected(self):
        self.env["TB_EXPECTED_TITLE_ID"] = "OTHER0000"
        self.assertEqual(self.probe(), 1)
        self.assertIn("PARAM.SFO title does not match", self.log)

    def test_missing_metadata_is_rejected(self):
        self.env["PS3_PARAM_SFO"] = str(self.root / "missing.sfo")
        self.assertEqual(self.probe(), 1)
        self.assertIn("Missing PARAM.SFO", self.log)

if __name__ == "__main__":
    unittest.main()
