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
from unittest.mock import Mock, patch
import zipfile

import builder_session
import package_builder
import package_resources


class LauncherSourceTests(unittest.TestCase):
    def test_installed_launcher_has_shared_settings_and_smoke_sources(self):
        resources = set(package_resources.resource_files())
        self.assertTrue({"player_settings.cpp", "player_settings.h", "launcher/display_smoke.h",
                         "launcher/ui.cpp", "cmake/PlayerLauncher.cmake", "scripts/verify_builder.py"} <= resources)


class PayloadTimestampTests(unittest.TestCase):
    def test_future_dated_build_inputs_are_packed_with_past_timestamps(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = root / "resources"
            resources.mkdir()
            source = resources / "CMakeLists.txt"
            source.write_bytes(b"project(probe)\n")
            source.chmod(0o755)
            os.utime(source, (2524608000, 2524608000))  # 2050: a packaging clock ahead of the user.
            stub = root / "stub.exe"
            stub.write_bytes(b"MZ")
            output = root / "Builder.exe"
            package_builder.exe(types.SimpleNamespace(resources=resources, stub=stub, output=output))
            offset, size, _ = package_builder.read_exe_payload(output)
            with zipfile.ZipFile(io.BytesIO(output.read_bytes()[offset:offset + size])) as archive:
                info = archive.getinfo("CMakeLists.txt")
                self.assertEqual(info.date_time, (2000, 1, 1, 0, 0, 0))
                self.assertEqual(archive.read(info), source.read_bytes())
                self.assertEqual(info.external_attr >> 16, source.stat().st_mode & 0xFFFF)


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
        # Only the POSIX AppRun test executes this interpreter. Windows
        # payload tests must not require administrator symlink privilege.
        if os.name != "nt":
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
        if os.name != "nt":
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
    def test_gui_python_starts_the_console_interpreter_for_setup(self):
        pythonw = Path("/builder with spaces/toolchain/python/pythonw.exe")
        with patch.object(builder_session.sys, "executable", str(pythonw)):
            command = builder_session.engine_command({})
        self.assertEqual(command[0], str(pythonw.with_name("python.exe")))

    def test_shared_session_finishes_and_remembers_success(self):
        with tempfile.TemporaryDirectory() as directory:
            pkg = Path(directory) / "game.pkg"
            pkg.touch()
            output = Path(directory) / "player"
            events = iter([{"event": "result", "ok": True}, {"event": "exit", "status": 0}])
            engine = Mock()
            engine.events.get_nowait.side_effect = events
            with patch.object(builder_session, "load_settings", return_value={}), \
                 patch.object(builder_session, "save_settings") as save, \
                 patch.object(builder_session, "Engine", return_value=engine):
                session = builder_session.BuilderSession()
                self.assertFalse(session.start({"output": str(output)}))
                self.assertIn("Choose your purchased", session.message)
                self.assertTrue(session.start({"pkg": str(pkg), "output": str(output)}))
                self.assertTrue(session.poll())
                self.assertIsNone(session.engine)
                self.assertTrue(session.success)
                self.assertEqual(session.output, output)
                self.assertTrue(save.call_args.args[0]["previous_build"])

    def test_engine_command_passes_paths_only(self):
        command = builder_session.engine_command({"pkg": "/p/game.pkg", "unlock": "/p/key.edat", "elf": "/p/EBOOT.elf",
                                              "output": "/games/Tank Battles", "jobs": 3, "workspace": ""})
        self.assertIn("--json", command)
        self.assertIn("--stdin-control", command)
        self.assertEqual(command[command.index("--pkg") + 1], "/p/game.pkg")
        self.assertEqual(command[command.index("--output") + 1], "/games/Tank Battles")
        self.assertEqual(command[command.index("--jobs") + 1], "3")
        self.assertNotIn("--workspace", command)

    def test_missing_files_are_named_before_starting(self):
        problems = builder_session.check_values({"pkg": "/nonexistent/game.pkg", "elf": ""})
        self.assertTrue(any("Game package not found" in problem for problem in problems))
        self.assertEqual(builder_session.check_values({"previous_build": True}), [])
        self.assertTrue(builder_session.check_values({}))

    def test_events_drive_stages_progress_and_problems(self):
        model = builder_session.Model()
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

    def test_log_updates_append_and_drop_only_expired_lines(self):
        model = builder_session.Model()
        limit = builder_session.LOG_LINES
        for index in range(limit):
            model.apply({"event": "log", "line": str(index)})
        self.assertEqual(model.log_update(limit), (0, []))

        for index in range(limit, limit + 2):
            model.apply({"event": "log", "line": str(index)})
        self.assertEqual(model.log_update(limit), (2, [str(limit), str(limit + 1)]))
        self.assertEqual(model.log_update(0), (0, [str(index) for index in range(2, limit + 2)]))
        self.assertEqual(model.log_update(1), (1, model.lines))


if __name__ == "__main__":
    unittest.main()
