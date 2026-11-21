"""Game-free checks for the standalone-builder toolchain manifest and stager."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import zipfile

import toolchain


class ManifestTests(unittest.TestCase):
    def test_every_component_is_pinned_with_a_digest_and_licence(self):
        manifest = toolchain.load_manifest()
        for name, tool in manifest["tools"].items():
            self.assertTrue(tool["version"] and tool["licence"], name)
            for host in toolchain.HOSTS:
                self.assertTrue(tool[host]["url"].startswith("https://"), name)
                self.assertRegex(tool[host]["sha256"], r"^[0-9a-f]{64}$", name)
        for name, wheel in manifest["wheels"].items():
            hosts = [h for h in wheel if h != "version"]
            self.assertTrue(hosts == ["any"] or sorted(hosts) == ["linux", "windows"], name)
            for host in hosts:
                self.assertRegex(wheel[host]["sha256"], r"^[0-9a-f]{64}$", name)
        for name, source in manifest["sources"].items():
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$", name)
            self.assertTrue(source["licence"], name)

    def test_lifter_requirements_are_all_pinned(self):
        manifest = toolchain.load_manifest()
        requirements = (toolchain.ROOT / "external/ps3recomp/requirements.txt").read_text()
        for line in requirements.splitlines():
            line = line.split("#")[0].strip()
            if not line or "python_version <" in line:
                continue
            name = line.split(">")[0].split("=")[0].split(";")[0].strip().lower()
            self.assertIn(name, manifest["wheels"], f"{name} from requirements.txt is not in the toolchain")

    def test_targets_are_the_supported_baselines(self):
        self.assertEqual(toolchain.TARGETS, {"linux": "x86_64-linux-gnu.2.31", "windows": "x86_64-windows-gnu"})


class FetchAndExtractTests(unittest.TestCase):
    def test_a_digest_mismatch_is_rejected_and_nothing_is_cached(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "payload.bin"
            payload.write_bytes(b"tampered")
            wrong = hashlib.sha256(b"original").hexdigest()
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                toolchain.fetch_one(Path(temporary) / "cache", payload.as_uri(), wrong)
            self.assertFalse(toolchain.cached(Path(temporary) / "cache", wrong).exists())

    def test_a_matching_download_is_cached_by_digest_and_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "payload.bin"
            payload.write_bytes(b"pinned")
            digest = hashlib.sha256(b"pinned").hexdigest()
            first = toolchain.fetch_one(Path(temporary) / "cache", payload.as_uri(), digest)
            payload.unlink()   # reuse must not need the network
            self.assertEqual(toolchain.fetch_one(Path(temporary) / "cache", payload.as_uri(), digest), first)

    def test_archives_cannot_escape_the_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "evil.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("../escaped.txt", "x")
            with self.assertRaisesRegex(ValueError, "Unsafe path"):
                toolchain.safe_extract(archive, Path(temporary) / "out")
            self.assertFalse((Path(temporary) / "escaped.txt").exists())
            tar = Path(temporary) / "evil.tar.gz"
            with tarfile.open(tar, "w:gz") as t:
                info = tarfile.TarInfo("../escaped.txt")
                info.size = 1
                t.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(Exception):
                toolchain.safe_extract(tar, Path(temporary) / "out2")
            self.assertFalse((Path(temporary) / "escaped.txt").exists())

    def test_wheels_unpack_into_site_packages_without_running_python(self):
        with tempfile.TemporaryDirectory() as temporary:
            wheel = Path(temporary) / "demo-1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as z:
                z.writestr("demo/__init__.py", "VALUE = 7\n")
                z.writestr("demo-1.0.dist-info/METADATA", "Name: demo\n")
            site = Path(temporary) / "site"
            site.mkdir()
            toolchain.install_wheel(wheel, site)
            self.assertEqual((site / "demo/__init__.py").read_text(), "VALUE = 7\n")


@unittest.skipIf(os.name == "nt", "POSIX shell wrapper")
class WrapperTests(unittest.TestCase):
    def test_static_is_dropped_only_from_shared_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "zig").mkdir()
            fake = root / "zig/zig"
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            fake.chmod(0o755)
            toolchain.write_wrappers(root / "bin", "linux")
            run = lambda *a: subprocess.run([str(root / "bin/zig-c++"), *a], capture_output=True, text=True,
                                            check=True, env={"PATH": "/nonexistent"}).stdout.split()
            self.assertEqual(run("-shared", "-static", "-o", "x.dll"), ["c++", "-shared", "-o", "x.dll"])
            self.assertEqual(run("-static", "-o", "x"), ["c++", "-static", "-o", "x"])
            self.assertEqual(run("a b", "-c"), ["c++", "a", "b", "-c"][:1] + ["a", "b", "-c"])


if __name__ == "__main__":
    unittest.main()
