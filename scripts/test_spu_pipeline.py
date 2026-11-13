#!/usr/bin/env python3
"""Run pinned SDK pipeline tests with its missing disabled-watch fixture symbols."""
import importlib.util
import sys
import unittest
from port import ROOT

def main():
 tools=ROOT/'external/ps3recomp/tools';sys.path.insert(0,str(tools))
 spec=importlib.util.spec_from_file_location('sdk_pipeline_fixture',tools/'test_spu_program_pipeline.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 original=module.Pipeline.write_runtime_blocks
 def fixture(self):
  runtime=original(self)
  path=self.root/'registry.c'
  path.write_text(path.read_text()+'''\n/* Disabled LS diagnostics required by the pinned public headers. */
int g_spu_ls_watch_n=0;
void spu_ls_watch_slow(uint32_t address,int writing,const uint8_t* data,uint32_t pc,uint32_t lr){
 (void)address;(void)writing;(void)data;(void)pc;(void)lr;
}
''')
  return runtime
 module.Pipeline.write_runtime_blocks=fixture
 print('Fixture adapter supplies disabled LS-watch symbols; SDK assertions and runtime blocks are unchanged.',flush=True)
 result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(module.Pipeline))
 raise SystemExit(0 if result.wasSuccessful() else 1)
if __name__=='__main__':main()
