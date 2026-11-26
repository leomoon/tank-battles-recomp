import json,tempfile,unittest
from pathlib import Path
from pacing_report import report, frame_accounting

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

class FrameAccountingTests(unittest.TestCase):
 def test_clock_mapping_and_exclusive_overlap(self):
  base=1000000000
  # A 20 ms frame; pacing covers [2,10], semaphore [8,15].
  events=[dict(kind=1,object=1,ns=base+2000000),
          dict(kind=2,object=1,ns=base+10000000,correction_u32=2000,interval_bits=0x3c888889,flags=0),
          dict(kind=9,object=1,ns=base+16000000),dict(kind=3,ns=base+21000000)]
  events.insert(0,dict(kind=3,ns=base-1))
  spans=[(8,0,0,0,0.,0.),(8,0,0,0,20.,20.),(37,1,1,0,8.,15.),
         (37,2,1,0,0.,20.)] # other thread must not enter main accounting
  result=frame_accounting(events,spans,base)
  row=result['timeline'][0]
  self.assertEqual(result['frames'],1)
  self.assertEqual([row[k] for k in ('pacing_only_ms','semaphore_only_ms','overlapping_wait_ms','remaining_ms')],[6.,5.,2.,7.])
  self.assertEqual(sum(row[k] for k in ('pacing_only_ms','semaphore_only_ms','overlapping_wait_ms','remaining_ms')),row['frame_ms'])
  self.assertEqual(result['reset_count'],1)
  self.assertIsNone(result['zero_correction_frames'])
 def test_incomplete_windows_and_reset_do_not_invent_waits(self):
  events=[dict(kind=3,ns=0),dict(kind=1,object=1,ns=1),dict(kind=9,object=1,ns=2),
          dict(kind=2,object=1,ns=3,correction_u32=0,interval_bits=0x3c888889,flags=0),dict(kind=3,ns=20000000)]
  result=frame_accounting(events,[(8,0,0,0,0.,0.),(8,0,0,0,10.,10.),(8,0,0,0,30.,30.)],0)
  self.assertEqual(result['frames'],1)
  self.assertEqual(result['exclusive_ms']['pacing_only_ms']['mean'],0)
  self.assertEqual(result['zero_correction_frames']['mean'],10.)

if __name__=='__main__':unittest.main()
