"""Movable player folder: staged updates that keep user state, refusal of
foreign folders, non-destructive migration; and the native launcher: folder-
relative environment, accounts (sign-in, remember, tokens, creation, errors,
per-server records), private temporary profiles and its window."""
import json
import os
from pathlib import Path
import shutil
import struct
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


FAKE_PLAYER = r"""
import json, os, stat, sys
args = sys.argv[1:]
if args and args[0] in ("--rpcn-check", "--rpcn-create-account", "--rpcn-request-reset", "--rpcn-reset-password"):
    path = args[1]
    profile = dict(line.split("=", 1) for line in open(path).read().splitlines())
    mode = stat.S_IMODE(os.stat(path).st_mode)
    log = os.path.join(os.path.dirname(os.path.realpath(sys.argv[0])), "calls.jsonl")
    with open(log, "a") as stream:
        stream.write(json.dumps({"args": args, "profile": profile, "mode": mode}) + "\n")
    if args[0] == "--rpcn-request-reset":
        print("[rpcn-result] reset_sent")
    elif args[0] == "--rpcn-reset-password":
        print("[rpcn-result] password_reset" if profile.get("reset_code") == "reset-code" else "[rpcn-result] reset_code_rejected")
    elif profile["password"] != os.environ.get("TB_TEST_PLAYER_PASSWORD", "secret pw"):
        print("[rpcn-result] wrong_password")
    elif args[0] == "--rpcn-create-account":
        print("[rpcn-result] created" if "email" in profile else "[rpcn-result] invalid_profile")
    elif profile["npid"] == "NeedsToken" and profile.get("token") != "T123":
        print("[rpcn-result] token_required")
    else:
        print("[rpcn-result] signed_in")
    sys.exit(0)
seen = {"argv": args, "cwd": os.getcwd(), "env": {k: v for k, v in os.environ.items() if k.startswith(("PS3_", "TB_"))}}
settings = os.environ.get("TB_SETTINGS_FILE")
if settings and os.path.isfile(settings):
    with open(settings) as stream:
        seen["settings"] = {key.strip(): int(value) for line in stream if "=" in line
                            for key, value in [line.split("=", 1)]}
if "--rpcn-profile" in args:
    path = args[args.index("--rpcn-profile") + 1]
    seen["profile"] = dict(line.split("=", 1) for line in open(path).read().splitlines())
    seen["profile_mode"] = stat.S_IMODE(os.stat(path).st_mode)
print(json.dumps(seen))
"""


def sdl_flags():
    if not shutil.which("pkg-config"):
        return None
    result = subprocess.run(["pkg-config", "--cflags", "--libs", "sdl2"], capture_output=True, text=True)
    return result.stdout.split() if result.returncode == 0 else None


@unittest.skipIf(os.name == "nt" or not shutil.which("c++") or not sdl_flags(), "builds the POSIX launcher with the host compiler and SDL2")
class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # ld.so splits LD_PRELOAD on spaces. Player folders can still contain them.
        cls.temporary = Path(tempfile.mkdtemp(prefix="tb-launcher-", dir="/tmp"))
        cls.launcher = cls.temporary / "Play Tank Battles"
        cls.secret_library = cls.temporary / "secret-library"
        cls.input_mock = cls.temporary / "launcher-input.so"
        cls.secret_library.mkdir()
        subprocess.run(["cc", "-shared", "-fPIC", str(ROOT / "scripts/launcher_secret_mock.c"),
                        "-o", str(cls.secret_library / "libsecret-1.so.0")], check=True)
        subprocess.run(["cc", "-shared", "-fPIC", str(ROOT / "scripts/launcher_input_mock.c"),
                        "-o", str(cls.input_mock), *sdl_flags(), "-ldl"], check=True)
        sources = [str(ROOT / "launcher" / name) for name in ("play_tank_battles.cpp", "accounts.cpp", "platform.cpp", "ui.cpp")]
        sources.append(str(ROOT / "player_settings.cpp"))
        subprocess.run(["c++", "-std=c++20", "-O1", "-I", str(ROOT / "launcher"), *sources, "-o", str(cls.launcher),
                        *sdl_flags(), "-pthread", "-ldl"], check=True)

    def setUp(self):
        self.expected_password = "secret pw"
        self.secret_store_fails = False

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temporary)

    def folder(self, name):
        root = self.temporary / name
        root.mkdir()
        shutil.copy2(self.launcher, root / "Play Tank Battles")
        write(root / "game/dev_hdd0/game/NPUB30154/PARAM.SFO", "sfo")
        write(root / "game/dev_hdd0/game/NPUB30154/USRDIR/EBOOT.elf", "elf")
        player = write(root / "player/TankBattlesRecomp", f"#!{sys.executable}\n" + FAKE_PLAYER)
        player.chmod(0o755)
        self.runtime = self.temporary / (name + " runtime")
        self.runtime.mkdir(mode=0o700)
        return root

    def run_launcher(self, root, *arguments, stdin=None, check=True, runtime_dir=True, window_input=None):
        secret_dir = root / "test-keyring"
        secret_dir.mkdir(exist_ok=True)
        temp_dir = root / "system-temp"
        temp_dir.mkdir(exist_ok=True)
        environment = {"PATH": "/nonexistent", "TMPDIR": str(temp_dir),
                       "LD_LIBRARY_PATH": str(self.secret_library), "TB_TEST_SECRET_DIR": str(secret_dir),
                       "TB_TEST_PLAYER_PASSWORD": self.expected_password}
        if runtime_dir:
            environment["XDG_RUNTIME_DIR"] = str(self.runtime)
        if self.secret_store_fails:
            environment["TB_TEST_SECRET_FAIL"] = "1"
        if window_input:
            environment.update(LD_PRELOAD=str(self.input_mock), SDL_VIDEODRIVER="dummy",
                               TB_TEST_LAUNCHER_INPUT="idle " + window_input)
        result = subprocess.run([str(root / "Play Tank Battles"), *arguments], capture_output=True, text=True,
                                input=stdin, cwd="/", env=environment, timeout=25)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def account(self, root, *arguments, password="secret pw"):
        result = self.run_launcher(root, "--account", *arguments, stdin=password + "\n", check=False)
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def calls(self, root):
        path = root / "player/calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_paths_are_relative_to_the_launcher_and_offline_is_explicit(self):
        root = self.folder("moved ü folder")
        seen = json.loads(self.run_launcher(root, "--offline").stdout)
        title = root / "game/dev_hdd0/game/NPUB30154"
        self.assertEqual(seen["argv"], [str(title / "USRDIR/EBOOT.elf")])
        self.assertEqual(seen["cwd"], str(root))
        self.assertEqual(seen["env"]["PS3_VFS_ROOT"], str(title))
        self.assertEqual(seen["env"]["PS3_GAMEDATA_ROOT"], str(root / "game"))
        self.assertEqual(seen["env"]["TB_LOG_DIR"], str(root / "logs"))
        self.assertEqual(seen["env"]["TB_SETTINGS_FILE"], str(root / "game/settings.ini"))
        self.assertTrue((root / "game/dev_hdd1").is_dir())
        # --play with no account starts offline too.
        self.assertEqual(json.loads(self.run_launcher(root, "--play").stdout)["argv"], [str(title / "USRDIR/EBOOT.elf")])

    def test_sign_in_remember_and_play_online_with_a_private_temporary_profile(self):
        root = self.folder("online")
        result = self.account(root, "sign-in", "--server", "example.org:31313", "--user", "Player1", "--remember")
        self.assertEqual(result["result"], "signed_in")
        call = self.calls(root)[-1]
        self.assertEqual(call["mode"], 0o600)
        self.assertNotIn("secret pw", " ".join(call["args"]), "password on a command line")
        account = root / "online/Player1@example.org_31313.account"
        self.assertEqual(stat_mode(account), 0o600)
        self.assertNotIn("secret pw", account.read_text())
        self.assertIn("remembered=1", account.read_text())
        self.assertEqual(self.run_launcher(root, "--account", "status").stdout.splitlines()[0],
                         "active=Player1@example.org_31313")
        seen = json.loads(self.run_launcher(root, "--play").stdout)
        self.assertEqual(seen["profile"]["password"], "secret pw")
        self.assertEqual(seen["profile_mode"], 0o600)
        self.assertNotIn("secret pw", " ".join(seen["argv"]))
        self.assertEqual(list(self.runtime.rglob("session-*")), [], "temporary profile left behind")
        self.assertEqual(json.loads(self.run_launcher(root, "--offline").stdout)["argv"][1:], [])

    def test_a_password_that_is_not_remembered_is_not_stored(self):
        root = self.folder("session only")
        self.assertEqual(self.account(root, "sign-in", "--server", "example.org:31313", "--user", "Player2")["result"], "signed_in")
        self.assertNotIn("password=", (root / "online/Player2@example.org_31313.account").read_text())
        # Without a remembered password, --play cannot go online and starts offline.
        self.assertEqual(json.loads(self.run_launcher(root, "--play").stdout)["argv"][1:], [])

    def test_runtime_profile_uses_private_system_temp_without_xdg_runtime(self):
        root = self.folder("no runtime dir")
        result = self.run_launcher(root, "--account", "sign-in", "--server", "example.org:31313",
                                   "--user", "Player1", stdin="secret pw\n", runtime_dir=False)
        self.assertIn("result=signed_in", result.stdout)
        profile = Path(self.calls(root)[-1]["args"][1])
        self.assertTrue(profile.is_relative_to(root / "system-temp"))
        self.assertFalse(profile.exists())
        self.assertFalse((root / "online/.session").exists())

    def test_passwords_are_separate_by_server_and_user(self):
        root = self.folder("multiple servers")
        self.assertEqual(self.account(root, "sign-in", "--server", "one.example:31313", "--user", "SameUser", "--remember")["result"], "signed_in")
        self.expected_password = "other secret"
        self.assertEqual(self.account(root, "sign-in", "--server", "two.example:31313", "--user", "SameUser", "--remember",
                                      password="other secret")["result"], "signed_in")
        self.expected_password = "third secret"
        self.assertEqual(self.account(root, "sign-in", "--server", "one.example:31313", "--user", "OtherUser", "--remember",
                                      password="third secret")["result"], "signed_in")
        keyring = root / "test-keyring"
        self.assertEqual(len(list(keyring.iterdir())), 3)
        self.assertEqual(self.calls(root)[-1]["profile"]["password"], "third secret")
        self.expected_password = "secret pw"
        self.account(root, "forget", "--server", "one.example:31313", "--user", "SameUser")
        self.assertEqual(len(list(keyring.iterdir())), 2)
        self.assertIn("saved=SameUser@two.example_31313 remembered=1", self.run_launcher(root, "--account", "status").stdout)

    def test_changed_password_rejects_saved_login_and_allows_new_password(self):
        root = self.folder("changed password")
        self.assertEqual(self.account(root, "sign-in", "--server", "example.org:31313", "--user", "Player1", "--remember")["result"], "signed_in")
        self.expected_password = "new password"
        rejected = self.run_launcher(root, "--play", check=False)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("saved password no longer works", rejected.stderr)
        self.assertNotIn("password=", (root / "online/Player1@example.org_31313.account").read_text())
        self.assertFalse(list((root / "test-keyring").iterdir()))
        self.assertIn("remembered=0", (root / "online/Player1@example.org_31313.account").read_text())
        self.assertEqual(self.account(root, "sign-in", "--server", "example.org:31313", "--user", "Player1", "--remember",
                                      password="new password")["result"], "signed_in")
        self.assertEqual(json.loads(self.run_launcher(root, "--play").stdout)["profile"]["password"], "new password")

    def test_reset_clears_only_that_accounts_password(self):
        root = self.folder("reset password")
        for server in ("one.example:31313", "two.example:31313"):
            self.assertEqual(self.account(root, "sign-in", "--server", server, "--user", "SameUser", "--remember")["result"], "signed_in")
        self.assertEqual(self.account(root, "sign-in", "--server", "one.example:31313", "--user", "SameUser", "--remember",
                                      password="bad")["result"], "wrong_password")
        self.assertEqual(len(list((root / "test-keyring").iterdir())), 2)
        # Reset requests and invalid codes leave both credentials intact.
        self.assertIn("result=reset_sent", self.run_launcher(root, "--account", "request-reset", "--server", "one.example:31313",
                                                                "--user", "SameUser", "--email", "test@example.invalid").stdout)
        self.assertEqual(len(list((root / "test-keyring").iterdir())), 2)
        self.assertEqual(self.account(root, "reset-password", "--server", "one.example:31313", "--user", "SameUser",
                                      "--code", "bad-code", password="new password")["result"], "reset_code_rejected")
        self.assertEqual(len(list((root / "test-keyring").iterdir())), 2)
        self.assertEqual(self.account(root, "reset-password", "--server", "one.example:31313", "--user", "SameUser",
                                      "--code", "reset-code", password="new password")["result"], "password_reset")
        self.assertEqual(len(list((root / "test-keyring").iterdir())), 1)
        self.assertTrue((root / "test-keyring/SameUser@two.example_31313").exists())

    def test_missing_secret_service_keeps_password_for_this_run_only(self):
        root = self.folder("keyring unavailable")
        self.secret_store_fails = True
        result = self.account(root, "sign-in", "--server", "example.org:31313", "--user", "Player1", "--remember")
        self.assertEqual(result["result"], "signed_in")
        self.assertIn("could not store", result["message"])
        self.assertEqual(list((root / "test-keyring").iterdir()), [])
        self.assertNotIn("password=", (root / "online/Player1@example.org_31313.account").read_text())
        self.assertEqual(json.loads(self.run_launcher(root, "--play").stdout)["argv"][1:], [])

    def test_pre_release_plaintext_is_removed_without_reusing_it(self):
        root = self.folder("old account")
        account = root / "online/Player1@example.org_31313.account"
        write(account, "endpoint=example.org:31313\nnpid=Player1\npassword=old secret\n")
        status = self.run_launcher(root, "--account", "status").stdout
        self.assertIn("saved=Player1@example.org_31313 remembered=0", status)
        self.assertNotIn("old secret", account.read_text())
        self.assertEqual(list((root / "test-keyring").iterdir()), [])

    def test_errors_tokens_creation_and_servers(self):
        root = self.folder("errors")
        wrong = self.account(root, "sign-in", "--server", "example.org:31313", "--user", "Player3", password="bad")
        self.assertEqual(wrong["result"], "wrong_password")
        self.assertIn("not correct", wrong["message"])
        self.assertFalse((root / "online/active").exists())
        needs = self.account(root, "sign-in", "--server", "example.org:31313", "--user", "NeedsToken")
        self.assertEqual(needs["result"], "token_required")
        self.assertIn("token", needs["message"])
        done = self.account(root, "sign-in", "--server", "example.org:31313", "--user", "NeedsToken", "--token", "T123")
        self.assertEqual(done["result"], "signed_in")
        created = self.account(root, "create", "--server", "other.example:4000", "--user", "Player4", "--email", "p4@example.invalid")
        self.assertEqual(created["result"], "signed_in")
        self.assertEqual([c["args"][0] for c in self.calls(root)[-2:]], ["--rpcn-create-account", "--rpcn-check"])
        self.assertEqual(self.calls(root)[-2]["profile"]["email"], "p4@example.invalid")
        status = self.run_launcher(root, "--account", "status").stdout
        self.assertIn("saved=NeedsToken@example.org_31313", status)
        self.assertIn("saved=Player4@other.example_4000", status)
        bad = self.account(root, "sign-in", "--server", "no port", "--user", "Player5")
        self.assertEqual(bad["result"], "invalid_input")
        injected = self.account(root, "sign-in", "--server", "example.org:31313", "--user", "Player6", password="pw\nnpid=x")
        self.assertEqual(injected["result"], "wrong_password")   # Only the first line is the password.
        self.assertEqual(self.account(root, "forget", "--server", "other.example:4000", "--user", "Player4")["result"], "forgotten")
        self.assertNotIn("Player4", self.run_launcher(root, "--account", "status").stdout)

    def test_the_window_draws_its_screens(self):
        root = self.folder("window")
        shots = self.temporary / "shots"
        shots.mkdir()
        result = subprocess.run([str(root / "Play Tank Battles")], capture_output=True, text=True,
                                env={"PATH": "/nonexistent", "SDL_VIDEODRIVER": "dummy", "TB_LAUNCHER_UI_SMOKE": str(shots)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(shots.glob("screen-*.bmp"))), 7)
        expected_sizes = [(640, 360), (1440, 900), (800, 900), (1440, 540), (640, 360), (1024, 768), (1024, 768)]
        for index, size in enumerate(expected_sizes):
            bitmap = (shots / f"screen-{index}.bmp").read_bytes()
            width, height = struct.unpack_from("<ii", bitmap, 18)
            self.assertEqual((width, abs(height)), size, f"screen {index} did not resize")
            # Resizing the outer window preserves the panel's readable display scale.
            offset = struct.unpack_from("<I", bitmap, 10)[0]
            bits = struct.unpack_from("<H", bitmap, 28)[0]
            self.assertEqual(bits, 32)
            card_width, card_height = 640, 360
            x, y = (width - card_width) // 2, (abs(height) - card_height) // 2
            row = abs(height) - 1 - y if height > 0 else y
            pixel = offset + (row * width + x) * 4
            b, g, r = bitmap[pixel:pixel + 3]
            self.assertEqual((r, g, b), (255, 199, 102), f"screen {index} lost its centered panel")

    def test_display_settings_work_across_input_methods_accounts_and_launches(self):
        root = self.folder("display")
        settings = root / "game/settings.ini"
        # Keyboard navigation works on a fresh, signed-out launcher.
        self.run_launcher(root, window_input="shift-tab Return Right Down Right Escape Escape")
        self.assertFalse(settings.exists(), "Cancel wrote default or edited preferences")
        offline = self.run_launcher(root, window_input="shift-tab Return Down Right Down Right Down Return Down Return")
        offline = json.loads(offline.stdout)
        self.assertEqual(offline["settings"], {"fullscreen": 0, "antialiasing": 0,
                                               "render_scale": 150, "window_preset": 720})

        # A signed-in user can skip the disabled window size on the pad.
        self.assertEqual(self.account(root, "sign-in", "--server", "one.example:31313",
                                      "--user", "Player1", "--remember")["result"], "signed_in")
        write(settings, "fullscreen=0\nwindow_preset=1080\nrender_scale=150\nantialiasing=1\n"
                        "overlay=1\nwindow_width=1100\nwindow_height=700\n")
        online = json.loads(self.run_launcher(root, window_input="pad pu pa pa pd pa @295,60 pd pa pd pa").stdout)
        status = self.run_launcher(root, "--account", "status").stdout
        self.assertIn("active=Player1@one.example_31313", status)
        self.assertEqual(online["profile"]["npid"], "Player1")
        self.assertEqual(online["settings"], {
            "fullscreen": 1, "window_preset": 1080, "render_scale": 200, "antialiasing": 1,
            "overlay": 1, "window_width": 1100, "window_height": 700,
        })
        self.run_launcher(root, window_input="pad pu pa pa pb Escape")
        saved = settings.read_bytes()

        # Settings are shared when switching accounts. Restore defaults
        # changes the draft, followed by Cancel or an explicit Save.
        self.assertEqual(self.account(root, "sign-in", "--server", "two.example:31313",
                                      "--user", "Player2", "--remember")["result"], "signed_in")
        self.run_launcher(root, window_input="@150,132 @226,145 @132,145 Escape")
        self.assertEqual(settings.read_bytes(), saved)
        self.run_launcher(root, window_input="@150,132 @226,145 @52,145 Escape")
        online = json.loads(self.run_launcher(root, "--play").stdout)
        self.assertEqual(online["profile"]["npid"], "Player2")
        self.assertEqual(online["settings"], {
            "fullscreen": 0, "window_preset": 0, "render_scale": 100, "antialiasing": 1,
            "overlay": 1, "window_width": 1100, "window_height": 700,
        })

    def test_all_six_render_scales_save_and_relaunch_from_keyboard_and_controller(self):
        root = self.folder("six resolution choices")
        for prefix in ("scale-", "pad-scale-"):
            for percent in (50, 75, 100, 150, 200, 300):
                with self.subTest(method=prefix, percent=percent):
                    # Use the packaged cross-platform smoke events, including
                    # a virtual SDL controller, without LD_PRELOAD input hooks.
                    env = os.environ.copy()
                    env.update(SDL_VIDEODRIVER="dummy", TB_LAUNCHER_DISPLAY_SMOKE=prefix + str(percent),
                               SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT="0x0000/0x0000")
                    result = subprocess.run([str(root / "Play Tank Battles")], capture_output=True, text=True,
                                            env=env, timeout=25, check=True)
                    settings = json.loads(result.stdout)["settings"]
                    self.assertEqual(settings["render_scale"], percent)
                    self.assertEqual(settings["window_preset"], 0)
                    self.assertEqual(json.loads(self.run_launcher(root, "--offline").stdout)["settings"], settings)

    def test_a_missing_player_is_explained(self):
        root = self.folder("broken")
        (root / "player/TankBattlesRecomp").unlink()
        result = subprocess.run([str(root / "Play Tank Battles"), "--offline"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Build it again", result.stderr)


def stat_mode(path):
    return os.stat(path).st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
