"""Synthetic contracts for Windows build discovery and owned-window observations."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from build_identity import measurement_build, read_command_log


class BuildTests(unittest.TestCase):
    def test_interval_arming_tolerates_concurrent_manifest_write(self):
        from script_gameplay import wait_for_interval
        process=Mock();process.poll.return_value=None
        armed=dict(interval_seconds=[5,65])
        with patch.object(Path,'read_text',side_effect=['',PermissionError('writer'),
                json.dumps(dict(interval_seconds=[])),json.dumps(armed)]),patch('script_gameplay.time.sleep'):
            self.assertEqual(wait_for_interval(Path('unused'),process),armed)
        process.poll.return_value=1
        with self.assertRaisesRegex(RuntimeError,'before arming'):
            wait_for_interval(Path('unused'),process)

    def test_visual_capture_acceptance_requires_both_matching_live_reviews(self):
        from script_gameplay import validate_visual_boundaries
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            for label,value in [('gameplay-before',120),('gameplay-after',54)]:
                data=label.encode();(out/(label+'.png')).write_bytes(data)
                (out/(label+'-review.json')).write_text(json.dumps(dict(image_sha256=hashlib.sha256(data).hexdigest(),
                    live_gameplay=True,countdown=value,reviewer='fixture',overlay_visible=True)))
            self.assertEqual(validate_visual_boundaries(out,True)['gameplay-after'],54)
            with self.assertRaises(OSError):validate_visual_boundaries(out,True,require_receipt=True)
            receipt=dict(review_sha256={label:hashlib.sha256((out/(label+'-review.json')).read_bytes()).hexdigest()
                for label in ('gameplay-before','gameplay-after')})
            (out/'visual-boundaries-verified.json').write_text(json.dumps(receipt))
            self.assertEqual(validate_visual_boundaries(out,True,require_receipt=True)['gameplay-after'],54)
            (out/'visual-boundaries-verified.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'receipt mismatch'):validate_visual_boundaries(out,True,require_receipt=True)
            with self.assertRaisesRegex(ValueError,'Invalid/stale'):validate_visual_boundaries(out,False)
            review=out/'gameplay-after-review.json'
            original=review.read_text()
            answer=json.loads(original)
            answer['live_gameplay']=False
            review.write_text(json.dumps(answer))
            with self.assertRaisesRegex(ValueError,'Invalid/stale'):validate_visual_boundaries(out,True)
            answer['live_gameplay']=True
            answer['countdown']=121
            review.write_text(json.dumps(answer))
            with self.assertRaisesRegex(ValueError,'Invalid/stale'):validate_visual_boundaries(out,True)
            review.write_text(original)
            before=out/'gameplay-before-review.json'
            answer=json.loads(before.read_text());answer['countdown']=53
            before.write_text(json.dumps(answer))
            with self.assertRaisesRegex(ValueError,'countdown increased'):validate_visual_boundaries(out,True)
            answer['countdown']=120;before.write_text(json.dumps(answer))
            (out/'gameplay-after.png').write_bytes(b'changed capture')
            with self.assertRaisesRegex(ValueError,'Invalid/stale'):validate_visual_boundaries(out,True)
            (out/'gameplay-after-review.json').unlink()
            with self.assertRaises(OSError):validate_visual_boundaries(out,True)
    def test_manual_countdown_is_a_fallback_for_unreadable_ocr(self):
        from script_gameplay import boundary_countdown
        process=Mock()
        self.assertEqual(boundary_countdown(Path('unused'),'gameplay-before','120',True,process),'120')
        process.poll.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);(out/'gameplay-before.png').write_bytes(b'boundary')
            process.poll.return_value=0
            with self.assertRaisesRegex(RuntimeError,'Boundary review not supplied'):
                boundary_countdown(out,'gameplay-before','unreadable',True,process)
            self.assertTrue((out/'gameplay-before-review-request.json').is_file())

    def test_campaign_briefing_requires_objective_and_continue_when_heading_is_lost(self):
        from script_gameplay import first_campaign_briefing, invalid_ending_scene
        self.assertTrue(first_campaign_briefing('CONTROLS: left stick MISSION: destroy all enemy units. CONTINUE'))
        self.assertTrue(first_campaign_briefing('ujxrnols: left stick MISSION: DESTROY ALL ENEMY units. CONTINUE'))
        self.assertFalse(first_campaign_briefing('MISSION: DESTROY ALL ENEMY units.'))
        self.assertFalse(first_campaign_briefing('CONTINUE SELECT MISSION'))
        self.assertFalse(first_campaign_briefing('MISSION FAILED CONTINUE'))
        self.assertTrue(invalid_ending_scene('ujxrnols: MISSION: DESTROY ALL ENEMY units. CONTINUE'))
        self.assertFalse(invalid_ending_scene('PLAYER 1 120'))

    def test_low_resolution_map_ocr_keeps_map_and_pack_evidence(self):
        from script_gameplay import default_battle_map
        self.assertTrue(default_battle_map('SEECT MAP PACK 1 COUNTRY MIPS FIELD OF WAR 1 COWR FIELDS CROSSOVER PACK 2 MILITARY X ZONE'))
        self.assertTrue(default_battle_map('SELECT MAP FIELD OF WAR 1 CONFIRM BACK'))
        self.assertFalse(default_battle_map('FIELD OF WAR 1'))
        self.assertFalse(default_battle_map('PACK 1 PACK 2 X ZONE'))
        self.assertFalse(default_battle_map('FIELD OF WAR 1 PACK 1'))
    def test_render_scale_is_forwarded_to_native_and_repeat_commands(self):
        from script_gameplay import main
        from port import ROOT
        base=ROOT/'analysis/performance'
        base.mkdir(parents=True,exist_ok=True)
        class InterceptLaunch(Exception):pass
        with tempfile.TemporaryDirectory(dir=base,prefix='scale-forwarding-') as tmp:
            for percent in (50,75,100,150,200,300):
                output=Path(tmp)/('native-'+str(percent))
                arguments=['script_gameplay.py','--output',str(output),'--render-scale',str(percent),'--raw-spu-idle','0']
                if os.name=='nt':arguments+=['--cpu-accounting','--manual-boundaries','--visual-boundaries']
                with patch('sys.argv',arguments),patch('subprocess.Popen',side_effect=InterceptLaunch) as launch:
                    with self.assertRaises(InterceptLaunch):main()
                    command=launch.call_args.args[0]
                    self.assertEqual(command[command.index('--render-scale')+1],str(percent))
                    self.assertEqual(command[command.index('--raw-spu-idle')+1],'0')
                    if os.name=='nt':self.assertIn('--cpu-accounting',command)
                arguments[2]=str(Path(tmp)/('repeat-'+str(percent)))
                arguments+=['--repeat','2']
                with patch('sys.argv',arguments),patch('subprocess.run',return_value=Mock(returncode=0)) as launch:
                    with self.assertRaises(SystemExit) as exit_status:main()
                    self.assertEqual(exit_status.exception.code,0)
                    self.assertEqual(launch.call_count,2)
                    for call in launch.call_args_list:
                        command=call.args[0]
                        self.assertEqual(command[command.index('--render-scale')+1],str(percent))
                        self.assertEqual(command[command.index('--raw-spu-idle')+1],'0')
                        if os.name=='nt':self.assertIn('--cpu-accounting',command)
                        if os.name=='nt':self.assertIn('--visual-boundaries',command)
    def test_visual_boundaries_require_explicit_manual_review(self):
        from script_gameplay import main
        with patch('sys.argv',['script_gameplay.py','--output','unused','--visual-boundaries']):
            with self.assertRaises(SystemExit) as status:main()
            self.assertEqual(status.exception.code,2)
    def test_end_capture_uses_measurement_clock_not_harness_wait_start(self):
        from script_gameplay import interval_end_ns
        manifest=dict(interval_seconds=[15.,75.])
        self.assertEqual(interval_end_ns(manifest,dict(monotonic_ns=120_000_000_000,elapsed_ns=20_000_000_000)),175_000_000_000)
        self.assertEqual(interval_end_ns(manifest,dict(monotonic_ns=140_000_000_000,elapsed_ns=40_000_000_000)),175_000_000_000)
    def test_timer_ocr_requires_unique_word_inside_hud_timer(self):
        from script_gameplay import windows_hud_countdown
        def word(text,x=615,y=75):
            return dict(text=text,x=x,y=y,width=42,height=24)
        def ocr(*words):
            return dict(lines=[dict(words=list(words))])
        self.assertEqual(windows_hud_countdown(ocr(word('115'),word('100',1100)),[1280,720]),'115')
        self.assertIsNone(windows_hud_countdown(ocr(word('115',1100)),[1280,720]))
        self.assertIsNone(windows_hud_countdown(ocr(word('150')),[1280,720]))
        self.assertIsNone(windows_hud_countdown(ocr(word('115'),word('114')),[1280,720]))
        self.assertIsNone(windows_hud_countdown(ocr(word('115',615,50)),[1280,720]))

    def test_manual_boundary_rejects_stale_or_missing_scene_proof(self):
        from script_gameplay import boundary_countdown
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);image=out/'gameplay-before.png';image.write_bytes(b'synthetic image')
            digest=hashlib.sha256(image.read_bytes()).hexdigest()
            process=Mock();process.poll.return_value=None
            for invalid in ({},dict(image_sha256='old',live_gameplay=True,countdown=115,reviewer='test'),dict(image_sha256=digest,live_gameplay=False,countdown=115,reviewer='test')):
                (out/'gameplay-before-review.json').write_text(json.dumps(invalid))
                with self.assertRaises(ValueError):boundary_countdown(out,'gameplay-before','',True,process)
            (out/'gameplay-before-review.json').write_text(json.dumps(dict(image_sha256=digest,live_gameplay=True,countdown=115,reviewer='test',overlay_visible=False)))
            self.assertEqual(boundary_countdown(out,'gameplay-before','',True,process),'115')
            valid=(out/'gameplay-before-review.json').read_text()
            with patch.object(Path,'read_text',side_effect=[PermissionError('writer active'),json.JSONDecodeError('partial','',0),valid]),patch('script_gameplay.time.sleep'):
                self.assertEqual(boundary_countdown(out,'gameplay-before','',True,process),'115')
            with self.assertRaises(ValueError):
                boundary_countdown(out,'gameplay-before','',True,process,expected_overlay=True)

    def test_bom_and_bomless_command_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'commands.tlog'
            for encoding in ('utf-16','utf-16-le','utf-8'):
                p.write_bytes('^ppu_recomp_000.cpp\n/O2 /c file.cpp'.encode(encoding))
                self.assertIn('/O2',read_command_log(p))

    def test_multiconfig_uses_artifact_compiler_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);build=root/'build';runtime=root/'bundle'
            (build/'Release').mkdir(parents=True);runtime.mkdir()
            (build/'Release/TankBattlesRecomp.exe').write_bytes(b'synthetic')
            (build/'CMakeCache.txt').write_text('TB_RUNTIME_ROOT:PATH='+str(runtime)+'\nCMAKE_CONFIGURATION_TYPES:STRING=Debug;Release\n')
            (runtime/'manifest.json').write_text(json.dumps(dict(source_commit='fixture-source',build=dict(compiler_id='Clang',compiler_version='fixture'))))
            info=measurement_build(build)
            self.assertEqual(info['build_type'],'Release')
            self.assertEqual(info['compiler'],'Clang fixture')
            self.assertEqual(info['runtime_source_commit'],'fixture-source')
            self.assertEqual(info['executable'].name,'TankBattlesRecomp.exe')
            self.assertEqual(info['title_pgo'],'')
            with (build/'CMakeCache.txt').open('a') as stream:
                stream.write('TB_TITLE_PGO:STRING=generate\n')
            self.assertEqual(measurement_build(build)['title_pgo'],'generate')


@unittest.skipUnless(os.name=='nt','Windows window API contracts')
class WindowTests(unittest.TestCase):
    def test_recycled_window_cannot_be_closed_or_captured(self):
        from windows_gameplay import WindowsDesktop
        desktop=WindowsDesktop()
        with patch.object(desktop,'owner',return_value=456):
            with self.assertRaisesRegex(ValueError,'ownership'):
                desktop.close(123,789)
            with self.assertRaisesRegex(ValueError,'ownership'):
                desktop.capture(123,789,Path('must-not-create.png'))

    def test_hidden_window_capture_rejected(self):
        from windows_gameplay import WindowsDesktop
        desktop=WindowsDesktop()
        with patch.object(desktop,'observe',return_value=dict(visible=False)):
            with self.assertRaisesRegex(ValueError,'hidden'):
                desktop.capture(123,789,Path('must-not-create.png'))


if __name__=='__main__':unittest.main()
