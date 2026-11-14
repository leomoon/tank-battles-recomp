#!/usr/bin/env python3
"""Exercise session exit codes, logging, and POSIX timeout cleanup."""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from boot_session import run_session
from port import ROOT

class SessionTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "build-tests").mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=ROOT / "build-tests")
        self.path = Path(self.directory.name)
    def tearDown(self):
        self.directory.cleanup()
    def session(self, code, **kwargs):
        return run_session([sys.executable, "-c", code], cwd=ROOT, env=os.environ.copy(),
                           log_path=self.path / "session.log", **kwargs)
    def test_nonzero_exit_and_both_streams(self):
        status, result = self.session("import sys; print('stdout-marker'); print('stderr-marker', file=sys.stderr); sys.exit(7)")
        self.assertEqual(status, 7)
        self.assertEqual(result["child_returncode"], 7)
        log = (self.path / "session.log").read_text()
        self.assertIn("stdout-marker", log)
        self.assertIn("stderr-marker", log)
        self.assertEqual(json.loads((self.path / "session.log.result.json").read_text()), result)
    def test_start_observer_and_failure_cleanup(self):
        observed=[]
        def fail(process):
            observed.append(process)
            raise ValueError('observer failed')
        with self.assertRaisesRegex(ValueError,'observer failed'):
            self.session('import time; time.sleep(60)',on_start=fail)
        self.assertEqual(len(observed),1)
        self.assertIsNotNone(observed[0].poll())
    @unittest.skipUnless(os.name == "posix", "POSIX signals")
    def test_signal_exit_preserved(self):
        status, result = self.session("import os, signal; os.kill(os.getpid(), signal.SIGTERM)")
        self.assertEqual(status, 143)
        self.assertEqual(result["child_returncode"], -signal.SIGTERM)
    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux process-tree inspection")
    def test_timeout_kills_descendant(self):
        child = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); print(p.pid,flush=True); time.sleep(60)"
        status, result = self.session(code, timeout=1)
        self.assertEqual(status, 124)
        self.assertTrue(result["timed_out"])
        pid = int((self.path / "session.log").read_text().strip())
        stat = Path(f"/proc/{pid}/stat")
        deadline = time.monotonic() + 2
        while stat.exists():
            try:
                state = stat.read_text().rsplit(")", 1)[1].split()[0]
            except FileNotFoundError:
                break
            if state == "Z":
                break
            self.assertLess(time.monotonic(), deadline, "Descendant survived process-group cleanup")
            time.sleep(0.02)

if __name__ == "__main__":
    unittest.main()
