import tempfile
import unittest
from pathlib import Path

from setup_pipeline import setup_lock


class SetupLockTests(unittest.TestCase):
    def test_lock_file_is_created_and_context_exits(self):
        with tempfile.TemporaryDirectory() as temporary:
            with setup_lock(temporary):
                self.assertTrue((Path(temporary) / "setup-state.lock").is_file())


if __name__ == "__main__":
    unittest.main()
