import json
import tempfile
import unittest
from pathlib import Path
from depth_cost_report import report

class DepthCostReportTests(unittest.TestCase):
    def fixture(self,p):
        m=dict(strict_passed=True,phase='gameplay',live_gameplay_verified=True,interval_seconds=[1,5],diagnostics=dict(texture_decode_profile=True))
        (p/'measurement.json').write_text(json.dumps(m))
        (p/'gameplay.jsonl').write_text(json.dumps(dict(monotonic_ns=10_000_000_000,elapsed_ns=0,events=[[],[2_100_000_000,2_500_000_000,3_100_000_000,3_500_000_000],[]])))
        # Samples outside the live interval must not inflate its costs.
        text=''
        for stamp,calls,size,cost in [(10_500_000_000,1,8,100),(12_000_000_000,10,80,1000),(14_000_000_000,14,112,9000),(16_000_000_000,1000,8000,9000000)]:
            text+=f'[depth-cost] path=surface-guest-read frame=9 calls={calls} requested_bytes={size} inclusive_ns={cost} monotonic_ns={stamp}\n'
        (p/'startup.log').write_text(text)
        return m

    def test_aligns_costs_to_live_completed_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary);self.fixture(p)
            result=report(p)['paths'];row=result['surface-guest-read']
            self.assertEqual((row['calls'],row['requested_bytes'],row['inclusive_ns'],row['completed_frames']),(4,32,8000,4))
            self.assertEqual(row['ms_per_completed_frame'],.002)
            self.assertFalse(result['depth-to-guest']['observed_anywhere_in_capture'])

    def test_wait_and_consumer_sites_remain_independent(self):
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary);self.fixture(p)
            text=(p/'startup.log').read_text()
            for label in ('wait:vk_eng_flush:1','flush:vk_eng_draw:2','consumer:guest-reference','fifo:0x1D70'):
                text+=(p/'startup.log').read_text().replace('surface-guest-read',label)
            (p/'startup.log').write_text(text)
            rows=report(p)['paths']
            for label in ('wait:vk_eng_flush:1','flush:vk_eng_draw:2','consumer:guest-reference','fifo:0x1D70'):
                self.assertEqual(rows[label]['inclusive_ns'],8000)
                self.assertEqual(rows[label]['completed_frames'],4)

    def test_rejects_failed_scene_and_unalignable_logs(self):
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary);self.fixture(p)
            (p/'invalid-scene.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'ending scene'):report(p)
            (p/'invalid-scene.json').unlink()
            (p/'startup.log').write_text('[depth-cost] old unaligned data')
            with self.assertRaisesRegex(ValueError,'timestamped'):report(p)

    def test_snapshot_hits_do_not_imply_fewer_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary);self.fixture(p)
            with (p/'startup.log').open('a') as f:
                f.write('[color-readback] frame=1 calls=6 hits=5 requested_bytes=96 fetched_bytes=100 monotonic_ns=12000000000\n')
                f.write('[color-readback] frame=2 calls=12 hits=10 requested_bytes=192 fetched_bytes=200 monotonic_ns=14000000000\n')
            row=report(p)['color_snapshot']
            self.assertAlmostEqual(row['hit_fraction'],5/6)
            self.assertEqual(row['completed_frames'],4)
            self.assertEqual(row['clipped_requested_bytes_per_frame'],24)
            self.assertEqual(row['backend_snapshot_bytes_per_frame'],25)

if __name__=='__main__':unittest.main()
