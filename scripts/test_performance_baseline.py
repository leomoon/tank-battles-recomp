"""Integrity checks for private baseline evidence, with synthetic files only."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from freeze_performance_baseline import copy_checked, executable, hashes, sha, verify
from host_inventory import command


class BaselineTests(unittest.TestCase):
    def test_multiconfig_executable_and_byte_preservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'build/Release/TankBattlesRecomp.exe'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'synthetic executable')
            self.assertEqual(executable(root/'build'), source)
            frozen = root/'frozen/player/TankBattlesRecomp.exe'
            self.assertEqual(copy_checked(source, frozen), sha(source))
            self.assertNotEqual(source.stat().st_ino, frozen.stat().st_ino)
            metadata = dict(executable='player/TankBattlesRecomp.exe', executable_sha256=sha(frozen), files=hashes(root/'frozen'))
            (root/'frozen/baseline.json').write_text(json.dumps(metadata))
            verify(root/'frozen')
            frozen.write_bytes(b'changed executable')
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                verify(root/'frozen')
            self.assertEqual(source.read_bytes(), b'synthetic executable')

    def test_manifest_cannot_verify_outside_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'outside').write_bytes(b'outside')
            snapshot = root/'snapshot'
            snapshot.mkdir()
            (snapshot/'baseline.json').write_text(json.dumps(dict(files={'../outside':sha(root/'outside')})))
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                verify(snapshot)

    def test_missing_inventory_command_is_explicitly_unavailable(self):
        with patch('host_inventory.subprocess.run', side_effect=FileNotFoundError('probe absent')):
            self.assertIn('unavailable', command(['absent']))


if __name__ == '__main__':
    unittest.main()
