import json,subprocess,tempfile,unittest,os,shutil,sys
from pathlib import Path
from gameplay_report import summarize,compare,COMPAT,renderer_failures
from port import ROOT

class GameplayTimingTests(unittest.TestCase):
 def test_pgo_training_cannot_be_a_clean_baseline(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);m=self.fixture(p);m['diagnostics']['pgo_training']=True
   (p/'measurement.json').write_text(json.dumps(m))
   report=summarize(p)
   self.assertFalse(report['baseline_eligible'])
   with self.assertRaisesRegex(ValueError,'clean baselines'):compare([report,report])
 def test_progress_watch_rejects_a_stopped_stream_only_during_timing(self):
  from gameplay_report import FrameProgress
  progress=FrameProgress()
  self.assertIsNone(progress.observe(1,0,False))
  self.assertIsNone(progress.observe(1,20_000_000_000,True))
  # Starting a measured interval after a held menu must reset the watchdog.
  self.assertIsNone(progress.observe(2,21_000_000_000,True))
  self.assertIsNone(progress.observe(2,25_999_999_999,True))
  self.assertEqual(progress.observe(2,26_000_000_000,True)['completed_frames'],2)
  self.assertIsNone(progress.observe(3,27_000_000_000,True))
  self.assertIsNone(progress.observe(3,40_000_000_000,False))
 def test_raw_spu_comparison_requires_explicit_matched_modes(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p);r=summarize(p)
   a=dict(r,manifest=dict(r['manifest'],raw_spu_idle='0'))
   b=dict(r,manifest=dict(r['manifest'],raw_spu_idle='1'))
   with self.assertRaisesRegex(ValueError,'Incompatible raw_spu_idle'):compare([a,b])
   self.assertEqual(len(compare([a,b],allowed_variants=('raw_spu_idle',))),2)
   with self.assertRaisesRegex(ValueError,'explicit polling'):compare([a,a],allowed_variants=('raw_spu_idle',))
   changed=dict(b,manifest=dict(b['manifest'],render_scale=75))
   with self.assertRaisesRegex(ValueError,'Incompatible render_scale'):compare([a,changed],allowed_variants=('raw_spu_idle',))
 def test_changed_overlay_rejects_an_otherwise_clean_interval(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp);manifest=self.fixture(path);manifest['overlay_consistent']=False
   (path/'measurement.json').write_text(json.dumps(manifest))
   with self.assertRaisesRegex(ValueError,'Overlay setting changed'):summarize(path)
 def test_display_settings_mismatch_rejects_comparison(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p);r=summarize(p)
   base=dict(r,manifest=dict(r['manifest'],render_scale=100,window_mode='windowed',fxaa=False))
   for field,value in [('render_scale',150),('window_mode','borderless'),('fxaa',True),('raw_spu_idle','1')]:
    changed=dict(base,manifest=dict(base['manifest'],**{field:value}))
    with self.assertRaisesRegex(ValueError,'Incompatible '+field):compare([base,changed])
 def test_backend_resource_lifetime_failures_reject_measurement(self):
  refused='[RSX vulkan] engine: stale texture release of object 352 (kind 1, 2048x2048) refused'
  full='[RSX vulkan] engine: object table full (2048)'
  self.assertEqual(renderer_failures(refused+'\n'+full),[refused,full])
  self.assertEqual(len(renderer_failures((full+'\n')*100)),64)
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);m=self.fixture(p);m['renderer_failures']=renderer_failures(refused)
   (p/'measurement.json').write_text(json.dumps(m))
   with self.assertRaisesRegex(ValueError,'Renderer failure'):summarize(p)
   m['renderer_failures']=[];(p/'measurement.json').write_text(json.dumps(m))
   (p/'startup.log').write_text(full)
   with self.assertRaisesRegex(ValueError,'retained startup log'):summarize(p)
 def test_fifo_resync_is_a_failed_gameplay_run(self):
  text='[cellGcmSys] FIFO walker circling (get=0x005C9650, put=0x005D3C3C)\n[cellGcmSys] FIFO resync (circling) 0x005C9650 -> put 0x005D3C3C'
  self.assertEqual(len(renderer_failures(text)),2)
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p);(p/'startup.log').write_text(text)
   with self.assertRaisesRegex(ValueError,'retained startup log'):summarize(p)
 def test_real_cadence_deduplicates_refreshes_and_stale_serials(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp);source=path/'test.cpp';source.write_text('''#include "gameplay_timing.h"
#include <cassert>
int main(){GameplayCadence c;for(unsigned i=0;i<60;i++){if(i%3==0)c.flip(i*16666667ull);c.present(i*16666667ull,c.guest);}assert(c.guest==20&&c.completed==20&&c.presents==60);c.present(2000000000,2);assert(c.completed==20);c.flip(2100000000);c.present(2200000000,c.guest);assert(c.completed==21);assert(c.events[1].back()==2200000000);}''')
   if os.name=='nt':
    sys.path.insert(0,str(ROOT/'external/ps3recomp/tools'))
    from msvc_env import environ,find_vcvars
    env=environ();compiler=shutil.which('clang-cl',path=env['PATH'])
    if not compiler:
     compiler=str(Path(find_vcvars()).parents[2]/'Tools/Llvm/x64/bin/clang-cl.exe')
    target=path/'test.exe'
    subprocess.run([compiler,'/std:c++20','/EHsc','/I'+str(ROOT),str(source),'/Fe:'+str(target),'/Fo:'+str(path/'test.obj')],env=env,check=True,capture_output=True)
   else:
    target=path/'test';subprocess.run(['c++','-std=c++20','-I'+str(ROOT),str(source),'-o',str(target)],check=True)
   subprocess.run([str(target)],check=True)
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
 def test_pending_review_runs_report_timing_but_are_never_eligible(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);m=self.fixture(p);m.update(live_gameplay_verified=False,scene_evidence=dict(pending_review=True))
   (p/'measurement.json').write_text(json.dumps(m))
   r=summarize(p);self.assertFalse(r['baseline_eligible']);self.assertIn('pending',r['scene_verification'])
   self.assertEqual(r['cadence']['completed_frame']['fps'],60)
   with self.assertRaises(ValueError):compare([r,r])
   m['scene_evidence']=dict(pending_review=False);(p/'measurement.json').write_text(json.dumps(m))
   with self.assertRaises(ValueError):summarize(p)
 def test_short_profiles_cannot_become_clean_baselines(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);m=self.fixture(p);m['interval_seconds']=[1,21]
   (p/'measurement.json').write_text(json.dumps(m))
   with self.assertRaises(ValueError):summarize(p,minimum_seconds=20)
   m['diagnostics']={'scopes':True};(p/'measurement.json').write_text(json.dumps(m))
   r=summarize(p,minimum_seconds=20);self.assertFalse(r['baseline_eligible'])
   with self.assertRaises(ValueError):compare([r])
   with self.assertRaises(ValueError):summarize(p)
   with self.assertRaises(ValueError):summarize(p,minimum_seconds=0)
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
 def test_quick_run_is_never_a_baseline(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);m=self.fixture(p);m['quick_bench']=True
   (p/'measurement.json').write_text(json.dumps(m))
   report=summarize(p);self.assertFalse(report['baseline_eligible'])
   with self.assertRaises(ValueError):compare([report])
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
 def test_declared_candidate_identity_cannot_relax_scene_or_quality(self):
  from build_identity import BUILD_FIELDS
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p);a=summarize(p);b=dict(a,manifest=dict(a['manifest']))
   b['manifest'].update(executable_sha256='candidate',submission_mode='grouped')
   plan=dict(reason='bounded draw groups',variants={role:{k:r['manifest'].get(k) for k in BUILD_FIELDS} for role,r in [('baseline',a),('candidate',b)]})
   self.assertEqual(len(compare([a,b],candidate_plan=plan)),2)
   with self.assertRaises(ValueError):compare([a,b])
   for field in ('scene','guest_resolution','internal_surfaces','audio','diagnostics','input_source','overlay'):
    changed=dict(b,manifest=dict(b['manifest']));changed['manifest'][field]='changed'
    with self.assertRaises(ValueError):compare([a,changed],candidate_plan=plan)
   changed=dict(b,manifest=dict(b['manifest'],executable_sha256='undeclared'))
   with self.assertRaises(ValueError):compare([a,changed],candidate_plan=plan)
   with self.assertRaises(ValueError):compare([b],candidate_plan=plan)
 def test_depth_variant_preserves_other_manifest_guards(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p);r=summarize(p)
   a=dict(r,manifest=dict(r['manifest'],depth_conversion='reference'))
   b=dict(r,manifest=dict(r['manifest'],depth_conversion='gpu'))
   with self.assertRaises(ValueError):compare([a,b])
   self.assertEqual(len(compare([a,b],allowed_variants=('depth_conversion',))),2)
   with self.assertRaises(ValueError):compare([b],allowed_variants=('depth_conversion',))
   for field in ('scene','guest_resolution','internal_surfaces','audio','diagnostics','input_source','overlay','executable_sha256'):
    c=dict(b,manifest=dict(b['manifest']));c['manifest'][field]='changed'
    with self.assertRaises(ValueError):compare([a,c],allowed_variants=('depth_conversion',))
 def test_color_readback_variant_preserves_other_manifest_guards(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p);r=summarize(p)
   a=dict(r,manifest=dict(r['manifest'],color_readback_mode='reference'))
   b=dict(r,manifest=dict(r['manifest'],color_readback_mode='cached'))
   with self.assertRaises(ValueError):compare([a,b])
   self.assertEqual(len(compare([a,b],allowed_variants=('color_readback_mode',))),2)
   with self.assertRaises(ValueError):compare([b],allowed_variants=('color_readback_mode',))
   for field in ('scene','guest_resolution','internal_surfaces','audio','diagnostics','input_source','overlay','executable_sha256'):
    c=dict(b,manifest=dict(b['manifest']));c['manifest'][field]='changed'
    with self.assertRaises(ValueError):compare([a,c],allowed_variants=('color_readback_mode',))
 def test_draw_remap_variant_preserves_other_manifest_guards(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p);r=summarize(p)
   a=dict(r,manifest=dict(r['manifest'],draw_remap_mode='reference'))
   b=dict(r,manifest=dict(r['manifest'],draw_remap_mode='cached'))
   with self.assertRaises(ValueError):compare([a,b])
   self.assertEqual(len(compare([a,b],allowed_variants=('draw_remap_mode',))),2)
   with self.assertRaises(ValueError):compare([b],allowed_variants=('draw_remap_mode',))
   for field in ('scene','guest_resolution','internal_surfaces','audio','diagnostics','input_source','overlay','executable_sha256'):
    c=dict(b,manifest=dict(b['manifest']));c['manifest'][field]='changed'
    with self.assertRaises(ValueError):compare([a,c],allowed_variants=('draw_remap_mode',))
 def test_fence_wait_variant_preserves_other_manifest_guards(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p);r=summarize(p)
   a=dict(r,manifest=dict(r['manifest'],fence_wait_mode='blocking'))
   b=dict(r,manifest=dict(r['manifest'],fence_wait_mode='poll'))
   with self.assertRaises(ValueError):compare([a,b])
   self.assertEqual(len(compare([a,b],allowed_variants=('fence_wait_mode',))),2)
   with self.assertRaises(ValueError):compare([b],allowed_variants=('fence_wait_mode',))
   for field in ('scene','guest_resolution','internal_surfaces','audio','diagnostics','input_source','overlay','executable_sha256'):
    c=dict(b,manifest=dict(b['manifest']));c['manifest'][field]='changed'
    with self.assertRaises(ValueError):compare([a,c],allowed_variants=('fence_wait_mode',))
 def test_decoder_variant_is_explicit_and_does_not_relax_other_settings(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p);r=summarize(p)
   a=dict(r,manifest=dict(r['manifest'],texture_decode_mode='reference'))
   b=dict(r,manifest=dict(r['manifest'],texture_decode_mode='specialized'))
   with self.assertRaises(ValueError):compare([a,b])
   self.assertEqual(len(compare([a,b],allowed_variants=('texture_decode_mode',))),2)
   with self.assertRaises(ValueError):compare([b],allowed_variants=('texture_decode_mode',))
   for field in ('scene','guest_resolution','internal_surfaces','audio','diagnostics','input_source','overlay','executable_sha256'):
    changed=dict(b,manifest=dict(b['manifest']));changed['manifest'][field]='changed'
    with self.assertRaises(ValueError):compare([a,changed],allowed_variants=('texture_decode_mode',))
 def test_rejects_incompatible_scene_or_build(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);self.fixture(p);r=summarize(p)
   for key in COMPAT:
    changed=dict(r,manifest=dict(r['manifest']));changed['manifest'][key]='changed'
    with self.assertRaises(ValueError):compare([r,changed])
if __name__=='__main__':unittest.main()
