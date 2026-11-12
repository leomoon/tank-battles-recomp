#!/usr/bin/env python3
"""Integration checks against the synthetic runner's real handler registrations."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from port import ROOT

class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "external/ps3recomp/tools"))
        from nid_database import compute_nid
        cls.nid = staticmethod(compute_nid)
        directory = ROOT / "analysis/step4-fixture"
        directory.mkdir(parents=True, exist_ok=True)
        entries = [(name, compute_nid(name)) for name in
                   ("cellPadInit", "cellAudioInit", "sys_initialize_tls", "sys_lwmutex_lock", "connect")]
        entries.append(("unknown", 0xDEADBEEF))
        source = directory / "imports.json"
        source.write_text(json.dumps([dict(library="synthetic", nid=hex(nid), stub="0x10000")
                                      for _,nid in entries]))
        output = directory / "report.json"
        subprocess.run([sys.executable, str(ROOT / "scripts/audit_imports.py"),
                        "--imports", str(source), "--elf", "build-smoke/synthetic/smoke.elf",
                        "--build-dir", "build-smoke", "--output", str(output)], cwd=ROOT, check=True)
        cls.entries = {int(x["nid"], 16): x for x in json.loads(output.read_text())["imports"]}
    def entry(self, name):
        return self.entries[self.nid(name)]
    def test_generic_handler(self):
        entry = self.entry("cellAudioInit")
        self.assertTrue(entry["runtime_has_handler"])
        self.assertIn("cellAudioInit", entry["generic_registrations"])
    def test_context_and_fast_handlers(self):
        self.assertEqual(self.entry("sys_initialize_tls")["effective_dispatch"], "context")
        self.assertEqual(self.entry("sys_lwmutex_lock")["effective_dispatch"], "fast_then_context")
    def test_port_override_precedence(self):
        entry = self.entry("cellPadInit")
        self.assertTrue(entry["context_registrations"])
        self.assertTrue(entry["port_override_aliases"])
        self.assertEqual(entry["effective_dispatch"], "port_override")
    def test_optional_is_not_missing_implementation(self):
        entry = self.entry("connect")
        self.assertFalse(entry["runtime_has_handler"])
        self.assertEqual(entry["status"], "absent_in_selected_profile")
    def test_unknown_name_and_absent_handler(self):
        entry = self.entries[0xDEADBEEF]
        self.assertFalse(entry["name_resolved"])
        self.assertFalse(entry["runtime_has_handler"])
        self.assertEqual(entry["status"], "missing_handler")

if __name__ == "__main__":
    unittest.main()
