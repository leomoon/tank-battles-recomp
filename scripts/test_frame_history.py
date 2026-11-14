#!/usr/bin/env python3
"""Game-free capture bounds, pixel integrity, and sequence-verifier checks."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from frame_history_report import report
from port import ROOT

class FrameHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out=ROOT/'build-tests/frame-history';out.mkdir(parents=True,exist_ok=True)
        source=out/'fixture.cpp'
        source.write_text('#include "execution_policy_api.h"\nint main(){unsigned char p[]={1,2,3,255,4,5,6,0};tb_scanout_sample(2,7,4096);for(unsigned n=10;n<13;n++)tb_frame_history(p,2,1,n);}\n')
        cls.exe=out/'fixture'
        subprocess.run(['c++','-std=c++17','-I'+str(ROOT),str(source),str(ROOT/'frame_history.cpp'),'-pthread','-o',str(cls.exe)],check=True)

    def fixture(self,d,**extra):
        env={k:v for k,v in os.environ.items() if not k.startswith('TB_FRAME_')}
        env.update(TB_FRAME_HISTORY=str(d/'history'),TB_FRAME_HISTORY_LIMIT='2',**extra)
        (d/'history').mkdir()
        subprocess.run([str(self.exe)],env=env,check=True,timeout=5)
        (d/'session.json').write_text(json.dumps(dict(strict_passed=True,session=dict(interrupted=False,launcher_exit_status=124))))
        (d/'startup.log').write_text('')

    def test_bounds_pixels_and_consecutive_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);self.fixture(d)
            self.assertEqual(len(list((d/'history').glob('*.ppm'))),2)
            self.assertEqual((d/'history/frame-0000.ppm').read_bytes(),b'P6\n2 1\n255\n\x01\x02\x03\x04\x05\x06')
            r=report(d);self.assertTrue(r['accepted_for_verification'])
            self.assertEqual([f['present'] for f in r['frames']],[10,11])
            self.assertEqual(r['frames'][0]['offset'],4096)

    def test_delayed_capture_does_not_write_early_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);self.fixture(d,TB_FRAME_HISTORY_AFTER='3600')
            self.assertEqual(list((d/'history').iterdir()),[])

    def test_truncated_frame_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);self.fixture(d)
            (d/'history/frame-0001.ppm').write_bytes(b'P6\n2 1\n255\n')
            with self.assertRaisesRegex(ValueError,'Incomplete'):report(d)

    def test_missing_present_id_is_not_consecutive(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);self.fixture(d)
            p=d/'history/frames.jsonl';rows=[json.loads(line) for line in p.read_text().splitlines()]
            rows[1]['present']+=1;p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            with self.assertRaisesRegex(ValueError,'not consecutive'):report(d)

    def test_failed_session_and_renderer_failure_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);self.fixture(d)
            (d/'startup.log').write_text('[rsx engine] pipeline -> fail\n[NV0039] invalid transfer geometry or DMA context\n')
            self.assertFalse(report(d)['accepted_for_verification'])
            self.assertEqual(report(d)['renderer_failure_count'],2)
            (d/'session.json').write_text(json.dumps(dict(strict_passed=False,session=dict(interrupted=False,launcher_exit_status=86))))
            with self.assertRaisesRegex(ValueError,'diagnostic only'):report(d)
            self.assertFalse(report(d,allow_failed=True)['accepted_for_verification'])

if __name__=='__main__':unittest.main()
