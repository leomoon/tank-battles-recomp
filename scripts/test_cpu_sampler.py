"""Exercise actual user-only CPU sampling against an owned synthetic busy process."""
import subprocess,sys,tempfile,unittest
from pathlib import Path
from port import ROOT

class CPUSamplerTests(unittest.TestCase):
 def test_samples_owned_busy_process(self):
  with tempfile.TemporaryDirectory() as tmp:
   binary=Path(tmp)/'sampler'
   subprocess.run(['cc','-O2',str(ROOT/'scripts/sample_gameplay_cpu.c'),'-o',str(binary)],check=True)
   process=subprocess.Popen([sys.executable,'-c','import time\nend=time.monotonic()+3\nwhile time.monotonic()<end: pass'])
   try:
    result=subprocess.run([str(binary),str(process.pid),'.5'],capture_output=True,text=True,timeout=3)
    if result.returncode and ('Operation not permitted' in result.stderr or 'Permission denied' in result.stderr):self.skipTest('Host denies user perf events')
    self.assertEqual(result.returncode,0,result.stderr)
    rows=[line.split(',') for line in result.stdout.splitlines()]
    self.assertGreater(len(rows),0)
    for stamp,tid,ip in rows:
     self.assertGreater(int(stamp),0);self.assertEqual(int(tid),process.pid);self.assertGreater(int(ip,16),0)
   finally:process.terminate();process.wait(timeout=3)
if __name__=='__main__':unittest.main()
