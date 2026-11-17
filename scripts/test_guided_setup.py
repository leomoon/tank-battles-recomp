"""Game-free acceptance of inspection, import rollback and resumable setup."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import setup_game as game
import setup_inputs as inputs
import setup_pipeline as pipeline
from synthetic_content import write_sfo
from test_setup_game import encrypted_fixture


class GuidedSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "original"
        (self.source / "USRDIR").mkdir(parents=True)
        self.self_data, self.elf, keys = encrypted_fixture()
        write_sfo(self.source / "PARAM.SFO", title_id="NPUB30154")
        (self.source / "USRDIR/EBOOT.BIN").write_bytes(self.self_data)
        for name in inputs.HASHES:
            if name.endswith(".edat"):
                (self.source / name).write_bytes(b"NPD\0synthetic-" + name.encode())
        self.hashes = {name: inputs.digest(self.source / name) for name in inputs.HASHES}
        self.addCleanup(patch.stopall)
        patch.dict(inputs.HASHES, self.hashes).start()
        patch.object(game, "ELF_SHA256", {hashlib.sha256(self.elf).hexdigest()}).start()
        self.keyfile = self.root / "keys"
        self.keyfile.write_text(keys)
        self.config = dict(game=dict(elf="game/dev_hdd0/game/NPUB30154/USRDIR/EBOOT.elf"),
                           paths=dict(gamedata_root="game", vfs_root="game/dev_hdd0/game/NPUB30154",
                                      hdd0_root="game/dev_hdd0", sdk="sdk", recompiled="recompiled"),
                           recompiler=dict(chunk_lines=1000))
        self.args = SimpleNamespace(pkg=None, game_dir=str(self.source), keys=None, rpcs3_source=None,
                                    rpcs3=None, elf=None, unlock=None, rap=None, allow_unknown_version=False,
                                    restart=False)
        self.installed = self.resolve(self.config["paths"]["vfs_root"])
        self.capture = io.StringIO()
        redirect = redirect_stdout(self.capture)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def resolve(self, value):
        return (self.root / value).resolve()

    def check(self):
        return game.check_game(self.args, self.config, self.root, self.resolve)

    def install(self):
        return game.install_game(self.args, self.config, self.root, self.resolve)

    def test_empty_directory_reports_every_file_and_action_without_writes(self):
        self.args.game_dir = None
        checks = self.check()
        self.assertEqual(sum(c["status"] == "error" for c in checks), len(inputs.HASHES) + 1)
        self.assertTrue(all(c["action"] for c in checks if c["status"] != "ok"))
        self.assertFalse((self.root / "game").exists())
        for name in (*inputs.HASHES, "EBOOT.elf", "unlock", "rap"):
            self.assertIn(name, self.capture.getvalue())

    def test_every_missing_and_mismatched_input(self):
        for name in self.hashes:
            with self.subTest(name=name):
                path = self.source / name
                original = path.read_bytes()
                path.unlink()
                self.assertEqual(next(c for c in inputs.inspect_title(self.source) if c["name"] == name)["status"], "error")
                path.write_bytes(original + b"untested-version")
                checks = inputs.inspect_title(self.source)
                self.assertEqual(next(c for c in checks if c["name"] == name)["status"], "error")
                checks = inputs.inspect_title(self.source, allow_unknown=True)
                self.assertEqual(next(c for c in checks if c["name"] == name)["status"], "warning")
                path.write_bytes(original)

    def test_wrong_title_and_bad_container_always_rejected(self):
        write_sfo(self.source / "PARAM.SFO", title_id="NPUB00000")
        self.assertEqual(inputs.inspect_title(self.source, True)[0]["status"], "error")
        (self.source / "USRDIR/assets_common.edat").write_bytes(b"not an EDAT")
        checks = inputs.inspect_title(self.source, True)
        self.assertEqual(next(c for c in checks if c["name"].endswith("assets_common.edat"))["status"], "error")
        (self.source / "USRDIR/EBOOT.BIN").write_bytes(b"SCE\0" + bytes(124))
        checks = inputs.inspect_title(self.source, True)
        self.assertEqual(next(c for c in checks if c["name"].endswith("EBOOT.BIN"))["status"], "error")

    def test_elf_checks_cannot_be_bypassed_with_unknown_flag(self):
        path = self.root / "EBOOT.elf"
        for data in (self.self_data, b"\x7fELF", self.elf[:100], self.elf[:18] + b"\x00\x3e" + self.elf[20:]):
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                inputs.validate_elf(path, allow_unknown=True)
        path.write_bytes(self.elf)
        with self.assertRaisesRegex(ValueError, "Untested"):
            inputs.validate_elf(path)
        inputs.validate_elf(path, allow_unknown=True)
        self.assertIn("compatibility is not established", self.capture.getvalue())

    def test_manual_elf_import_preserves_originals_and_can_resume_without_keys(self):
        manual = self.source / "USRDIR/EBOOT.elf"
        manual.write_bytes(self.elf)
        before = {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        output = self.install()
        self.assertEqual(output.read_bytes(), self.elf)
        self.assertEqual(before, {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob("*") if p.is_file()})
        self.args.game_dir = None
        self.assertEqual(self.install(), output)
        self.assertEqual(json.loads((self.root / "game/setup.json").read_text())["input_sha256"], self.hashes)

    def test_explicit_manual_elf_path(self):
        manual = self.root / "manual.elf"
        manual.write_bytes(self.elf)
        self.args.elf = str(manual)
        self.assertEqual(self.install().read_bytes(), self.elf)

    def test_invalid_cached_elf_is_repaired_only_with_a_provider(self):
        (self.source / "USRDIR/EBOOT.elf").write_bytes(b"interrupted decryption")
        self.assertTrue(any(c["name"] == "EBOOT.elf" and c["status"] == "error" for c in self.check()))
        self.args.keys = str(self.keyfile)
        self.assertTrue(any(c["name"] == "EBOOT.elf" and c["status"] == "ready" for c in self.check()))
        self.assertEqual(self.install().read_bytes(), self.elf)
        self.assertEqual((self.source / "USRDIR/EBOOT.elf").read_bytes(), b"interrupted decryption")

    def test_wrong_elf_or_unlock_leaves_installed_game_untouched(self):
        self.args.keys = str(self.keyfile)
        self.install()
        before = (self.installed / "USRDIR/EBOOT.elf").read_bytes()
        bad = self.root / "manual.elf"
        bad.write_bytes(self.self_data)
        self.args.elf = str(bad)
        with self.assertRaisesRegex(ValueError, "decrypted ELF"):
            self.install()
        self.assertEqual((self.installed / "USRDIR/EBOOT.elf").read_bytes(), before)
        self.args.elf = None
        self.args.unlock = str(bad)
        with self.assertRaisesRegex(ValueError, "Unsupported unlock"):
            self.install()
        self.assertEqual((self.installed / "USRDIR/EBOOT.elf").read_bytes(), before)

    def test_unlock_rap_rules_and_ambiguous_candidates(self):
        self.args.keys = str(self.keyfile)
        local = self.root / "game/input"
        local.mkdir(parents=True)
        unlock = bytearray(352)
        unlock[:16] = b"NPD\0" + struct.pack(">III", 1, 2, 1)
        unlock[16:16 + len(game.CONTENT_ID)] = game.CONTENT_ID.encode()
        self.args.unlock = str(local / "purchased.edat")
        Path(self.args.unlock).write_bytes(unlock)
        self.assertIn("license-2 RAP", [c["name"] for c in self.check() if c["status"] == "error"])
        self.args.rap = str(local / "purchased.rap")
        Path(self.args.rap).write_bytes(b"bad")
        self.assertTrue(any("exactly 16" in c["action"] for c in self.check()))
        Path(self.args.rap).write_bytes(bytes(16))
        self.assertFalse(any(c["status"] == "error" for c in self.check()))

        self.install()
        self.assertEqual((self.root / "game/dev_hdd0/home/00000001/exdata" / (game.CONTENT_ID + ".rap")).stat().st_mode & 0o777, 0o600)
        self.args.rap = None
        self.args.unlock = None
        self.args.game_dir = None
        self.assertFalse(any(c["status"] == "error" for c in self.check()))
        # License 3 never needs a RAP.
        unlock[8:12] = struct.pack(">I", 3)
        self.args.unlock = str(local / "purchased.edat")
        Path(self.args.unlock).write_bytes(unlock)
        self.assertFalse(any(c["status"] == "error" for c in self.check()))


    def test_missing_explicit_providers_and_ambiguous_unlocks(self):
        self.args.elf = str(self.root / "missing.elf")
        self.args.keys = str(self.root / "missing.keys")
        checks = self.check()
        self.assertIn("EBOOT.elf", [c["name"] for c in checks if c["status"] == "error"])
        self.assertIn("keys", [c["name"] for c in checks if c["status"] == "error"])
        local = self.root / "game/input"
        local.mkdir(parents=True)
        (local / "one.edat").touch()
        (local / "two.edat").touch()
        self.assertTrue(any("Multiple candidates" in c["action"] for c in self.check()))

    def test_rpcs3_cli_runs_on_a_copy_and_never_leaks_output(self):
        fake = self.root / "rpcs3"
        fake.write_text(f'#!{sys.executable}\nimport pathlib,sys\n'
                        'assert sys.argv[1:3] == ["--headless", "--decrypt"]\n'
                        'source = pathlib.Path(sys.argv[3])\n'
                        f'assert source.parent != pathlib.Path({str(self.source / "USRDIR")!r})\n'
                        f'source.with_suffix(".elf").write_bytes({self.elf!r})\n'
                        'print("secret fixture output must not be forwarded")\n')
        fake.chmod(0o755)
        self.args.rpcs3 = str(fake)
        self.assertEqual(self.install().read_bytes(), self.elf)
        self.assertFalse((self.source / "USRDIR/EBOOT.elf").exists())
        self.assertNotIn("secret fixture", self.capture.getvalue())
        fake.write_text(f'#!{sys.executable}\nraise SystemExit(1)\n')
        with self.assertRaisesRegex(ValueError, "did not produce"):
            game.decrypt_with_rpcs3(self.source / "USRDIR/EBOOT.BIN", fake)


class ResumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = dict(paths=dict(recompiled="recompiled", gamedata_root="game", sdk="sdk", build="build"), recompiler=dict(chunk_lines=1000))
        (self.root / "EBOOT.elf").write_bytes(b"synthetic signature input")
        self.args = SimpleNamespace(elf=str(self.root / "EBOOT.elf"), restart=False)
        self.calls = []
        self.fail = None

    def resolve(self, value):
        return (self.root / value).resolve()

    def prepare(self, args, config):
        self.calls.append("prepare")
        directory = self.resolve(config["paths"]["recompiled"])
        directory.mkdir(parents=True)
        (directory / "ppu_recomp.h").write_text("synthetic header")
        if self.fail == "prepare":
            raise ValueError("interrupted translation")
        (directory / "ppu_recomp_000.cpp").write_text("synthetic source")

    def action(self, name):
        def run(args, config):
            self.calls.append(name)
            if self.fail == name:
                raise ValueError("fixture failure")
            if name == "configure":
                directory = self.root / "build"
                directory.mkdir(exist_ok=True)
                (directory / "CMakeCache.txt").write_text("synthetic CMake cache")
                (directory / "build.ninja").write_text("synthetic Ninja build")
            if name == "build" and not (self.root / "build/TankBattlesRecomp").is_file():
                (self.root / "build/TankBattlesRecomp").write_text("synthetic executable")
        return run

    def run_setup(self):
        with redirect_stdout(io.StringIO()):
            pipeline.run_pipeline(self.args, self.config, self.resolve, self.prepare, self.action("configure"), self.action("build"))

    def test_resume_and_dependency_and_output_invalidation(self):
        self.run_setup()
        self.assertEqual(self.calls, ["prepare", "configure", "build"])
        self.calls.clear()
        self.run_setup()
        self.assertEqual(self.calls, ["build"])
        for mutation in (
                         lambda: (self.root / "recompiled/ppu_recomp_000.cpp").write_text("tampered"),
                         lambda: (self.root / "EBOOT.elf").write_bytes(b"changed input"),
                         lambda: (self.root / "game/setup-state.json").write_text("interrupted json")):
            self.calls.clear()
            mutation()
            self.run_setup()
            self.assertEqual(self.calls[0], "prepare")
            self.assertEqual(self.calls[-1], "build")
        self.args.restart = True
        self.calls.clear()
        self.run_setup()
        self.assertEqual(self.calls[0], "prepare")

    def test_prepare_failure_keeps_previous_output_and_build_failure_resumes(self):
        self.run_setup()
        self.args.restart = True
        self.fail = "prepare"
        with self.assertRaisesRegex(ValueError, "Setup prepare failed"):
            self.run_setup()
        self.assertEqual((self.root / "recompiled/ppu_recomp_000.cpp").read_text(), "synthetic source")
        self.args.restart = False
        self.fail = "build"
        with self.assertRaisesRegex(ValueError, "completed work will be reused"):
            self.run_setup()
        self.fail = None
        self.calls.clear()
        self.run_setup()
        self.assertEqual(self.calls, ["build"])

    def test_configuration_changes_and_failed_configure_are_retried(self):
        self.run_setup()
        self.args.backend = "vulkan"
        self.calls.clear()
        self.fail = "configure"
        with self.assertRaisesRegex(ValueError, "Setup configure failed"):
            self.run_setup()
        self.assertEqual(self.calls, ["configure"])
        self.calls.clear()
        self.fail = None
        self.run_setup()
        self.assertEqual(self.calls, ["configure", "build"])
        (self.root / "build/build.ninja").write_text("tampered build rules")
        self.calls.clear()
        self.run_setup()
        self.assertEqual(self.calls, ["configure", "build"])

    def test_setup_lock_covers_import_and_build(self):
        with pipeline.setup_lock(self.root / "game"):
            with self.assertRaisesRegex(ValueError, "Another setup"):
                with pipeline.setup_lock(self.root / "game"):
                    self.fail("second setup acquired the lock")
        with pipeline.setup_lock(self.root / "game"):
            pass

    def test_changed_executable_is_relinked(self):
        self.run_setup()
        executable = self.root / "build/TankBattlesRecomp"
        executable.write_text("damaged output")
        self.calls.clear()
        self.run_setup()
        self.assertEqual(executable.read_text(), "synthetic executable")
        self.assertEqual(self.calls, ["build"])


if __name__ == "__main__":
    unittest.main()
