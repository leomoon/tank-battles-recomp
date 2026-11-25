import unittest
import json
import tempfile
from pathlib import Path
from online_gameplay import review, capture
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
from gameplay_report import paired_cadence

class PairedCadenceTests(unittest.TestCase):
    def rows(self, origin=1_000_000_000_000):
        events=list(range(0,61_000_000_000,20_000_000))
        return [dict(monotonic_ns=origin+61_000_000_000,elapsed_ns=61_000_000_000,events=[events,events,events])]

    def test_independent_process_origins_align_to_same_host_interval(self):
        begin=1_000_000_000_000
        for origin in (begin,begin-10_000_000_000):
            rows=self.rows(origin)
            if origin<begin:
                rows[0]['events']=[[x+10_000_000_000 for x in e] for e in rows[0]['events']]
                rows[0]['elapsed_ns']+=10_000_000_000
                rows[0]['monotonic_ns']+=10_000_000_000
            report=paired_cadence(rows,begin,begin+60_000_000_000)
            self.assertEqual(report['guest_flip']['fps'],50)
            self.assertEqual(report['completed_frame']['p99_ms'],20)
            self.assertEqual(report['window_present']['one_second_frames'],[50]*60)

    def test_incomplete_coverage_and_short_intervals_are_rejected(self):
        begin=1_000_000_000_000
        with self.assertRaisesRegex(ValueError,'60 seconds'):
            paired_cadence(self.rows(),begin,begin+59_000_000_000)
        with self.assertRaisesRegex(ValueError,'coverage'):
            paired_cadence(self.rows(),begin,begin+62_000_000_000)

    def test_stalled_stream_and_duplicate_events_are_rejected(self):
        begin=1_000_000_000_000
        rows=self.rows();rows[0]['events'][1]=[0,59_000_000_000,61_000_000_000]
        with self.assertRaisesRegex(ValueError,'Stopped completed_frame'):
            paired_cadence(rows,begin,begin+60_000_000_000)
        rows=self.rows();rows[0]['events'][0].insert(1,0)
        with self.assertRaisesRegex(ValueError,'duplicate'):
            paired_cadence(rows,begin,begin+60_000_000_000)


    def test_present_refreshes_do_not_raise_completed_fps(self):
        begin=1_000_000_000_000
        rows=self.rows()
        rows[0]['events'][1]=list(range(0,61_000_000_000,25_000_000))
        result=paired_cadence(rows,begin,begin+60_000_000_000)
        self.assertEqual(result['completed_frame']['fps'],40)
        self.assertEqual(result['window_present']['fps'],50)

    def test_review_refuses_hidden_peers_and_keeps_scope_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            manifest=dict(start_ns=10_000_000_000,end_ns=70_000_000_000,peers=[dict(label='A'),dict(label='B')])
            (out/'report.json').write_text(json.dumps(dict(manifest=manifest,baseline_eligible=False)))
            observations=[dict(ns=n*1_000_000_000,peers=[dict(title='Tank Battles FPS '+w,visible=True,hidden=False,grouped=[]) for w in 'AB']) for n in range(9,72)]
            path=out/'visibility.jsonl'
            observations[30]['peers'][1]['hidden']=True
            path.write_text(''.join(json.dumps(r)+'\n' for r in observations))
            args=['--output',str(out),'--observations',str(path),'--verified-live-at-end']
            with self.assertRaisesRegex(ValueError,'Hidden or grouped'):review(args)
            observations[30]['peers'][1]['hidden']=False
            path.write_text(''.join(json.dumps(r)+'\n' for r in observations))
            review(args)
            result=json.loads((out/'report.json').read_text())
            self.assertTrue(result['paired_same_host_eligible'])
            self.assertFalse(result['baseline_eligible'])


    def test_capture_filters_credentials_and_remains_pending_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);exe=root/'player';exe.write_bytes(b'fixture')
            timing=root/'timing.jsonl'
            timing.write_text(json.dumps(self.rows()[0])+'\n')
            spec=dict(diagnostics_active=[],sdk_verbose=False,input_source='fixture',clock_domain='same-host-monotonic',map='fixture',render_scale=100,fxaa=False,
                      network_topology='same-host',peer_path='loopback',port_translator=True,display_backend='x11',
                      password='do-not-copy',peers=[dict(label=w,role=role,executable=str(exe),source_commit='fixture',
                      timing=str(timing),audio='fixture',window_size=[1280,720],password='do-not-copy')
                      for w,role in [('A','host'),('B','invited')]])
            path=root/'spec.json';path.write_text(json.dumps(spec));out=root/'capture'
            with patch('online_gameplay.time.monotonic_ns',side_effect=[1_000_000_000_000,1_060_000_000_000]), patch('online_gameplay.time.sleep'), redirect_stdout(StringIO()):
                capture(['--spec',str(path),'--output',str(out),'--verified-live-visible-ungrouped'])
            result=json.loads((out/'report.json').read_text())
            self.assertFalse(result['baseline_eligible'])
            self.assertFalse(result['manifest']['scene_end_verified'])
            self.assertNotIn('do-not-copy',''.join(p.read_text() for p in out.iterdir()))

if __name__=='__main__':unittest.main()
