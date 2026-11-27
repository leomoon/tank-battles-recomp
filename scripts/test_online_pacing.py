"""Game-free hook, read-only recorder and opt-in adapter contract checks."""
import json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
from pacing_report import report
from patch_online_pacing import patch_pacing,INCLUDE,PACING,CORRECTION,STORE,POLL,CALIBRATION,FRAME_SAMPLE,PHASE_REQUEST,ROLE_IDS,MAIN,RENDER_GATE,UPDATE_CALL,ADVANCE_CALL

ROOT=Path(__file__).resolve().parents[1]
class PacingHooks(unittest.TestCase):
 def fixture(self,root):
  resets='        vm_write32(ctx->gpr[31] + 0x124, ctx->gpr[0]);\n'*3
  bodies=[PACING+'loc_001A3C34:\n'+POLL+resets+'}\n',CORRECTION+'loc_0016F014:\n        ctx->gpr[30] = (int64_t)(int32_t)(0);\n'+STORE+'}\n']
  for function,nid in [('0093BD84','1F953B9F'),('0093BE84','9647570B')]:
   bodies.append(f'void func_{function}(ppu_context* ctx) {{\n    PPU_MM_SNAP\n        ps3_hle_call(0x{nid}u, ctx); return;  /* import stub */\n}}\n')
  bodies += ['void func_0016DD64(ppu_context* ctx) {\n    PPU_MM_SNAP\n'+FRAME_SAMPLE+PHASE_REQUEST+'        ctx->gpr[5] = ctx->gpr[26] - ctx->gpr[4];\n}\n',
             'void func_0015B0BC(ppu_context* ctx) {\n    PPU_MM_SNAP\n        vm_write32(ctx->gpr[28] + 0x8, ctx->gpr[4]);\n}\n']
  bodies += ['void func_001D511C(ppu_context* ctx) {\n    PPU_MM_SNAP\n'+CALIBRATION+'}\n']
  bodies += ['void func_0015361C(ppu_context* ctx) {\n    PPU_MM_SNAP\n'+ROLE_IDS+'}\n']
  for name in ('00169438','001696DC','00170A60','001D3A2C','001A3368'):
   extra='        vm_write64(ctx->gpr[31] + 0x18, ctx->gpr[3]);\n' if name=='00169438' else '        vm_write64(ctx->gpr[9] + 0x110, ctx->gpr[3]);\n' if name=='001A3368' else ''
   bodies += [f'void func_{name}(ppu_context* ctx) {{\n    PPU_MM_SNAP\n'+extra+'}\n']
  bodies += [MAIN+ADVANCE_CALL+UPDATE_CALL+RENDER_GATE+'}\n']
  for n,body in enumerate(bodies):(root/f'ppu_recomp_{n:03}.cpp').write_text(INCLUDE+body)
 def test_instruction_hooks_are_scoped_idempotent_and_register_neutral(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.fixture(root);patch_pacing(root)
   first={p:p.read_text() for p in root.iterdir()};patch_pacing(root)
   self.assertEqual(first,{p:p.read_text() for p in root.iterdir()})
   self.assertIn('tb_pacing_hook(ctx, 1);',first[root/'ppu_recomp_000.cpp'])
   self.assertEqual(first[root/'ppu_recomp_000.cpp'].count('tb_pacing_hook(ctx, 10);'),3)
   self.assertIn(STORE+'        tb_pacing_hook(ctx, 2);',first[root/'ppu_recomp_001.cpp'])
   self.assertIn('tb_pacing_hook(ctx, 5);\n        ps3_hle_call(0x1F953B9Fu, ctx);\n        tb_pacing_hook(ctx, 6);',first[root/'ppu_recomp_002.cpp'])
 def test_empty_fixture_is_unchanged(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'ppu_recomp_000.cpp';p.write_text('void fixture() {}\n');patch_pacing(d);self.assertEqual(p.read_text(),'void fixture() {}\n')
 def test_unexpected_missing_and_ambiguous_layouts_fail_before_edits(self):
  for mutation in ('anchor','function','duplicate','format','audit_function','reset','extra_reset','calibration','phase','frame','role','lifecycle','advance'):
   with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as d:
    root=Path(d);self.fixture(root)
    if mutation=='anchor':
     p=root/'ppu_recomp_001.cpp';p.write_text(p.read_text().replace(' -0x2534',' -0x2530'))
    elif mutation=='format':
     for p in root.iterdir():p.write_text(p.read_text().replace('    PPU_MM_SNAP\n','    PPU_MM_SNAP \n'))
    elif mutation=='function':(root/'ppu_recomp_001.cpp').unlink()
    elif mutation=='audit_function':(root/'ppu_recomp_004.cpp').unlink()
    elif mutation=='reset':
     p=root/'ppu_recomp_000.cpp';p.write_text(p.read_text().replace(' + 0x124',' + 0x128',1))
    elif mutation=='extra_reset':
     patch_pacing(root);p=root/'ppu_recomp_000.cpp';p.write_text(p.read_text().replace('loc_001A3C34:\n','loc_001A3C34:\n        vm_write32(ctx->gpr[31] + 0x124, ctx->gpr[0]);\n',1))
    elif mutation in ('phase','frame'):
     p=root/'ppu_recomp_004.cpp';p.write_text(p.read_text().replace(PHASE_REQUEST if mutation=='phase' else FRAME_SAMPLE,'        /* changed instruction */\n'))
    elif mutation=='role':
     p=root/'ppu_recomp_007.cpp';p.write_text(p.read_text().replace(ROLE_IDS,'        /* changed identity comparison */\n'))
    elif mutation=='advance':
     p=root/'ppu_recomp_013.cpp';p.write_text(p.read_text().replace(ADVANCE_CALL,'        /* missing advance */\n'))
    elif mutation=='lifecycle':
     p=root/'ppu_recomp_008.cpp';p.write_text(p.read_text().replace(' + 0x18,',' + 0x20,'))
    elif mutation=='calibration':
     p=root/'ppu_recomp_006.cpp';p.write_text(p.read_text().replace(' + 0x18,',' + 0x20,'))
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
inline void vm_write32(uint64_t p,uint32_t v){for(int i=0;i<4;++i)vm_base[p+i]=uint8_t(v>>(24-8*i));}
''')
  (cls.root/'fixture.cpp').write_text('''#include "ppu_recomp.h"
#include <cstring>
#include <chrono>
#include <thread>
#include <cstdio>
#include <initializer_list>
uint8_t memory[0x20000]={};uint8_t* vm_base=memory;
extern "C" void tb_pacing_hook(ppu_context*,unsigned);
extern "C" void tb_pacing_experiment(ppu_context*,unsigned);
int main(int argc,char** argv) {
 if(argc>1 && (!strcmp(argv[1],"render-candidate") || !strcmp(argv[1],"render-audit"))) {
  ppu_context c{};
  if(!strcmp(argv[1],"render-audit")) {tb_pacing_hook(&c,0);std::this_thread::sleep_for(std::chrono::milliseconds(50));}
  memory[0x13014]=1;memory[0x10121]=1;memory[0x10122]=1;
  const bool enabled=getenv("TB_EXPERIMENTAL_ONLINE_PACING") && !strcmp(getenv("TB_EXPERIMENTAL_ONLINE_PACING"),"recovery-render");
  auto request=[&](unsigned delta) {
    c.gpr[29]=33;c.gpr[3]=16;tb_pacing_experiment(&c,14);
    c.gpr[30]=0x13000;c.gpr[3]=0x14000;c.gpr[31]=101+delta;c.gpr[28]=101;
    vm_write32(0x14020,101);tb_pacing_experiment(&c,13);
    c.gpr[3]=0;tb_pacing_experiment(&c,12);
  };
  request(1);vm_write32(0x14020,102);c.gpr[3]=0x14000;tb_pacing_experiment(&c,19);c.gpr[7]=0x10122;c.gpr[29]=1;c.cr=0x12345678;
  const auto before=c;uint8_t saved[sizeof(memory)];memcpy(saved,memory,sizeof(memory));
  tb_pacing_experiment(&c,17);if(c.gpr[29]!=(enabled?0:1))return 30;
  c.gpr[29]=1;if(memcmp(&c,&before,sizeof(c)) || memcmp(saved,memory,sizeof(memory)))return 31;
  request(1);c.gpr[7]=0x10122;c.gpr[29]=1;
  tb_pacing_experiment(&c,17);if(c.gpr[29]!=1)return 32; // Duplicate host frame.
  tb_pacing_experiment(&c,15);request(2);c.gpr[7]=0x10122;c.gpr[29]=1;
  tb_pacing_experiment(&c,17);if(c.gpr[29]!=1)return 33;
  tb_pacing_experiment(&c,15);request(1);vm_write32(0x14020,102);
  c.gpr[7]=0x10122;c.gpr[29]=1;tb_pacing_experiment(&c,17);if(c.gpr[29]!=1)return 34;
  tb_pacing_experiment(&c,15);request(1);tb_pacing_experiment(&c,15);
  c.gpr[7]=0x10122;c.gpr[29]=1;tb_pacing_experiment(&c,17);if(c.gpr[29]!=1)return 35;
  return 0;
 }
 ppu_context c{};c.gpr[31]=0x10000;c.gpr[30]=0x11000;c.gpr[11]=1234;c.fpr[0]=999;c.fpr[1]=888;c.cr=128;
 const auto before=c;
 tb_pacing_hook(&c,0);
 std::this_thread::sleep_for(std::chrono::milliseconds(50));
 for(unsigned i=0;i<(argc>1 && !strcmp(argv[1],"overflow")?40000:20);++i) {
  tb_pacing_hook(&c,0);tb_pacing_hook(&c,1);c.cr=0;tb_pacing_hook(&c,1);c.cr=128;tb_pacing_hook(&c,2);
  tb_pacing_hook(&c,3);tb_pacing_hook(&c,4);tb_pacing_hook(&c,5);tb_pacing_hook(&c,6);
 }
 if(argc>1 && !strcmp(argv[1],"audit")) {
  c.gpr[3]=0x15000;c.gpr[6]=0x12000;c.gpr[30]=0x13000;c.gpr[28]=0x14000;c.gpr[4]=3;c.gpr[26]=7;c.gpr[5]=4;
  const auto audit_before=c;
  for(unsigned op=7;op<=10;++op)tb_pacing_hook(&c,op);
  if(memcmp(&audit_before,&c,sizeof(c)))return 4;
  c.gpr[22]=123456789;c.gpr[31]=10000;c.gpr[28]=10;c.gpr[24]=9990;c.gpr[25]=40;c.gpr[23]=10;
  const auto clock_before=c;
  tb_pacing_hook(&c,11);
  c.gpr[1]=0x16000;c.gpr[7]=c.gpr[29]^c.gpr[3];
  const auto phase_before=c;
  for(unsigned op=12;op<=14;++op)tb_pacing_hook(&c,op);
  c.fpr[1]=1.0/60.0;tb_pacing_hook(&c,18);c=phase_before;
  if(memcmp(&phase_before,&c,sizeof(c)))return 6;
  c.gpr[1]=clock_before.gpr[1];
  c.gpr[7]=clock_before.gpr[7];
  if(memcmp(&clock_before,&c,sizeof(c)))return 5;
  c=before;
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
 def run_fixture(self,name,enabled,overflow=False,early=False,audit=False,clock_only=False,frame_only=False):
  trace=self.root/(name+'.jsonl');trigger=self.root/(name+'.trigger');trigger.touch()
  env={k:v for k,v in os.environ.items() if not k.startswith('TB_PACING_') and k!='TB_EXPERIMENTAL_ONLINE_PACING'}
  if enabled:env.update(TB_PACING_TRACE=str(trace),TB_PACING_TRIGGER=str(trigger),TB_PACING_SECONDS='1',TB_PACING_ROLE='host')
  if clock_only:env['TB_PACING_CLOCK_ONLY']='1'
  if frame_only:env['TB_PACING_FRAME_ONLY']='1'
  subprocess.run([str(self.exe)]+(['overflow'] if overflow else ['early'] if early else ['audit'] if audit else []),env=env,check=True)
  return trace
 def test_disabled_creates_no_output_and_preserves_guest(self):
  self.assertFalse(self.run_fixture('off',False).exists())
 def test_render_candidate_scopes_mutation_and_retires_previous_options(self):
  for option in (None,'','0','1','stale-phase','recovery-no-wait','recovery-render-typo','recovery-render'):
   env={k:v for k,v in os.environ.items() if not k.startswith(('TB_PACING_','TB_EXPERIMENTAL_'))}
   if option is not None:env['TB_EXPERIMENTAL_ONLINE_PACING']=option
   subprocess.run([str(self.exe),'render-candidate'],env=env,check=True)
 def test_render_guard_metadata_preserves_frame_indices_and_action_meaning(self):
  trace=self.root/'render-guard.jsonl';trigger=self.root/'render-guard.trigger';trigger.touch()
  env={k:v for k,v in os.environ.items() if not k.startswith(('TB_PACING_','TB_EXPERIMENTAL_'))}
  env.update(TB_PACING_TRACE=str(trace),TB_PACING_TRIGGER=str(trigger),TB_PACING_SECONDS='1',TB_PACING_FRAME_ONLY='1',TB_EXPERIMENTAL_ONLINE_PACING='recovery-render')
  subprocess.run([str(self.exe),'render-audit'],env=env,check=True)
  rows=[json.loads(line) for line in trace.read_text().splitlines()]
  self.assertEqual(rows[0]['dropped'],0)
  actions=[row for row in rows[1:] if row['kind']==16]
  self.assertEqual(len(actions),1)
  self.assertEqual((actions[0]['object'],actions[0]['local_sequence'],actions[0]['previous_guest_us']),(0x10000,102,101))
  first=next(row for row in rows[1:] if row['kind']==19)
  self.assertEqual((first['object'],first['interval_bits'],first['local_sequence'],first['previous_guest_us'],first['observed_guest_us'],first['flags']),(0x14000,0x13000,102,101,102,29))
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
 def test_producer_audit_records_operands_without_writing_guest(self):
  rows=[json.loads(l) for l in self.run_fixture('audit',True,audit=True).read_text().splitlines()][1:]
  samples=[r for r in rows if r['kind']==6]
  self.assertEqual(len(samples),1)
  self.assertEqual((samples[0]['previous_guest_us'],samples[0]['observed_guest_us'],samples[0]['correction_u32']),(3,7,4))
  self.assertTrue({6,7,8,9,10,11,12,13}.issubset({r['kind'] for r in rows}))
  update=next(r for r in rows if r['kind']==18)
  self.assertEqual(update['interval_bits'],0x3c888889)
  clock=next(r for r in rows if r['kind']==10)
  self.assertEqual((clock['previous_guest_us'],clock['observed_guest_us'],clock['local_sequence']),(123456789,10000,10))
  self.assertEqual((clock['interval_bits'],clock['result_u32'],clock['correction_u32']),(9990,40,10))
 def test_shutdown_marks_interrupted_window(self):
  rows=[json.loads(l) for l in self.run_fixture('early',True,early=True).read_text().splitlines()]
  self.assertFalse(rows[0]['complete']);self.assertTrue(rows[0]['events'])
 def test_clock_only_capture_filters_other_guest_events(self):
  rows=[json.loads(l) for l in self.run_fixture('clock-only',True,audit=True,clock_only=True).read_text().splitlines()]
  self.assertEqual(rows[0]['events'],1)
  self.assertEqual(rows[1]['kind'],10)
 def test_frame_only_capture_preserves_guest_and_retains_frame_events(self):
  rows=[json.loads(l) for l in self.run_fixture('frame-only',True,audit=True,frame_only=True).read_text().splitlines()]
  self.assertEqual(rows[0]['dropped'],0)
  self.assertEqual({r['kind'] for r in rows[1:]},{1,2,3,6,7,8,9,11,12,18})
 def test_overflow_is_bounded_and_counted(self):
  rows=[json.loads(l) for l in self.run_fixture('overflow',True,True).read_text().splitlines()]
  self.assertFalse(report(self.root/'overflow.jsonl')['usable_timeline'])
  self.assertEqual(rows[0]['events'],32768);self.assertGreater(rows[0]['dropped'],0)
  self.assertEqual(len(rows),32769)

if __name__=='__main__':unittest.main()
