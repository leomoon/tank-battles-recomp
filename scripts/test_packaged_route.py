"""The installed builder route on synthetic input: read-only relocated
resources, a workspace path with spaces and non-ASCII characters, only the
staged toolchain on PATH, no Git, no network.

Needs a runtime bundle and a staged toolchain for this host:
    TB_TEST_RUNTIME_BUNDLE=build-zig/linux/runtime-bundle TB_TEST_TOOLCHAIN=/opt/tb-toolchain \\
        python -m unittest test_packaged_route
"""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = os.environ.get("TB_TEST_RUNTIME_BUNDLE")
TOOLCHAIN = os.environ.get("TB_TEST_TOOLCHAIN")

DRIVER = r'''
import argparse, sys
sys.path.insert(0, sys.argv[1] + "/scripts")
import port
from install_layout import Layout
port.LAYOUT = Layout(port.ROOT, sys.argv[2])
port.LAYOUT.verify()
config = port.LAYOUT.load_config("config.toml")
args = argparse.Namespace(elf=sys.argv[3], restart=False, build_dir=None, jobs=4, build_type="Release",
                          backend="auto", cmake="cmake", ninja="ninja", cmake_args=[], runtime_bundle=None,
                          runtime_source=None, toolchain=None, sdk=None, recomp_dir=None, analyze_only=False)
port.selected_runtime(args)
from setup_pipeline import run_pipeline
run_pipeline(args, config, port.resolve, port.prepare, port.configure, port.build)
'''


def make_writable(path):
    for root, directories, files in os.walk(path):
        for name in directories + files:
            target = Path(root) / name
            if not target.is_symlink():
                target.chmod(target.stat().st_mode | stat.S_IWUSR)
    Path(path).chmod(Path(path).stat().st_mode | stat.S_IWUSR)


@unittest.skipUnless(BUNDLE and TOOLCHAIN, "set TB_TEST_RUNTIME_BUNDLE and TB_TEST_TOOLCHAIN")
class PackagedRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = Path(tempfile.mkdtemp(prefix="tb packaged route "))
        cls.package = cls.temporary / "Builder ü" / "resources"
        source = json.loads((Path(BUNDLE) / "manifest.json").read_text())["source_commit"]
        subprocess.run([sys.executable, str(ROOT / "scripts/package_resources.py"), "assemble", "--output", str(cls.package),
                        "--runtime-bundle", BUNDLE, "--toolchain", TOOLCHAIN, "--source", source, "--link-large"],
                       check=True, capture_output=True)
        # Like a mounted AppImage: nothing under the resources can be written.
        for root, directories, files in os.walk(cls.package, topdown=False):
            for name in files + directories:
                path = Path(root) / name
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode & ~0o222)
        cls.package.chmod(0o555)
        cls.workspace = cls.temporary / "wörk space"
        (cls.workspace / "home").mkdir(parents=True)
        cls.fixture = cls.temporary / "fixture"
        subprocess.run([sys.executable, str(ROOT / "scripts/prepare_runtime_fixture.py"), "--sdk",
                        str(ROOT / "external/ps3recomp"), "--output", str(cls.fixture)], check=True, capture_output=True)
        identity = json.loads((Path(TOOLCHAIN) / "toolchain.json").read_text())
        cls.python = Path(TOOLCHAIN) / "python" / ("python.exe" if identity["host"] == "windows" else "bin/python3")

    @classmethod
    def tearDownClass(cls):
        make_writable(cls.temporary)
        shutil.rmtree(cls.temporary)

    def environment(self):
        env = {"HOME": str(self.workspace / "home"), "PATH": "/nonexistent", "LANG": "C.UTF-8"}
        for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA"):
            if key in os.environ:
                env[key] = os.environ[key]
        return env

    def pipeline(self, package=None):
        command = [str(self.python), "-E", "-s", "-c", DRIVER, str(package or self.package), str(self.workspace),
                   str(self.fixture / "smoke.elf")]
        unshare = shutil.which("unshare")
        if unshare and sys.platform.startswith("linux"):
            probe = subprocess.run([unshare, "-Un", "--map-current-user", "true"], capture_output=True)
            if probe.returncode == 0:
                command = [unshare, "-Un", "--map-current-user", *command]   # No network; ordinary file permissions.
        return subprocess.run(command, env=self.environment(), capture_output=True, text=True, timeout=1800)

    def test_build_reuse_resume_and_rejections(self):
        first = self.pipeline()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        player = self.workspace / "build-player" / ("TankBattlesRecomp.exe" if os.name == "nt" else "TankBattlesRecomp")
        self.assertTrue(player.is_file())
        written = [p for p in self.package.rglob("*") if p.name == "__pycache__" and not p.is_relative_to(self.package / "toolchain")]
        self.assertFalse(written, "the read-only resources were written to")
        self.assertIn("[setup] prepare: translating into staging", first.stdout)

        again = self.pipeline()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("verified translations reused", again.stdout)
        self.assertIn("verified configuration reused", again.stdout)
        self.assertIn("no work to do", again.stdout)

        player.unlink()   # An interrupted link: completed stages are kept, only the link reruns.
        resumed = self.pipeline()
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertIn("verified translations reused", resumed.stdout)
        self.assertTrue(player.is_file())

        moved = self.temporary / "moved ü" / "resources"
        moved.parent.mkdir()
        shutil.copytree(self.package, moved, symlinks=True)
        relocated = self.pipeline(moved)
        self.assertEqual(relocated.returncode, 0, relocated.stdout + relocated.stderr)
        self.assertIn("verified translations reused", relocated.stdout)

        make_writable(moved)
        script = moved / "scripts/setup_inputs.py"
        script.write_text(script.read_text() + "\n# modified\n")
        tampered = self.pipeline(moved)
        self.assertNotEqual(tampered.returncode, 0)
        self.assertIn("damaged or modified", tampered.stderr)

    def test_a_runtime_from_another_source_is_rejected(self):
        command = [str(self.python), "-E", "-s", str(self.package / "scripts/port.py"), "--workspace",
                   str(self.workspace / "other"), "configure", "--elf", str(self.fixture / "smoke.elf"),
                   "--runtime-source", "0" * 40]
        result = subprocess.run(command, env=self.environment(), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Runtime bundle source mismatch", result.stderr)

    def test_the_workspace_cannot_be_inside_the_resources(self):
        command = [str(self.python), "-E", "-s", str(self.package / "scripts/port.py"), "--workspace",
                   str(self.package / "work"), "build"]
        result = subprocess.run(command, env=self.environment(), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the installed builder resources", result.stderr)


if __name__ == "__main__":
    unittest.main()
