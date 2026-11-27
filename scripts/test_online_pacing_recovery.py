"""One-frame render eligibility, ownership, ordering and lifecycle checks."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class RecoveryTests(unittest.TestCase):
    def test_render_decision_requires_fresh_owned_single_frame_request(self):
        compiler=shutil.which('g++') or shutil.which('clang++')
        if not compiler:self.skipTest('C++ compiler unavailable')
        root=Path(__file__).resolve().parents[1]
        source=r'''
#include "online_pacing_recovery.h"
#include <cassert>
#include <initializer_list>
using namespace tb_pacing_recovery;
RenderState seed(uint32_t remote=102,uint32_t local=101) {
 RenderState s;s.role(33,16);s.observe(1,2,remote,local,100,true);s.request(1,remote,local,100);s.advance(2,remote);return s;
}
int main() {
 RenderState raw;raw.role(33,16);raw.observe(1,2,102,101,100,true);raw.request(1,102,101,100);
 assert(!raw.consume(102,101,true,true)); // Numerical catch-up alone is insufficient.
 auto s=seed();s.advance(2,102);assert(!s.consume(102,101,true,true)); // Two advances invalidate.
 s=seed();s.advance(3,102);assert(!s.consume(102,101,true,true)); // Wrong manager.
 s=seed();assert(s.consume(102,101,true,true));assert(!s.consume(102,102,true,true));
 s.request(1,102,101,102);assert(!s.consume(102,102,true,true));
 for(auto gap:{0u,2u,0xffffffffu,0x80000000u}) {s=seed(101+gap,101);assert(!s.consume(102,101,true,true));}
 s=seed(0,0xffffffff);assert(s.consume(0,101,true,true));
 s=seed();assert(!s.consume(103,101,true,true)); // A later update cannot use it.
 s=seed();assert(!s.consume(102,50000101,true,true));
 s=seed();assert(!s.consume(102,99,true,true));
 s=seed();assert(!s.consume(102,101,false,true));
 s=seed();assert(!s.consume(102,101,true,false));
 s=seed();s.observe(1,2,102,102,101,true);assert(!s.consume(102,101,true,true));
 s=seed();s.observe(1,2,103,103,101,true);assert(!s.consume(102,101,true,true));
 s=seed();s.observe(1,2,101,101,101,true);assert(!s.consume(102,101,true,true));
 s=seed();s.observe(1,2,102,101,50000099,true);s.request(1,102,101,50000101);
 assert(!s.consume(102,50000101,true,true)); // Duplicate cannot refresh age.
 s=seed();s.observe(3,2,103,102,101,true);assert(s.unsupported && !s.consume(102,101,true,true));
 s=seed();s.observe(1,3,103,102,101,true);assert(s.unsupported && !s.consume(102,101,true,true));
 s=seed();s.observe(1,2,102+0x80000000u,101,101,true);assert(!s.consume(102,101,true,true));
 s=seed();s.role(16,16);assert(!s.consume(102,101,true,true));
 s=seed();s.role(44,16);assert(!s.consume(102,101,true,true));
 s=seed();auto generation=s.generation;s.reset();assert(s.generation==generation+1 && !s.consume(102,101,true,true));
 s.role(44,55);s.observe(3,4,1,0,101,true);s.request(3,1,0,101);s.advance(4,1);assert(s.consume(1,102,true,true));
 s=seed();s.role(33,0);s.observe(1,2,102,101,100,true);s.request(1,102,101,100);assert(!s.consume(102,101,true,true));
 RenderState unknown;unknown.observe(1,2,102,101,100,true);unknown.request(1,102,101,100);assert(!unknown.consume(102,101,true,true));
 s=seed();s.observe(1,2,103,102,101,false);assert(!s.consume(102,101,true,true));
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);(path/'fixture.cpp').write_text(source)
            subprocess.run([compiler,'-std=c++20','-Wall','-Wextra','-Werror','-I'+str(root),str(path/'fixture.cpp'),'-o',str(path/'fixture')],check=True)
            subprocess.run([str(path/'fixture')],check=True)


if __name__=='__main__':unittest.main()
