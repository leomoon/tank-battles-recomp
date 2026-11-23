#!/usr/bin/env python3
"""Exercise persisted preferences, overlay defaults and native session logs."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from port import ROOT


class PlayerLaunchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=ROOT/'build-tests/player-launch';cls.build.mkdir(parents=True,exist_ok=True)
        source=cls.build/'fixture.cpp'
        source.write_text(r'''
#include "player_settings.h"
#include "player_session.h"
#include "execution_policy_api.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
int main(int argc,char** argv) {
 if(!strcmp(argv[1],"session")) {tb_player_session_begin();printf("fixture stdout\n");fprintf(stderr,"fixture stderr\n");printf("verbose=%s\n",getenv("PS3_VERBOSE"));return 0;}
 tb_settings_init();
 if(!strcmp(argv[1],"write")) {tb_settings_note_overlay(1);tb_settings_note_fullscreen(1);tb_settings_note_window(1366,768);tb_settings_note_window(999999,1);}
 if(!strcmp(argv[1],"toggle")) tb_fps_toggle();
 if(!strcmp(argv[1],"antialiasing")) tb_settings_note_antialiasing(!tb_settings_antialiasing());
 printf("antialiasing=%d\n",tb_settings_antialiasing());
 int w=0,h=0;int valid=tb_settings_window_size(&w,&h);
 static uint32_t pixels[TB_OVERLAY_BUFFER_SIZE/4];
 tb_overlay_region region{};
 printf("overlay=%d size=%d,%d,%d fullscreen=%s\n",tb_draw_overlay(pixels,288,180,0,&region),valid,w,h,getenv("PS3RECOMP_FULLSCREEN")?getenv("PS3RECOMP_FULLSCREEN"):"unset");
}
''')
        cls.exe=cls.build/'fixture'
        sources = ('player_settings.cpp', 'player_session.cpp', 'fps_overlay.cpp', 'input_events.cpp',
                   'rendering_profile.cpp')
        subprocess.run(['c++','-std=c++20','-O2','-I'+str(ROOT),str(source),
                        *[str(ROOT/p) for p in sources],
                        '-pthread','-o',str(cls.exe)],check=True)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.env={k:v for k,v in os.environ.items() if not k.startswith(('TB_','PS3_','PS3RECOMP_'))}
        self.env.update(PS3_GAMEDATA_ROOT=str(self.root),TB_LOG_DIR=str(self.root/'logs'))

    def run_fixture(self,mode,**extra):
        return subprocess.run([str(self.exe),mode],env=dict(self.env,**extra),capture_output=True,text=True,check=True)

    def test_overlay_off_by_default_and_explicit_override(self):
        self.assertIn('overlay=0',self.run_fixture('read').stdout)
        self.assertIn('overlay=1',self.run_fixture('read',TB_FPS_OVERLAY='1').stdout)

    def test_persisted_overlay_fullscreen_size_and_override(self):
        self.run_fixture('write')
        before=(self.root/'settings.ini').read_bytes()
        self.assertIn('overlay=1 size=1,1366,768 fullscreen=1',self.run_fixture('read').stdout)
        self.assertIn('overlay=0 size=1,1366,768 fullscreen=0',self.run_fixture('read',TB_FPS_OVERLAY='0',PS3RECOMP_FULLSCREEN='0').stdout)
        self.assertEqual(before,(self.root/'settings.ini').read_bytes())
        self.assertFalse(list(self.root.glob('*.tmp*')))

    def test_f3_toggle_persists_across_processes(self):
        self.assertIn('overlay=1',self.run_fixture('toggle').stdout)
        self.assertIn('overlay=1',self.run_fixture('read').stdout)
        self.assertIn('overlay=0',self.run_fixture('toggle').stdout)
        self.assertIn('overlay=0',self.run_fixture('read').stdout)

    def test_antialiasing_default_toggle_persistence_and_override(self):
        self.assertIn('antialiasing=0', self.run_fixture('read').stdout)
        self.assertIn('antialiasing=1', self.run_fixture('antialiasing').stdout)
        self.assertIn('antialiasing=1', self.run_fixture('read').stdout)
        before = (self.root/'settings.ini').read_bytes()
        self.assertIn('antialiasing=0', self.run_fixture('read', TB_FXAA='0').stdout)
        self.assertEqual(before, (self.root/'settings.ini').read_bytes())
        self.assertIn('antialiasing=0', self.run_fixture('antialiasing').stdout)
        self.assertIn('antialiasing=0', self.run_fixture('read').stdout)
        self.assertIn('antialiasing=1', self.run_fixture('antialiasing', TB_SETTINGS_FILE='').stdout)
        self.assertIn('antialiasing=0', self.run_fixture('read').stdout)

    def test_diagnostics_can_disable_settings(self):
        self.run_fixture('write')
        before=(self.root/'settings.ini').read_bytes()
        self.assertIn('overlay=0',self.run_fixture('read',TB_SETTINGS_FILE='').stdout)
        self.run_fixture('toggle',TB_SETTINGS_FILE='')
        self.assertEqual(before,(self.root/'settings.ini').read_bytes())

    def test_invalid_size_and_unknown_settings_are_ignored(self):
        (self.root/'settings.ini').write_text('window_width = 999999\nwindow_height = -1\nunknown = 1\nnot a setting\n')
        self.assertIn('size=0,0,0',self.run_fixture('read').stdout)

    def test_native_logs_capture_both_streams_with_readable_permissions(self):
        before=os.umask(0o077)
        try:result=self.run_fixture('session')
        finally:os.umask(before)
        self.assertEqual(result.stdout,'')
        log=next((self.root/'logs').glob('session-*.log'))
        self.assertIn(str(log),result.stderr)
        text=log.read_text();self.assertIn('fixture stdout',text);self.assertIn('fixture stderr',text);self.assertIn('verbose=0',text)
        self.assertEqual(log.stat().st_mode&0o777,0o644)

    def test_console_and_external_capture_preserve_stdio(self):
        for option in ({'TB_LOG_CONSOLE':'1'},{'TB_SESSION_CAPTURED':'1'}):
            result=self.run_fixture('session',**option)
            self.assertIn('fixture stdout',result.stdout);self.assertIn('fixture stderr',result.stderr)
        self.assertFalse((self.root/'logs').exists())


if __name__=='__main__':unittest.main()
