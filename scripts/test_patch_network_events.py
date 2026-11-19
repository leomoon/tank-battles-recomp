"""Check the local title hook without using game code or data."""
from pathlib import Path
import tempfile
import unittest
from patch_network_events import DECLARATION, HOOK, SIGNATURE, patch


class NetworkHookTests(unittest.TestCase):
    def test_hook_is_attached_once(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'ppu_recomp_000.cpp'
            path.write_text('#include "ppu_recomp.h"\n' + SIGNATURE + '}\n')
            patch(name)
            first = path.read_bytes()
            self.assertIn((SIGNATURE + HOOK).encode(), first)
            self.assertEqual(first.count(DECLARATION.encode()), 1)
            patch(name)
            self.assertEqual(path.read_bytes(), first)

    def test_ambiguous_hook_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            for n in range(2):
                (Path(name) / f'ppu_recomp_{n:03}.cpp').write_text(SIGNATURE)
            with self.assertRaises(ValueError):
                patch(name)

    def test_game_free_fixture_is_unchanged(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'ppu_recomp_000.cpp'
            path.write_text('void synthetic_fixture() {}\n')
            patch(name)
            self.assertEqual(path.read_text(), 'void synthetic_fixture() {}\n')


if __name__ == '__main__':
    unittest.main()
