#!/usr/bin/env python3
"""Original reproducer for segment-based transfer routing and main-stack layout."""
from pathlib import Path
import subprocess
from port import ROOT

def main():
 directory=ROOT/'build-tests/guest-layout';directory.mkdir(parents=True,exist_ok=True)
 source=directory/'layout.cpp'
 source.write_text(r'''#include "guest_layout.h"
#include <cassert>
#include <cstdio>
// Synthetic sender: stack-local transfers use the CPU, other transfers enqueue
// an SPU command. The real guest's routing depends on the same ISA extraction.
static unsigned segment(uint32_t address){return ((address<<4)|(address>>28)) & 15;}
static bool enqueue_sync(uint32_t stack){return segment(stack)!=segment(0);}
int main(){
 assert(!enqueue_sync(0x0ff00000)); // Reproduce the old missing command.
 assert(segment(TB_MAIN_STACK_TOP-0x90)==13);
 assert(enqueue_sync(TB_MAIN_STACK_TOP-0x90));
 assert(segment(TB_MAIN_STACK_TOP-TB_MAIN_STACK_SIZE)==13);
 assert(TB_MAIN_STACK_TOP<0xe0000000u);
 assert(segment(0x10000000)!=segment(TB_MAIN_STACK_TOP));
 puts("PASS: old segment-zero stack drops a sync; PS3 stack segment routes it to the SPU.");
}
''')
 exe=directory/'layout'
 subprocess.run(['c++','-std=c++20','-O2','-I'+str(ROOT),str(source),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
if __name__=='__main__':main()
