import json
from pathlib import Path
import tempfile
import unittest
from windows_cpu_report import analyze


class CpuReportTests(unittest.TestCase):
    def fixture(self,root):
        origin=1_000_000_000_000
        (root/'measurement.json').write_text(json.dumps(dict(interval_seconds=[2,5])))
        # Only the two middle samples are inside the measurement. Their
        # observation labels deliberately do not match the actual QPC epoch.
        samples=[]
        for second,cpu in ((1,0),(3,100_000_000),(4,600_000_000),(6,900_000_000)):
            threads=[dict(tid=7,name='ppu main',cpu_ns=cpu)]
            if second>=4:threads.append(dict(tid=9,name='new worker',cpu_ns=50_000_000))
            samples.append(dict(seconds=999,resources=dict(status='available',
                monotonic_ns=origin+second*1_000_000_000,process_cpu_ns=cpu*2,threads=threads)))
        (root/'observations.jsonl').write_text('\n'.join(json.dumps(s) for s in samples))
        times=[3_000_000_000+i*10_000_000 for i in range(101)]
        (root/'gameplay.jsonl').write_text(json.dumps(dict(monotonic_ns=origin+7_000_000_000,
            elapsed_ns=7_000_000_000,events=[[],times,[]]))+'\n')

    def test_cpu_and_frames_use_identical_actual_sample_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root);result=analyze(root)
            self.assertEqual(result['completed_frames'],100)
            self.assertEqual(result['coverage_seconds'],1)
            self.assertEqual(result['measurement_seconds'],3)
            self.assertEqual(result['threads'][0]['cpu_ms_per_frame'],5)
            self.assertEqual(result['threads'][0]['logical_cores'],.5)
            self.assertEqual(result['unmatched_thread_ids'],[9])
            self.assertEqual(result['unattributed_cpu_ms_per_frame'],5)

    def test_regressing_counter_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            path=root/'observations.jsonl';rows=[json.loads(s) for s in path.read_text().splitlines()]
            rows[2]['resources']['threads'][0]['cpu_ns']=0
            path.write_text('\n'.join(json.dumps(s) for s in rows))
            with self.assertRaisesRegex(ValueError,'regressed'):analyze(root)

    def test_missing_data_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(analyze(tmp)['status'],'unavailable')


if __name__=='__main__':unittest.main()
