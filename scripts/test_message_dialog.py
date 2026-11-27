"""Compile the actual port dialog overlay and check dismissal ownership/results."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from instrument_sdk import EDITS

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / 'external/ps3recomp'


class DialogDismissal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shutil.which('cc')
        if not cc:
            raise unittest.SkipTest('C compiler unavailable')
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        original = SDK / 'libs/system/cellMsgDialog.c'
        source = original.read_text()
        for before, after in EDITS['libs/system/cellMsgDialog.c']:
            if source.count(before) != 1:
                raise AssertionError('Dialog SDK anchor changed')
            source = source.replace(before, after)
        # Preserve actual SDK headers while relocating the generated source.
        source = re.sub(r'#include "(\.\./[^"\n]+)"',
                        lambda m: '#include "' + str((original.parent / m[1]).resolve()) + '"', source)
        (out / 'dialog.c').write_text(source)
        (out / 'test.c').write_text(r'''
#include "cellMsgDialog.h"
#include "cellSysutil.h"
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>
uint8_t memory[0x20000]; uint8_t* vm_base=memory;
static int queued; static int32_t result;
s32 cellSysutilQueueGuestCallback(u32 opd,u64 arg,u64 userdata) {
 assert(opd==0x11234 && userdata==0x12000);++queued;result=(int32_t)arg;return 0;
}
extern int tb_system_dialog_active(void);
extern void cellMsgDialog_tick(void);
static void open_notice(unsigned type) {
 queued=0;
 assert(cellMsgDialogOpen2(type,(const char*)(uintptr_t)0x10000,
  (CellMsgDialogCallback)(uintptr_t)0x11234,(void*)(uintptr_t)0x12000,0)==0);
}
int main(int argc,char**argv) {
 strcpy((char*)memory+0x10000,"A localized no-button notice");
 if(argc>1) {
  setenv("MSGDIALOG_NOBUTTON_WAITS","1",1);
  setenv("MSGDIALOG_AUTODISMISS_MS",argv[1],1);
  open_notice(3);assert(!queued && tb_system_dialog_active());
  struct timespec t={0,10000000};nanosleep(&t,0);cellMsgDialog_tick();
  if(atoi(argv[1]))assert(queued==1 && result==CELL_MSGDIALOG_BUTTON_ESCAPE);
  else {assert(!queued);assert(cellMsgDialogClose(0)==0);assert(queued==1 && result==CELL_MSGDIALOG_BUTTON_NONE);}
  return 0;
 }
 open_notice(3);assert(queued==1 && result==CELL_MSGDIALOG_BUTTON_ESCAPE && !tb_system_dialog_active());
 open_notice(0x83);assert(queued==1 && result==CELL_MSGDIALOG_BUTTON_NONE);
 open_notice(0x1003);assert(!queued && tb_system_dialog_active());
 assert(cellMsgDialogClose(0)==0);assert(queued==1 && result==CELL_MSGDIALOG_BUTTON_NONE);
 open_notice(0x1003);assert(cellMsgDialogAbort()==0);assert(!queued && !tb_system_dialog_active());
 open_notice(0x23);assert(queued==1 && result==CELL_MSGDIALOG_BUTTON_OK);
 open_notice(0x13);assert(queued==1 && result==CELL_MSGDIALOG_BUTTON_YES);
 return 0;
}
''')
        cls.exe = out / 'dialog-check'
        subprocess.run([cc, '-std=c11', '-D_GNU_SOURCE', '-I'+str(SDK/'include'),
                        '-I'+str(SDK/'libs/system'), '-I'+str(SDK/'runtime/ppu'),
                        str(out/'test.c'), str(out/'dialog.c'), '-pthread', '-o', str(cls.exe)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_check(self, *args):
        env = {k:v for k,v in os.environ.items() if not k.startswith(('MSGDIALOG_', 'TB_DIALOG_'))}
        subprocess.run([str(self.exe), *args], env=env, check=True, capture_output=True)

    def test_user_dismissal_and_title_close_remain_distinct(self):
        self.run_check()

    def test_grace_period_uses_user_cancel_result(self):
        self.run_check('1')

    def test_title_owned_no_button_wait_keeps_close_result(self):
        self.run_check('0')


if __name__ == '__main__':
    unittest.main()
