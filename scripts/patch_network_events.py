"""Attach the port's fast-RPCN event-lifetime fix to locally lifted title code."""
import argparse
from pathlib import Path


SIGNATURE = "void func_001530E0(ppu_context* ctx) {\n    PPU_MM_SNAP\n"
HOOK = "    tb_rpcn_retain_discovery_event(ctx);\n"
DECLARATION = 'extern "C" void tb_rpcn_retain_discovery_event(ppu_context*);\n'


def patch(directory):
    matches = []
    for path in Path(directory).glob("ppu_recomp_*.cpp"):
        source = path.read_text()
        if SIGNATURE in source:
            matches.append((path, source))
    if not matches:
        return  # Game-free runtime fixtures have no title event queue.
    if len(matches) != 1 or matches[0][1].count(SIGNATURE) != 1:
        raise ValueError("Ambiguous Tank Battles event queue function")
    path, source = matches[0]
    if SIGNATURE + HOOK in source:
        return
    include = '#include "ppu_recomp.h"\n'
    if source.count(include) != 1:
        raise ValueError("Cannot attach the Tank Battles event queue hook")
    source = source.replace(include, include + DECLARATION, 1)
    source = source.replace(SIGNATURE, SIGNATURE + HOOK, 1)
    path.write_text(source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    patch(parser.parse_args().directory)
