import json,tempfile,unittest
from pathlib import Path
from online_pacing_audit import audit
from online_pacing_model import pacing_deadline

class AuditTests(unittest.TestCase):
 def fixture(self,root):
  events=[dict(ns=110,kind=6,previous_guest_us=100,observed_guest_us=1300,correction_u32=1200),
          dict(ns=120,kind=7,previous_guest_us=1000,observed_guest_us=1200,correction_u32=1200),
          dict(ns=130,kind=3,correction_u32=600),
          dict(ns=140,kind=2,previous_guest_us=1000,interval_bits=0x3c888889,correction_u32=600,deadline_guest_us=pacing_deadline(1000,0x3c888889,600)),
          dict(ns=150,kind=9,correction_u32=0)]
  header=dict(schema=1,clock='host_monotonic_ns',events=len(events),complete=True,dropped=0,start_ns=100,end_ns=200)
  path=Path(root)/'trace.jsonl';path.write_text('\n'.join(json.dumps(x) for x in [header,*events])+'\n');return path,header,events
 def test_audited_chain_and_deadline_match(self):
  with tempfile.TemporaryDirectory() as root:
   path,_,_=self.fixture(root);r=audit(path)
   self.assertTrue(r['arithmetic_valid']);self.assertEqual(r['correction_writes_matching_peak_half'],1);self.assertEqual(r['reset_events'],1)
 def test_wrong_arithmetic_is_reported_and_dropped_trace_is_rejected(self):
  with tempfile.TemporaryDirectory() as root:
   path,h,events=self.fixture(root);events[2]['correction_u32']=123
   path.write_text('\n'.join(json.dumps(x) for x in [h,*events]))
   self.assertFalse(audit(path)['arithmetic_valid'])
   h['dropped']=1;path.write_text('\n'.join(json.dumps(x) for x in [h,*events]))
   with self.assertRaisesRegex(ValueError,'lossless'):audit(path)
   self.assertFalse(audit(path,allow_dropped=True)['lossless'])
 def test_phase_boundaries_and_clock_domain_are_checked(self):
  with tempfile.TemporaryDirectory() as root:
   path,h,events=self.fixture(root)
   with self.assertRaisesRegex(ValueError,'Phase outside'):audit(path,[dict(start_ns=90,end_ns=160)])
   h['clock']='guest_us';path.write_text('\n'.join(json.dumps(x) for x in [h,*events]))
   with self.assertRaisesRegex(ValueError,'clock'):audit(path)

if __name__=='__main__':unittest.main()
