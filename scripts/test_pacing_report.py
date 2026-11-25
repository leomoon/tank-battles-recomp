import json,tempfile,unittest
from pathlib import Path
from pacing_report import report

class PacingReportTests(unittest.TestCase):
 def write(self,root,**changes):
  header=dict(schema=1,clock='host_monotonic_ns',events=1,start_ns=100,end_ns=200,complete=True,dropped=0)
  header.update(changes)
  event=dict(ns=150,kind=3,correction_u32=1000)
  path=Path(root)/'trace.jsonl';path.write_text(json.dumps(header)+'\n'+json.dumps(event)+'\n');return path
 def test_correction_only_trace_does_not_invent_pacing_observations(self):
  with tempfile.TemporaryDirectory() as root:
   value=report(self.write(root))
   self.assertEqual(value['correction_us']['mean'],1000)
   self.assertIsNone(value['deadline_lateness_guest_us'])
   self.assertTrue(value['usable_timeline'])
 def test_dropped_and_interrupted_traces_are_not_complete_timelines(self):
  with tempfile.TemporaryDirectory() as root:
   self.assertFalse(report(self.write(root,dropped=1))['usable_timeline'])
   self.assertFalse(report(self.write(root,complete=False))['usable_timeline'])
 def test_truncated_trace_and_wrong_clock_rejected(self):
  with tempfile.TemporaryDirectory() as root:
   with self.assertRaisesRegex(ValueError,'Incomplete'):report(self.write(root,events=2))
   with self.assertRaisesRegex(ValueError,'schema'):report(self.write(root,clock='guest_clock'))


 def test_frame_correlation_requires_an_exact_clock_mapping(self):
  with tempfile.TemporaryDirectory() as root:
   path=self.write(root)
   budget=Path(root)/'budget.trace';budget.write_text('# ps3_ftrace dropped=0\n8 1 0 0 0.000 0.000\n8 1 0 0 0.100 0.100\n')
   log=Path(root)/'startup.log';log.write_text('[frame-budget] base_monotonic_ns=100\n')
   result=report(path,budget,log)['frame_correlation']
   self.assertEqual(result['base_monotonic_ns'],100)
   self.assertEqual(result['events_inside_complete_frames'],1)
   budget.write_text('# ps3_ftrace dropped=0\n8 1 0 0 100.000 100.000\n8 1 0 0 200.000 200.000\n')
   with self.assertRaisesRegex(ValueError,'overlap'):report(path,budget,log)
   log.write_text('')
   with self.assertRaisesRegex(ValueError,'clock mapping'):report(path,budget,log)

if __name__=='__main__':unittest.main()
