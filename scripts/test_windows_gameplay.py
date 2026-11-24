"""Synthetic contracts for Windows build discovery and owned-window observations."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from build_identity import measurement_build, read_command_log


class BuildTests(unittest.TestCase):
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
