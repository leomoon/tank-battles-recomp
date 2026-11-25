import unittest
from online_pacing_model import correction_update,receive_sample,pacing_deadline,f32

class AuditedArithmetic(unittest.TestCase):
 def test_unsigned_wrap_is_compared_as_signed_difference(self):
  self.assertEqual(receive_sample(100,0xffffff00),356)
  self.assertEqual(receive_sample(100,200,50),50)
  self.assertEqual(receive_sample(0x80000000,0),0)
 def test_peak_decays_only_after_selecting_maximum(self):
  peak,write=correction_update(8000,12000)
  self.assertEqual((peak,write),(6000,6000))
  peak,write=correction_update(peak,0)
  self.assertEqual((peak,write),(3000,3000))
  self.assertEqual(correction_update(512,0),(0,None))
  self.assertEqual(correction_update(513,0),(256,256))
  self.assertEqual(correction_update(12000,0,True),(0,None))
  self.assertEqual(correction_update(12000,0,clock_ready=False),(0,None))
 def test_deadline_uses_full_unsigned_adjustment_and_float_rounding(self):
  bits=0x3c888889
  self.assertEqual(pacing_deadline(0,bits,0),f32(16666.66753590107))
  self.assertEqual(pacing_deadline(1_000_000,bits,5000),f32(f32(1_016_666.6675359011)+5000))
  self.assertGreater(pacing_deadline(0,bits,0xffffffff),4_000_000_000)

if __name__=='__main__':unittest.main()
