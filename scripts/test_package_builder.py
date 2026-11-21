"""Builder packaging: notices, icon, AppDir layout and AppRun, Builder.exe
payload round trip and tamper detection, pinned AppImage runtime, and the
builder window's engine command and event model."""
import hashlib
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

import builder_app
import package_builder


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = Path(tempfile.mkdtemp(prefix="tb builder package "))
        self.addCleanup(shutil.rmtree, self.temporary)
        self.resources = self.temporary / "resources"
        (self.resources / "scripts").mkdir(parents=True)
        (self.resources / "scripts/builder_app.py").write_text("print('builder', __import__('sys').argv[1:])\n")
        (self.resources / "package.json").write_text("{}\n")
        python = self.resources / "toolchain/python/bin/python3"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)

    def test_notices_name_every_component_and_the_lgpl_source(self):
        windows, linux = package_builder.notices("windows"), package_builder.notices("linux")
        for name in ("zig", "cmake", "ninja", "python", "openssl", "curl", "sdl2"):
            self.assertIn(name, windows)
        self.assertIn("libiconv", windows)
        self.assertIn("relink", windows)
        self.assertNotIn("libiconv", linux)
        self.assertIn("contains no game content", linux)

    def test_the_icon_is_a_valid_png(self):
        data = package_builder.png(32)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", data[16:24]), (32, 32))

    @unittest.skipIf(os.name == "nt", "POSIX AppRun")
    def test_the_appdir_runs_the_builder_with_the_staged_python(self):
        appdir = self.temporary / "App Dir ü"
        package_builder.main.__globals__["sys"].argv = ["package_builder.py", "appdir", "--resources", str(self.resources),
                                                        "--output", str(appdir)]
        package_builder.main()
        for name in ("AppRun", "tank-battles-builder.desktop", "tank-battles-builder.png", ".DirIcon"):
            self.assertTrue((appdir / name).is_file(), name)
        self.assertIn("Exec=AppRun", (appdir / "tank-battles-builder.desktop").read_text())
        result = subprocess.run([str(appdir / "AppRun"), "--self-test"], capture_output=True, text=True,
                                env={"PATH": "/nonexistent"}, cwd="/")
        self.assertEqual(result.stdout.strip(), "builder ['--self-test']")

    def test_builder_exe_payload_round_trips_and_detects_tampering(self):
        stub = self.temporary / "stub.exe"
        stub.write_bytes(b"MZ" + b"\0" * 510)
        output = self.temporary / "Builder.exe"
        package_builder.exe(types.SimpleNamespace(resources=self.resources, stub=stub, output=output))
        offset, size, digest = package_builder.read_exe_payload(output)
        self.assertEqual(offset, 512)
        payload = output.read_bytes()[offset:offset + size]
        self.assertEqual(hashlib.sha256(payload).digest(), digest)
        names = zipfile.ZipFile(io.BytesIO(payload)).namelist()
        self.assertIn("scripts/builder_app.py", names)
        self.assertIn("package.json", names)
        self.assertFalse(any(name.startswith("toolchain/python/bin") for name in names), "symlinks are not packed")
        damaged = bytearray(output.read_bytes())
        damaged[offset + 10] ^= 0xFF
        self.assertNotEqual(hashlib.sha256(bytes(damaged[offset:offset + size])).digest(), digest)
        truncated = self.temporary / "truncated.exe"
        truncated.write_bytes(output.read_bytes()[:-10])
        with self.assertRaises(ValueError):
            package_builder.read_exe_payload(truncated)

    def test_the_appimage_runtime_must_match_its_pin(self):
        wrong = self.temporary / "runtime"
        wrong.write_bytes(b"not the runtime")
        args = types.SimpleNamespace(appdir=str(self.temporary), runtime=str(wrong), output=str(self.temporary / "x.AppImage"))
        if not shutil.which("mksquashfs"):
            self.skipTest("mksquashfs not installed")
        with self.assertRaisesRegex(SystemExit, "pinned digest"):
            package_builder.appimage(args)


class BuilderWindowLogicTests(unittest.TestCase):
    def test_engine_command_passes_paths_only(self):
        command = builder_app.engine_command({"pkg": "/p/game.pkg", "unlock": "/p/key.edat", "elf": "/p/EBOOT.elf",
                                              "output": "/games/Tank Battles", "jobs": 3, "workspace": ""})
        self.assertIn("--json", command)
        self.assertIn("--stdin-control", command)
        self.assertEqual(command[command.index("--pkg") + 1], "/p/game.pkg")
        self.assertEqual(command[command.index("--output") + 1], "/games/Tank Battles")
        self.assertEqual(command[command.index("--jobs") + 1], "3")
        self.assertNotIn("--workspace", command)

    def test_missing_files_are_named_before_starting(self):
        problems = builder_app.check_values({"pkg": "/nonexistent/game.pkg", "elf": ""})
        self.assertTrue(any("Game package not found" in problem for problem in problems))
        self.assertEqual(builder_app.check_values({"previous_build": True}), [])
        self.assertTrue(builder_app.check_values({}))

    def test_events_drive_stages_progress_and_problems(self):
        model = builder_app.Model()
        for event in ({"event": "stage", "stage": "inputs", "state": "started"},
                      {"event": "log", "line": "[error] EBOOT.elf: /x/EBOOT.elf"},
                      {"event": "log", "line": "  Supply your decrypted EBOOT.elf."},
                      {"event": "stage", "stage": "inputs", "state": "failed"},
                      {"event": "progress", "stage": "compile", "done": 3, "total": 9},
                      {"event": "result", "ok": False, "error": "Missing", "log": "/w/logs/setup.log"}):
            model.apply(event)
        self.assertEqual(model.stages["inputs"], "failed")
        self.assertEqual(model.progress, (3, 9))
        self.assertEqual(model.problems(), ["EBOOT.elf: /x/EBOOT.elf: Supply your decrypted EBOOT.elf."])
        self.assertEqual(model.log_path, "/w/logs/setup.log")


if __name__ == "__main__":
    unittest.main()
