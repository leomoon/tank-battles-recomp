import json
import tempfile
import unittest
from pathlib import Path
from online_clock_report import report


class ClockReport(unittest.TestCase):
    def write(self,root,events,**changes):
        header=dict(schema=1,clock='host_monotonic_ns',start_ns=0,end_ns=1000,
                    events=len(events),dropped=0,complete=True)
        header.update(changes)
        path=Path(root)/'clock.jsonl'
        path.write_text('\n'.join(json.dumps(r) for r in [header,*events])+'\n')
        return path

    def sample(self,count,now,remote,rtt,adjustment):
        return dict(ns=count,kind=10,object=1,socket=2,local_sequence=count,
                    observed_guest_us=now,interval_bits=remote,result_u32=rtt,
                    correction_u32=adjustment&0xffffffff)

    def test_wrapped_clocks_and_negative_offsets(self):
        with tempfile.TemporaryDirectory() as root:
            events=[self.sample(i,0xfffffff0,4,40,40) for i in range(1,11)]
            result=report(self.write(root,events));series=result['series'][0]
            self.assertTrue(series['complete_ten_samples'])
            self.assertTrue(all(r['original_arithmetic_matches'] and r['inside_current_response_bounds'] for r in series['samples']))
            row=report(self.write(root,[self.sample(1,100,40,20,-50)]))['series'][0]['samples'][0]
            self.assertEqual(row['adjustment_us'],-50)
            self.assertTrue(row['inside_current_response_bounds'])

    def test_average_from_large_previous_delay_can_exceed_current_bounds(self):
        with tempfile.TemporaryDirectory() as root:
            events=[self.sample(1,10000,10000,20000,10000),
                    self.sample(2,20000,20000,40,5010)]
            rows=report(self.write(root,events))['series'][0]['samples']
            self.assertTrue(rows[1]['original_arithmetic_matches'])
            self.assertFalse(rows[1]['inside_current_response_bounds'])

    def test_missing_samples_drops_and_clock_domains_are_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            event=self.sample(2,100,100,40,20)
            result=report(self.write(root,[event],dropped=1,complete=False))
            self.assertFalse(result['lossless'])
            self.assertFalse(result['usable_timeline'])
            self.assertFalse(result['series'][0]['complete_ten_samples'])
            self.assertIsNone(result['series'][0]['samples'][0]['original_arithmetic_matches'])
            with self.assertRaisesRegex(ValueError,'clock'):
                report(self.write(root,[],clock='guest_us'))

    def test_interleaved_transports_and_new_exchange_reset_series(self):
        with tempfile.TemporaryDirectory() as root:
            first=self.sample(1,100,100,40,20)
            other=dict(first,object=3,ns=2)
            second=self.sample(2,200,200,40,20)
            second['ns']=3
            reset=dict(first,ns=4)
            result=report(self.write(root,[first,other,second,reset]))
            self.assertEqual([len(s['samples']) for s in result['series']],[2,1,1])
            self.assertTrue(all(s['continuous'] for s in result['series']))


if __name__=='__main__':unittest.main()
