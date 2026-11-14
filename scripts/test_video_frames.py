#!/usr/bin/env python3
"""Check real video decoding preserves a one-frame flash and measures capture rate."""
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from video_frame_report import report_video

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'ffmpeg/ffprobe required')
class VideoFramesTests(unittest.TestCase):
    def video(self,d,rate):
        path=d/'synthetic.mkv'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',f'color=black:s=64x64:r={rate}:d={3/rate}','-vf',"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='eq(n,1)'",'-frames:v','3','-c:v','ffv1','-fps_mode','passthrough',str(path)],check=True)
        return path
    def test_single_frame_flash_at_60fps_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=report_video(self.video(Path(tmp),60))
            self.assertEqual(r['frames'],3);self.assertTrue(r['meets_30fps'])
            self.assertGreater(r['largest_sampled_change'],.99)
            self.assertEqual({c['frame'] for c in r['largest_changes']},{1,2})
            self.assertEqual([c['frame'] for c in r['isolated_change_candidates']],[1])
            self.assertEqual(len(r['frame_changes']),2)
    def test_progressing_scene_change_is_not_an_isolated_flash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'transition.mkv'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=black:s=64x64:r=60:d=0.05','-vf',"drawbox=x=0:y=0:w=iw:h=ih:color=gray:t=fill:enable='eq(n,1)',drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='eq(n,2)'",'-frames:v','3','-c:v','ffv1','-fps_mode','passthrough',str(path)],check=True)
            r=report_video(path)
            self.assertEqual(r['isolated_change_candidates'],[])
            self.assertEqual(r['short_excursion_candidates'],[])
            self.assertGreater(r['largest_sampled_change'],.99)
    def test_flash_spanning_multiple_refreshes_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'repeated.mkv'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=black:s=64x64:r=60:d=0.067','-vf',"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='between(n,1,2)'",'-frames:v','4','-c:v','ffv1','-fps_mode','passthrough',str(path)],check=True)
            r=report_video(path)
            self.assertEqual(r['frames'],4)
            self.assertEqual(r['isolated_change_candidates'],[])
            self.assertEqual([(c['first_frame'],c['return_frame']) for c in r['short_excursion_candidates']],[(1,3)])
    def test_slow_capture_is_not_reported_as_30fps(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=report_video(self.video(Path(tmp),20))
            self.assertFalse(r['meets_30fps'])

if __name__=='__main__':unittest.main()
