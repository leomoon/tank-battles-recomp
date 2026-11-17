"""Game-free preparation, atomic publication and Windows/POSIX privacy checks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import prepare_entitlement as preparation
from private_files import WindowsFiles, publish_private
from test_entitlement import fixture

ROOT = Path(__file__).resolve().parents[1]


class PreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="entitlement-café-雪-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.edat = self.directory / (preparation.CONTENT_ID + ".edat")
        self.rap = self.directory / (preparation.CONTENT_ID + ".rap")
        self.output = Path(str(self.edat) + ".rifkey")
        self.edat.write_bytes(fixture(bytes(16)))
        self.rap.write_bytes(bytes(range(16)))  # Artificial license input.

    def assert_private(self, path):
        if os.name == "nt":
            acl = WindowsFiles().acl(path)
            self.assertTrue(acl.startswith("D:P"), acl)
            self.assertEqual(acl.count("("), 1, acl)
            self.assertIn(";;FA;;;", acl)
            self.assertNotIn(";ID;", acl)
        else:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_pinned_tables_and_known_synthetic_vectors(self):
        vectors = {
            bytes(16): "a0b21c84d2ef63699e75c34eed055380",
            bytes(range(16)): "56b28b74268d9326cf9b3a522e7215b4",
            bytes([255]) * 16: "680415c81c0459e2ee941857315550d7",
        }
        for rap, expected in vectors.items():
            self.assertEqual(preparation.derive_rif(rap, preparation.platform_tables()).hex(), expected)
        metadata = json.loads((ROOT / "scripts/rap_format.json").read_text())
        self.assertEqual(len(metadata["source_commit"]), 40)
        self.assertIn(metadata["source_commit"], metadata["source_url"])

    def test_changed_malformed_or_missing_table_is_rejected(self):
        for change in (lambda table: table.pop("RAP_KEY"),
                       lambda table: table.update(RAP_E1=bytes(15)),
                       lambda table: table.update(RAP_E2=bytes(16)),
                       lambda table: table.update(RAP_PBOX=bytes(16))):
            tables = preparation.platform_tables()
            change(tables)
            with self.assertRaises(ValueError):
                preparation.derive_rif(bytes(16), tables)

    def test_explicit_source_verification_is_optional_and_pinned(self):
        crypto = self.directory / "optional-source/rpcs3/Crypto"
        crypto.mkdir(parents=True)
        header = crypto / "key_vault.h"
        text = "\n".join("const unsigned char " + name + "[16] = {" +
                         ",".join(f"0x{byte:02x}" for byte in value) + "};"
                         for name, value in preparation.platform_tables().items())
        header.write_text(text)
        self.assertEqual(preparation.platform_tables(crypto.parents[1]), preparation.platform_tables())
        header.write_text(text.replace("0x86", "0x00", 1))
        with self.assertRaisesRegex(ValueError, "pinned"):
            preparation.platform_tables(crypto.parents[1])

    def test_complete_private_file_and_matching_reuse(self):
        before = self.edat.read_bytes(), self.rap.read_bytes()
        path = preparation.prepare(self.directory)
        self.assertEqual(path.read_bytes().hex(), "56b28b74268d9326cf9b3a522e7215b4")
        self.assert_private(path)
        self.assertEqual(preparation.prepare(self.directory), path)
        self.assertEqual(before, (self.edat.read_bytes(), self.rap.read_bytes()))
        self.assertFalse(list(self.directory.glob(".rif-*.tmp")))

    def test_existing_different_sidecar_is_never_replaced(self):
        self.output.write_bytes(b"preserve this existing material")
        with self.assertRaisesRegex(ValueError, "differs"):
            preparation.prepare(self.directory)
        self.assertEqual(self.output.read_bytes(), b"preserve this existing material")

    def test_readable_existing_sidecar_becomes_private(self):
        self.output.write_bytes(bytes.fromhex("56b28b74268d9326cf9b3a522e7215b4"))
        if os.name != "nt":
            self.output.chmod(0o644)
        preparation.prepare(self.directory)
        self.assert_private(self.output)

    def test_failed_flush_never_publishes_partial_output(self):
        with patch("private_files.os.fsync", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                preparation.prepare(self.directory)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.directory.glob(".rif-*.tmp")))

    def test_publication_never_overwrites_a_concurrent_winner(self):
        self.output.write_bytes(b"winner")
        with self.assertRaises(FileExistsError):
            publish_private(self.output, b"loser")
        self.assertEqual(self.output.read_bytes(), b"winner")
        self.assertFalse(list(self.directory.glob(".rif-*.tmp")))

    def test_concurrent_preparers_publish_one_complete_file_without_logging_keys(self):
        command = [sys.executable, "-c",
                   "import sys; sys.path.insert(0,sys.argv[1]); from prepare_entitlement import prepare; prepare(sys.argv[2])",
                   str(ROOT / "scripts"), str(self.directory)]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
        for process in processes:
            out, err = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, err.decode(errors="replace"))
            self.assertEqual(out + err, b"")
        self.assertEqual(len(self.output.read_bytes()), 16)
        self.assert_private(self.output)
        self.assertFalse(list(self.directory.glob(".rif-*.tmp")))

    def test_invalid_rap_sizes_never_create_sidecar(self):
        for length in (0, 15, 17):
            self.rap.write_bytes(bytes(length))
            with self.assertRaisesRegex(ValueError, "exactly 16"):
                preparation.prepare(self.directory)
            self.assertFalse(self.output.exists())

    def test_symlink_sidecar_is_rejected_without_touching_target(self):
        target = self.directory / "unrelated"
        target.write_bytes(b"unrelated")
        try:
            self.output.symlink_to(target)
        except OSError:
            self.skipTest("Host does not grant symbolic-link creation")
        with self.assertRaisesRegex(ValueError, "regular file"):
            preparation.prepare(self.directory)
        self.assertEqual(target.read_bytes(), b"unrelated")

    def test_standalone_license_three_needs_no_rap_or_sidecar(self):
        self.edat.write_bytes(fixture(bytes(16), license=3))
        self.rap.unlink()
        result = subprocess.run([sys.executable, str(ROOT / "scripts/prepare_entitlement.py"), "--exdata", str(self.directory)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("needs no RAP", result.stdout)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
