"""Movable player folder: staged updates that keep user state, refusal of
foreign folders, non-destructive migration, and the native launcher's
folder-relative environment, offline default and profile selection."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import player_folder

ROOT = Path(__file__).resolve().parents[1]


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = Path(tempfile.mkdtemp(prefix="tb player folder "))
        self.addCleanup(shutil.rmtree, self.temporary)
        source = self.temporary / "workspace"
        self.title = source / "game/dev_hdd0/game/NPUB30154"
        self.exdata = source / "game/dev_hdd0/home/00000001/exdata"
        write(self.title / "PARAM.SFO", "sfo")
        write(self.title / "USRDIR/EBOOT.elf", "elf v1")
        write(self.title / "USRDIR/assets.edat", "assets")
        write(self.title / "USRDIR/assets.edat.dec", "decrypted cache")
        write(self.title / "USRDIR/assets.edat.dec.lock", "")
        write(self.exdata / "UP2006-NPUB30154_00-KEY.edat", "unlock")
        write(self.exdata / "OTHER-GAME.edat", "not ours")
        build = source / "build"
        self.player = write(build / player_folder.executable_name("TankBattlesRecomp"), "player v1")
        write(build / player_folder.executable_name("PlayTankBattles"), "launcher")
        self.build = build
        self.output = self.temporary / "Tank Battles ü"

    def sources(self):
        files = player_folder.build_outputs(self.build, False)
        files.update(player_folder.content_files(self.title, self.exdata, "NPUB30154"))
        return files

    def test_contents_exclude_caches_and_other_titles(self):
        names = set(self.sources())
        self.assertIn("game/dev_hdd0/game/NPUB30154/USRDIR/EBOOT.elf", names)
        self.assertIn("game/dev_hdd0/home/00000001/exdata/UP2006-NPUB30154_00-KEY.edat", names)
        self.assertIn(player_folder.launcher_name(), names)
        self.assertFalse(any(".dec" in name or "OTHER-GAME" in name for name in names))

    def test_an_update_replaces_only_what_it_owns(self):
        player_folder.assemble(self.output, self.sources(), {"source_commit": "a"}, log=lambda *_: None)
        save = write(self.output / "game/dev_hdd0/home/00000001/savedata/AUTOSAVE/DATA", "progress")
        settings = write(self.output / "game/settings.ini", "fxaa=1\n")
        profile = write(self.output / "online/active", "me.profile\n")
        cache = write(self.output / "game/dev_hdd0/game/NPUB30154/USRDIR/assets.edat.dec", "cache")
        stale = write(self.title / "USRDIR/removed.edat", "gone next build")
        player_folder.assemble(self.output, self.sources(), {"source_commit": "b"}, log=lambda *_: None)
        self.assertTrue((self.output / "game/dev_hdd0/game/NPUB30154/USRDIR/removed.edat").is_file())

        stale.unlink()
        self.player.write_text("player v2")
        manifest = player_folder.assemble(self.output, self.sources(), {"source_commit": "c"}, log=lambda *_: None)
        self.assertEqual((self.output / "player" / self.player.name).read_text(), "player v2")
        self.assertFalse((self.output / "game/dev_hdd0/game/NPUB30154/USRDIR/removed.edat").exists())
        for kept, text in ((save, "progress"), (settings, "fxaa=1\n"), (profile, "me.profile\n"), (cache, "cache")):
            self.assertEqual(kept.read_text(), text)
        self.assertEqual(manifest["source_commit"], "c")
        self.assertEqual(json.loads((self.output / player_folder.MANIFEST).read_text())["files"], manifest["files"])
        self.assertFalse(list(self.output.glob(".staging-*")))

    def test_a_folder_holding_other_files_is_refused(self):
        write(self.output / "notes.txt", "mine")
        with self.assertRaisesRegex(ValueError, "not a Tank Battles player folder"):
            player_folder.assemble(self.output, self.sources(), {}, log=lambda *_: None)
        self.assertEqual(sorted(p.name for p in self.output.iterdir()), ["notes.txt"])

    def test_an_incomplete_import_is_refused(self):
        (self.title / "USRDIR/EBOOT.elf").unlink()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            player_folder.content_files(self.title, self.exdata, "NPUB30154")

    def test_migration_never_overwrites(self):
        old = self.temporary / "old game"
        write(old / "dev_hdd0/home/00000001/savedata/AUTOSAVE/DATA", "old progress")
        write(old / "settings.ini", "old settings")
        player_folder.assemble(self.output, self.sources(), {}, log=lambda *_: None)
        write(self.output / "game/settings.ini", "new settings")
        copied = player_folder.migrate(old, self.output, log=lambda *_: None)
        self.assertEqual(copied, ["dev_hdd0/home/00000001/savedata"])
        self.assertEqual((self.output / "game/settings.ini").read_text(), "new settings")
        self.assertEqual((self.output / "game/dev_hdd0/home/00000001/savedata/AUTOSAVE/DATA").read_text(), "old progress")


@unittest.skipIf(os.name == "nt" or not shutil.which("c++"), "builds the POSIX launcher with the host compiler")
class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = Path(tempfile.mkdtemp(prefix="tb launcher "))
        cls.launcher = cls.temporary / "Play Tank Battles"
        subprocess.run(["c++", "-std=c++20", "-O1", "-I", str(ROOT / "launcher"), str(ROOT / "launcher/play_tank_battles.cpp"),
                        "-o", str(cls.launcher)], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temporary)

    def folder(self, name):
        root = self.temporary / name
        root.mkdir()
        shutil.copy2(self.launcher, root / "Play Tank Battles")
        write(root / "game/dev_hdd0/game/NPUB30154/PARAM.SFO", "sfo")
        write(root / "game/dev_hdd0/game/NPUB30154/USRDIR/EBOOT.elf", "elf")
        player = write(root / "player/TankBattlesRecomp",
                       f"#!{sys.executable}\nimport json,os,sys\n"
                       "print(json.dumps({'argv':sys.argv[1:],'cwd':os.getcwd(),"
                       "'env':{k:v for k,v in os.environ.items() if k.startswith(('PS3_','TB_'))}}))\n")
        player.chmod(0o755)
        return root

    def run_launcher(self, root, *arguments):
        result = subprocess.run([str(root / "Play Tank Battles"), *arguments], capture_output=True, text=True,
                                env={"PATH": "/nonexistent"}, cwd="/")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_paths_are_relative_to_the_launcher_and_offline_by_default(self):
        root = self.folder("moved ü folder")
        seen = self.run_launcher(root)
        title = root / "game/dev_hdd0/game/NPUB30154"
        self.assertEqual(seen["argv"], [str(title / "USRDIR/EBOOT.elf")])
        self.assertEqual(seen["cwd"], str(root))
        self.assertEqual(seen["env"]["PS3_VFS_ROOT"], str(title))
        self.assertEqual(seen["env"]["PS3_GAMEDATA_ROOT"], str(root / "game"))
        self.assertEqual(seen["env"]["TB_LOG_DIR"], str(root / "logs"))
        self.assertTrue((root / "game/dev_hdd1").is_dir())

    def test_a_selected_profile_goes_online_unless_offline_is_requested(self):
        root = self.folder("online")
        write(root / "online/me.profile", "endpoint=example.org:31313\n")
        write(root / "online/active", "me.profile\n")
        self.assertEqual(self.run_launcher(root)["argv"][1:], ["--rpcn-profile", str(root / "online/me.profile")])
        self.assertEqual(self.run_launcher(root, "--offline")["argv"][1:], [])
        write(root / "online/active", "../outside.profile\n")
        self.assertEqual(self.run_launcher(root)["argv"][1:], [])

    def test_a_missing_player_is_explained(self):
        root = self.folder("broken")
        (root / "player/TankBattlesRecomp").unlink()
        result = subprocess.run([str(root / "Play Tank Battles")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Build it again", result.stderr)


if __name__ == "__main__":
    unittest.main()
