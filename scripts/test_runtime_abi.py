"""Reject title headers generated against a different reusable PPU ABI."""
from pathlib import Path
import subprocess
import hashlib
import json
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SDK=ROOT/'external/ps3recomp'

class RuntimeABI(unittest.TestCase):
    def test_matching_title_and_changed_preamble(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            command=[sys.executable,str(ROOT/'scripts/runtime_abi.py'),'--sdk',str(SDK),'--output',str(root/'abi')]
            subprocess.run(command,check=True,capture_output=True)
            title=root/'title.h'
            common=(root/'abi/ppu_recomp.h').read_text()
            identity=json.loads((root/'abi/abi.json').read_text())['header_sha256']
            self.assertEqual(hashlib.sha256((root/'abi/ppu_recomp.h').read_bytes()).hexdigest(),identity)
            self.assertNotIn(b'\r\n',(root/'abi/ppu_recomp.h').read_bytes())
            title.write_text(common+'\nvoid original_synthetic_function(ppu_context*);\n')
            result=subprocess.run(command+['--check-title-header',str(title)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            title.write_text(common.replace('uint64_t','uint32_t',1))
            result=subprocess.run(command+['--check-title-header',str(title)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Title ABI differs',result.stderr)

if __name__=='__main__':unittest.main()
