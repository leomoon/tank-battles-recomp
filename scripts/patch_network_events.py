"""Attach RPCN compatibility hooks to the locally lifted Tank Battles title."""
import argparse
from pathlib import Path


SIGNATURE = "void func_001530E0(ppu_context* ctx) {\n    PPU_MM_SNAP\n"
HOOK = "    tb_rpcn_retain_discovery_event(ctx);\n"
DECLARATION = 'extern "C" void tb_rpcn_retain_discovery_event(ppu_context*);\n'
SCORE_SIGNATURE = "void func_0080A820(ppu_context* ctx) {\n    PPU_MM_SNAP\n"
SCORE_DECLARATION = 'extern "C" uint32_t tb_rpcn_score_placeholder(uint32_t, uint32_t);\n'
SCORE_LOADING = '        ctx->gpr[4] = vm_read32(ctx->gpr[2] + -0x4310);\n'
SCORE_PLACEHOLDER = (
    '        ctx->gpr[4] = tb_rpcn_score_placeholder(\n'
    '            vm_read32(ctx->gpr[2] + -0x4310), vm_read32(ctx->gpr[2] + -0x4318));\n'
)
INCLUDE = '#include "ppu_recomp.h"\n'


def patch_score_menu(path, source):
    start = source.index(SCORE_SIGNATURE)
    end = source.index('\n}\n', start) + 1
    function = source[start:end]
    if function.count(SCORE_PLACEHOLDER) == 2 and SCORE_DECLARATION in source:
        return
    if function.count(SCORE_LOADING) != 2 or source.count(INCLUDE) != 1:
        raise ValueError("Cannot attach the Tank Battles empty leaderboard hook")
    # Choose between the title's existing Loading and dash labels. An empty
    # server result must not create a dummy player to make the menu draw.
    function = function.replace(SCORE_LOADING, SCORE_PLACEHOLDER)
    source = source[:start] + function + source[end:]
    path.write_text(source.replace(INCLUDE, INCLUDE + SCORE_DECLARATION, 1))


def patch(directory):
    matches = []
    score_matches = []
    for path in Path(directory).glob("ppu_recomp_*.cpp"):
        source = path.read_text()
        if SIGNATURE in source:
            matches.append(path)
        if SCORE_SIGNATURE in source:
            score_matches.append(path)
    if len(matches) > 1:
        raise ValueError("Ambiguous Tank Battles event queue function")
    if len(score_matches) > 1:
        raise ValueError("Ambiguous Tank Battles leaderboard function")
    if score_matches:
        path = score_matches[0]
        patch_score_menu(path, path.read_text())
    if not matches:
        return  # Game-free runtime fixtures have no title event queue.
    path = matches[0]
    source = path.read_text()
    if source.count(SIGNATURE) != 1:
        raise ValueError("Ambiguous Tank Battles event queue function")
    if SIGNATURE + HOOK in source:
        return
    if source.count(INCLUDE) != 1:
        raise ValueError("Cannot attach the Tank Battles event queue hook")
    source = source.replace(INCLUDE, INCLUDE + DECLARATION, 1)
    source = source.replace(SIGNATURE, SIGNATURE + HOOK, 1)
    path.write_text(source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    patch(parser.parse_args().directory)
