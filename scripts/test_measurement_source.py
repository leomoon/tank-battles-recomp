"""Prevent an older or damaged runtime being used as the requested baseline."""
import unittest
from unittest.mock import patch
from build_identity import require_runtime_source


class MeasurementSourceTests(unittest.TestCase):
    def test_old_or_unidentified_runtime_is_rejected(self):
        for source in ('older', None):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, 'Rebuild'):
                require_runtime_source({'runtime_source_commit': source}, 'current')

    def test_matching_commit_still_requires_integrity_verification(self):
        with patch('runtime_bundle.verify', side_effect=ValueError('Runtime file hash mismatch')) as verify:
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                require_runtime_source({'runtime_source_commit': 'current',
                                        'runtime_manifest': '/runtime/manifest.json'}, 'current')
            self.assertEqual(verify.call_args.kwargs, {'source': 'current'})

    def test_matching_verified_runtime_is_accepted(self):
        with patch('runtime_bundle.verify', return_value={}):
            require_runtime_source({'runtime_source_commit': 'current',
                                    'runtime_manifest': '/runtime/manifest.json'}, 'current')


if __name__ == '__main__':
    unittest.main()
