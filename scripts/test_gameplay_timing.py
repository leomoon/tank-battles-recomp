import json,subprocess,tempfile,unittest
from pathlib import Path
from gameplay_report import summarize,compare,COMPAT
from port import ROOT

class GameplayTimingTests(unittest.TestCase):
 def test_real_cadence_deduplicates_refreshes_and_stale_serials(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp);source=path/'test.cpp';source.write_text('''#include "gameplay_timing.h"
#include <cassert>
int main(){GameplayCadence c;for(unsigned i=0;i<60;i++){if(i%3==0)c.flip(i*16666667ull);c.present(i*16666667ull,c.guest);}assert(c.guest==20&&c.completed==20&&c.presents==60);c.present(2000000000,2);assert(c.completed==20);c.flip(2100000000);c.present(2200000000,c.guest);assert(c.completed==21);assert(c.events[1].back()==2200000000);}''')
   subprocess.run(['c++','-std=c++20','-I'+str(ROOT),str(source),'-o',str(path/'test')],check=True);subprocess.run([str(path/'test')],check=True)
 def fixture(self,path):
  m={k:'synthetic' for k in COMPAT};m.update(strict_passed=True,live_gameplay_verified=True,phase='gameplay',interval_seconds=[1,61],visible_entire_interval=True,diagnostics={},sdk_verbose=False)
  (path/'measurement.json').write_text(json.dumps(m))
  rows=[dict(elapsed_ns=t*1000000000,events=[[int((t-1+i/60)*1e9) for i in range(60)]]*3) for t in range(1,63)]
  (path/'gameplay.jsonl').write_text('\n'.join(map(json.dumps,rows)));return m
 def test_precise_rates_and_stall_percentiles(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p);r=summarize(p);self.assertEqual(r['cadence']['completed_frame']['fps'],60);self.assertAlmostEqual(r['cadence']['completed_frame']['p95_ms'],1000/60,places=4)
 def test_rejects_hidden_briefing_failed_short_and_heavy_intervals(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);base=self.fixture(p)
   for field,value in [('session',dict(launcher_exit_status=86)),('session',dict(launcher_exit_status=0,interrupted=True)),('strict_passed',False),('live_gameplay_verified',False),('phase','briefing'),('interval_seconds',[1,30]),('visible_entire_interval',False),('diagnostics',dict(video=True)),('interval_seconds',[1,70])]:
    m=dict(base);m[field]=value;(p/'measurement.json').write_text(json.dumps(m))
    with self.assertRaises(ValueError):summarize(p)
 def test_stall_is_retained_in_exact_intervals(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p)
   rows=[json.loads(l) for l in (p/'gameplay.jsonl').read_text().splitlines()]
   for row in rows:
    for stream in row['events']:
     for i,value in enumerate(stream):
      if value>=30000000000:stream[i]+=1000000000
    if row['elapsed_ns']>=30000000000:row['elapsed_ns']+=1000000000
   (p/'gameplay.jsonl').write_text('\n'.join(map(json.dumps,rows)))
   r=summarize(p);self.assertGreater(r['cadence']['completed_frame']['max_ms'],1000);self.assertLess(r['cadence']['completed_frame']['fps'],60)
 def test_nanosecond_boundaries_do_not_reject_float_rounding(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);m=self.fixture(p);m['interval_seconds']=[1.000000123,61.000000123]
   (p/'measurement.json').write_text(json.dumps(m));self.assertEqual(summarize(p)['cadence']['completed_frame']['fps'],60)
 def test_visual_scene_rejection_overrides_earlier_manifest(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p);(p/'invalid-scene.json').write_text('{}')
   with self.assertRaises(ValueError):summarize(p)
 def test_verbose_or_unknown_logging_is_not_a_clean_baseline(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);m=self.fixture(p);self.assertTrue(summarize(p)['baseline_eligible'])
   for value in (True,None):
    m['sdk_verbose']=value;(p/'measurement.json').write_text(json.dumps(m));r=summarize(p);self.assertFalse(r['baseline_eligible'])
    with self.assertRaises(ValueError):compare([r])
 def test_overlay_variant_must_be_explicit(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p);r=summarize(p);variant=dict(r,manifest=dict(r['manifest'],overlay=False))
   with self.assertRaises(ValueError):compare([r,variant])
   self.assertEqual(len(compare([r,variant],allowed_variants=('overlay',))),2)
   with self.assertRaises(ValueError):compare([r,variant],allowed_variants=('scene',))
 def test_rejects_incompatible_scene_or_build(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p);r=summarize(p)
   for key in COMPAT:
    changed=dict(r,manifest=dict(r['manifest']));changed['manifest'][key]='changed'
    with self.assertRaises(ValueError):compare([r,changed])
if __name__=='__main__':unittest.main()
