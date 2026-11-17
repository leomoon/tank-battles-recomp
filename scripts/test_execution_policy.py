#!/usr/bin/env python3
"""Exercise strict failure paths through the instrumented native SDK."""
import hashlib,json,os,subprocess,sys,unittest
from pathlib import Path
from port import ROOT

class ExecutionPolicyTests(unittest.TestCase):
 def run_probe(self,kind,mode='strict'):
  directory=ROOT/'build-tests/execution-policy';directory.mkdir(parents=True,exist_ok=True)
  env={k:v for k,v in os.environ.items() if not k.startswith(('PS3_','TB_'))}
  env.update(PS3_PARAM_SFO=os.environ.get('TB_TEST_SFO',str(ROOT/'build-smoke/synthetic/PARAM.SFO')),PS3_VFS_ROOT=str(directory),
   PS3_HDD0_ROOT=str(directory/'dev_hdd0'),PS3_HDD1_ROOT=str(directory/'dev_hdd1'),PS3_GAMEDATA_ROOT=str(directory),
   TB_EXECUTION_PROBE=kind,TB_EXECUTION_MODE=mode,SDL_AUDIODRIVER='dummy',SDL_VIDEODRIVER='dummy')
  result=subprocess.run([os.environ.get('TB_TEST_RUNNER',str(ROOT/'build-smoke/TankBattlesRecomp')),os.environ.get('TB_TEST_ELF',str(ROOT/'build-smoke/synthetic/smoke.elf'))],env=env,cwd=ROOT,capture_output=True,timeout=10)
  text=(result.stdout+result.stderr).decode(errors='replace');(directory/f'{kind}-{mode}.log').write_text(text)
  return result.returncode,text
 def test_missing_import_stops(self):
  code,text=self.run_probe('import');self.assertEqual(code,86,text[-1200:]);self.assertIn('STRICT STOP: hle-missing',text)
  self.assertIn('hle-missing=0x00000001',text)
 def test_unknown_target_stops(self):
  code,text=self.run_probe('target');self.assertEqual(code,86,text[-1200:]);self.assertIn('STRICT STOP: ppu-miss',text)
  self.assertIn('address=0x12345678',text)
 def test_null_target_stops(self):
  code,text=self.run_probe('null-target');self.assertEqual(code,86,text[-1200:]);self.assertIn('STRICT STOP: ppu-miss',text)
 def test_interpreter_entry_stops(self):
  code,text=self.run_probe('interpreter');self.assertEqual(code,86,text[-1200:]);self.assertIn('STRICT STOP: spu-interpreter',text)
  self.assertIn('image=0x00015F91',text)
 def test_untranslated_spu_target_stops(self):
  code,text=self.run_probe('microstep');self.assertEqual(code,86,text[-1200:]);self.assertIn('STRICT STOP: spu-untranslated',text)
 def test_diagnostic_mode_reports_missing_import(self):
  code,text=self.run_probe('import','diagnostic');self.assertEqual(code,0,text[-1200:]);self.assertNotIn('STRICT STOP',text)
  self.assertIn('hle-missing=0x00000001',text)
 def test_conversion_results(self):
  code,text=self.run_probe('conversion');self.assertEqual(code,0,text[-1200:]);self.assertIn('[conversion-probe] PASS',text)
 def test_source_overlay_is_reproducible(self):
  directory=ROOT/'build-tests/execution-policy';directory.mkdir(parents=True,exist_ok=True)
  outputs=[]
  for suffix in ('a','b'):
   output=directory/('overlay-'+suffix)
   subprocess.run([sys.executable,str(ROOT/'scripts/instrument_sdk.py'),'--sdk',str(ROOT/'external/ps3recomp'),'--output',str(output)],check=True)
   outputs.append(json.loads((output/'manifest.json').read_text()))
  self.assertEqual(outputs[0],outputs[1])
  for item in outputs[0]:
   original=ROOT/'external/ps3recomp'/item['source']
   self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),item['sha256'])

if __name__=='__main__':unittest.main()
