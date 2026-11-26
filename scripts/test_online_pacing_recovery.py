"""Deterministic stale-phase experiment provenance and lifecycle checks."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class RecoveryTests(unittest.TestCase):
    def test_phase_estimate_invalidation_requires_known_older_frames(self):
        compiler=shutil.which('g++') or shutil.which('clang++')
        if not compiler:self.skipTest('C++ compiler unavailable')
        root=Path(__file__).resolve().parents[1]
        source=r'''
#include "online_pacing_recovery.h"
#include <cassert>
using namespace tb_pacing_recovery;
State estimate(uint32_t frame,uint32_t delta=8000) {
 State s;s.sample(frame,delta,0);s.select(delta,0);s.publish(delta/2,delta/2);return s;
}
int main() {
 auto s=estimate(100);
 assert(!s.recover(100,100,4000,0,4000)); // Same frame: preserve the wait.
 assert(!s.recover(99,100,4000,0,4000));  // Local ahead: preserve the wait.
 assert(s.recover(101,100,4000,0,4000));  // New host frame supersedes old estimate.
 assert(!s.recover(101,100,4000,0,4000)); // Never consume twice.
 s=estimate(101);assert(!s.recover(101,100,4000,0,4000)); // Fresh estimate.
 s=estimate(100);assert(!s.recover(101,100,4001,0,4000)); // Untracked overwrite.
 s=estimate(100);assert(!s.recover(101,100,4000,1,4000)); // Unknown pending sample.
 s=estimate(100);s.sample(101,9000,0);
 assert(!s.recover(101,100,4000,9000,4000)); // Fresh pending control signal.
 s=estimate(100);s.sample(99,9000,0);
 assert(s.recover(101,100,4000,9000,4000)); // Both tracked estimates are older.
 s=estimate(100);s.select(0,4000);s.publish(2000,2000);
 assert(s.recover(101,100,2000,0,2000)); // Original halving retains provenance.
 s=estimate(0xffffffff);assert(s.recover(0,0xffffffff,4000,0,4000));
 s=estimate(0);assert(!s.recover(0x80000000,1,4000,0,4000)); // Ambiguous half-wrap.
 s=estimate(100);s={};assert(!s.recover(101,100,4000,0,4000)); // Session reset.
 s=estimate(200);assert(s.recover(201,200,4000,0,4000)); // Fresh new session.
 s=estimate(100);s.sample(100,0xffffffff,0);assert(s.pending_value==0); // Negative delta.
 s=estimate(100,0x7fffffff);assert(s.recover(101,100,0x3fffffff,0,0x3fffffff));
 s=estimate(100);s.select(0,0);s.publish(0,0);
 assert(!s.recover(101,100,4000,0,4000)); // A cleared title peak cannot be resurrected.
 s=estimate(100);s.sample(102,8000,0);s.select(8000,4000);s.publish(4000,4000);
 assert(!s.recover(101,100,4000,0,4000)); // Newer peak wins.
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);(path/'fixture.cpp').write_text(source)
            subprocess.run([compiler,'-std=c++20','-Wall','-Wextra','-Werror','-I'+str(root),str(path/'fixture.cpp'),'-o',str(path/'fixture')],check=True)
            subprocess.run([str(path/'fixture')],check=True)


if __name__=='__main__':unittest.main()
