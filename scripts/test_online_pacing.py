"""Game-free anchor and read-only recorder contract checks."""
import json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
from pacing_report import report
from patch_online_pacing import patch_pacing,INCLUDE,PACING,CORRECTION,STORE,POLL

ROOT=Path(__file__).resolve().parents[1]
class PacingHooks(unittest.TestCase):
 def fixture(self,root):
  bodies=[PACING+'loc_001A3C34:\n'+POLL+'}\n',CORRECTION+STORE+'}\n']
  for function,nid in [('0093BD84','1F953B9F'),('0093BE84','9647570B')]:
   bodies.append(f'void func_{function}(ppu_context* ctx) {{\n    PPU_MM_SNAP\n        ps3_hle_call(0x{nid}u, ctx); return;  /* import stub */\n}}\n')
  for n,body in enumerate(bodies):(root/f'ppu_recomp_{n:03}.cpp').write_text(INCLUDE+body)
 def test_instruction_hooks_are_scoped_idempotent_and_register_neutral(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.fixture(root);patch_pacing(root)
   first={p:p.read_text() for p in root.iterdir()};patch_pacing(root)
   self.assertEqual(first,{p:p.read_text() for p in root.iterdir()})
   self.assertIn('tb_pacing_hook(ctx, 1);',first[root/'ppu_recomp_000.cpp'])
   self.assertIn(STORE+'        tb_pacing_hook(ctx, 2);',first[root/'ppu_recomp_001.cpp'])
   self.assertIn('tb_pacing_hook(ctx, 5);\n        ps3_hle_call(0x1F953B9Fu, ctx);\n        tb_pacing_hook(ctx, 6);',first[root/'ppu_recomp_002.cpp'])
 def test_empty_fixture_is_unchanged(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'ppu_recomp_000.cpp';p.write_text('void fixture() {}\n');patch_pacing(d);self.assertEqual(p.read_text(),'void fixture() {}\n')
 def test_unexpected_missing_and_ambiguous_layouts_fail_before_edits(self):
  for mutation in ('anchor','function','duplicate','format'):
   with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as d:
    root=Path(d);self.fixture(root)
    if mutation=='anchor':
     p=root/'ppu_recomp_001.cpp';p.write_text(p.read_text().replace(' -0x2534',' -0x2530'))
    elif mutation=='format':
     for p in root.iterdir():p.write_text(p.read_text().replace('    PPU_MM_SNAP\n','    PPU_MM_SNAP \n'))
    elif mutation=='function':(root/'ppu_recomp_001.cpp').unlink()
    else:(root/'ppu_recomp_099.cpp').write_text((root/'ppu_recomp_001.cpp').read_text())
    before={p:p.read_bytes() for p in root.iterdir()}
    with self.assertRaises(ValueError):patch_pacing(root)
    self.assertEqual(before,{p:p.read_bytes() for p in root.iterdir()})

class PacingRecorder(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)
  # A minimal original test ABI, no generated title header/content required.
  (cls.root/'ppu_recomp.h').write_text('''#pragma once
#include <cstdint>
struct ppu_context {uint64_t gpr[32]{};double fpr[32]{};uint32_t cr=0;uint64_t lr=0;};
extern uint8_t* vm_base;
inline uint8_t vm_read8(uint64_t p){return vm_base[p];}
inline uint32_t vm_read32(uint64_t p){return uint32_t(vm_base[p])<<24|uint32_t(vm_base[p+1])<<16|uint32_t(vm_base[p+2])<<8|vm_base[p+3];}
inline uint64_t vm_read64(uint64_t p){return uint64_t(vm_read32(p))<<32|vm_read32(p+4);}
''')
  (cls.root/'fixture.cpp').write_text('''#include "ppu_recomp.h"
#include <cstring>
#include <chrono>
#include <thread>
#include <cstdio>
uint8_t memory[0x20000]={};uint8_t* vm_base=memory;
extern "C" void tb_pacing_hook(ppu_context*,unsigned);
int main(int argc,char** argv) {
 ppu_context c{};c.gpr[31]=0x10000;c.gpr[30]=0x11000;c.gpr[11]=1234;c.fpr[0]=999;c.fpr[1]=888;c.cr=128;
 const auto before=c;
 tb_pacing_hook(&c,0);
 std::this_thread::sleep_for(std::chrono::milliseconds(50));
 for(unsigned i=0;i<(argc>1 && !strcmp(argv[1],"overflow")?40000:20);++i) {
  tb_pacing_hook(&c,0);tb_pacing_hook(&c,1);c.cr=0;tb_pacing_hook(&c,1);c.cr=128;tb_pacing_hook(&c,2);
  tb_pacing_hook(&c,3);tb_pacing_hook(&c,4);tb_pacing_hook(&c,5);tb_pacing_hook(&c,6);
 }
 if(memcmp(&before,&c,sizeof(c)))return 2;
 for(auto b:memory)if(b)return 3;
 if(argc<2 || strcmp(argv[1],"early")) std::this_thread::sleep_for(std::chrono::milliseconds(1100));
 return 0;
}
''')
  compiler=shutil.which('g++') or shutil.which('clang++')
  if not compiler:raise unittest.SkipTest('C++ compiler unavailable')
  cls.exe=cls.root/'fixture'
  subprocess.run([compiler,'-std=c++20','-O2','-pthread','-I'+str(cls.root),'-I'+str(ROOT),str(ROOT/'online_pacing.cpp'),str(cls.root/'fixture.cpp'),'-o',str(cls.exe)],check=True)
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def run_fixture(self,name,enabled,overflow=False,early=False):
  trace=self.root/(name+'.jsonl');trigger=self.root/(name+'.trigger');trigger.touch()
  env={k:v for k,v in os.environ.items() if not k.startswith('TB_PACING_')}
  if enabled:env.update(TB_PACING_TRACE=str(trace),TB_PACING_TRIGGER=str(trigger),TB_PACING_SECONDS='1',TB_PACING_ROLE='host')
  subprocess.run([str(self.exe)]+(['overflow'] if overflow else ['early'] if early else []),env=env,check=True)
  return trace
 def test_disabled_creates_no_output_and_preserves_guest(self):
  self.assertFalse(self.run_fixture('off',False).exists())
 def test_enabled_records_numeric_metadata_and_preserves_guest(self):
  path=self.run_fixture('on',True)
  rows=[json.loads(l) for l in path.read_text().splitlines()]
  summary=report(path)
  self.assertTrue(summary['lossless'])
  self.assertEqual(summary['correction_us']['min'],1234)
  self.assertEqual(summary['recvfrom']['calls'],20)
  self.assertEqual(rows[0]['role'],'host');self.assertEqual(rows[0]['dropped'],0)
  self.assertEqual({r['kind'] for r in rows[1:]},{1,2,3,4,5})
  self.assertEqual(rows[1]['correction_u32'],1234)
  self.assertTrue(all(rows[0]['start_ns']<=r['ns']<=rows[0]['end_ns'] for r in rows[1:]))
 def test_shutdown_marks_interrupted_window(self):
  rows=[json.loads(l) for l in self.run_fixture('early',True,early=True).read_text().splitlines()]
  self.assertFalse(rows[0]['complete']);self.assertTrue(rows[0]['events'])
 def test_overflow_is_bounded_and_counted(self):
  rows=[json.loads(l) for l in self.run_fixture('overflow',True,True).read_text().splitlines()]
  self.assertFalse(report(self.root/'overflow.jsonl')['usable_timeline'])
  self.assertEqual(rows[0]['events'],32768);self.assertGreater(rows[0]['dropped'],0)
  self.assertEqual(len(rows),32769)

if __name__=='__main__':unittest.main()
