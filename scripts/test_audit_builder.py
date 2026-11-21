"""The builder payload audit rejects personal and purchased files, title code,
executables outside the verified trees, private keys, modified resources and
tampered or unsafe Builder.exe payloads, and passes a clean package."""
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile

import audit_builder
import package_builder

ROOT = Path(__file__).resolve().parents[1]


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = Path(tempfile.mkdtemp(prefix="tb audit "))
        cls.clean = cls.temporary / "clean"
        subprocess.run([sys.executable, str(ROOT / "scripts/package_resources.py"), "assemble", "--output", str(cls.clean),
                        "--source", "0" * 40], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temporary)

    def copy(self, name):
        target = self.temporary / name
        shutil.copytree(self.clean, target)
        self.addCleanup(shutil.rmtree, target)
        return target

    def problems(self, path, source=None):
        return audit_builder.audit(path, source)["problems"]

    def test_a_clean_package_passes(self):
        report = audit_builder.audit(self.clean, "0" * 40)
        self.assertTrue(report["ok"], report["problems"])
        self.assertIn("source", " ".join(self.problems(self.clean, "1" * 40)))

    def test_personal_and_purchased_files_are_rejected(self):
        for relative, content in (("game/dev_hdd0/game/NPUB30154/USRDIR/EBOOT.elf", b"\x7fELF"),
                                  ("extra/tank-battles_NPUB30154.pkg", b"pkg"),
                                  ("extra/UP2006-KEY.edat", b"edat"),
                                  ("online/me@rpcn.example.org_31313.account", b"password=x"),
                                  ("extra/savedata/AUTOSAVE/DATA", b"save"),
                                  ("extra/server.txt", b"-----BEGIN PRIVATE KEY-----\nabc\n")):
            with self.subTest(relative):
                root = self.copy("personal-" + str(abs(hash(relative))))
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_bytes(content)
                self.assertTrue(self.problems(root), relative)

    def test_title_code_and_stray_executables_are_rejected(self):
        root = self.copy("title")
        body = "".join(f"void func_00{i:06X}(ppu_context* ctx) {{}}\n" for i in range(200))
        (root / "scripts/lifted.cpp.txt").write_text(body)
        (root / "scripts/player").write_bytes(b"\x7fELF" + b"\0" * 60)
        (root / "external/ppu_recomp_003.cpp").write_text("x")
        problems = " ".join(self.problems(root))
        self.assertIn("generated title code: scripts/lifted.cpp.txt", problems)
        self.assertIn("executable outside", problems)
        self.assertIn("ppu_recomp_003", problems)

    def test_a_modified_resource_fails_verification(self):
        root = self.copy("modified")
        script = root / "scripts/port.py"
        script.write_text(script.read_text() + "\n# changed\n")
        self.assertIn("verification failed", " ".join(self.problems(root)))

    def test_builder_exe_payload_tampering_and_unsafe_paths(self):
        stub = self.temporary / "stub.exe"
        stub.write_bytes(b"MZ" + b"\0" * 126)
        exe = self.temporary / "Builder.exe"
        package_builder.exe(types.SimpleNamespace(resources=self.clean, stub=stub, output=exe))
        self.assertTrue(audit_builder.audit(exe, "0" * 40)["ok"])
        damaged = bytearray(exe.read_bytes())
        damaged[200] ^= 1
        bad = self.temporary / "damaged.exe"
        bad.write_bytes(bytes(damaged))
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            audit_builder.audit(bad)
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("../escape.txt", "x")
        import hashlib
        unsafe = self.temporary / "unsafe.exe"
        data = payload.getvalue()
        unsafe.write_bytes(b"MZ" + struct.pack("<QQ", 2, len(data))[:0] + data +
                           struct.pack("<QQ", 2, len(data)) + hashlib.sha256(data).digest() + package_builder.MAGIC)
        with self.assertRaisesRegex(ValueError, "Unsafe path"):
            audit_builder.audit(unsafe)


if __name__ == "__main__":
    unittest.main()
