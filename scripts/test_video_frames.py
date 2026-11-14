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
    def test_slow_capture_is_not_reported_as_30fps(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=report_video(self.video(Path(tmp),20))
            self.assertFalse(r['meets_30fps'])

if __name__=='__main__':unittest.main()
