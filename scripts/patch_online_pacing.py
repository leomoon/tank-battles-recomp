"""Attach read-only pacing diagnostics using exact title instruction anchors."""
from pathlib import Path

DECLARATION='extern "C" void tb_pacing_hook(ppu_context*, unsigned);\n'
INCLUDE='#include "ppu_recomp.h"\n'
PACING='void func_001A33CC(ppu_context* ctx) {\n    PPU_MM_SNAP\n'
CORRECTION='void func_0016EEBC(ppu_context* ctx) {\n    PPU_MM_SNAP\n'
STORE='loc_0016F304:\n        ctx->gpr[31] = vm_read32(ctx->gpr[2] + -0x2534);\n        ctx->gpr[11] = vm_read32(ctx->gpr[28] + 0x24);\n        vm_write32(ctx->gpr[31] + 0x0, ctx->gpr[11]);\n'
POLL='        { double a = ctx->fpr[1]; double b = ctx->fpr[0]; uint32_t cr_val = (a != a || b != b) ? 1 : (a < b) ? 8 : (a > b) ? 4 : 2; ctx->cr = (ctx->cr & ~(0xFu << 4)) | (cr_val << 4); }\n        if (((ctx->cr >> 7) & 1)) goto loc_001A3C34;\n'


def patch_pacing(directory):
    paths=list(Path(directory).glob('ppu_recomp_*.cpp'))
    sources={p:p.read_text() for p in paths}
    edits={}
    # Validate all present functions before writing any file. Small game-free
    # fixtures contain none; a partially present title is rejected.
    signatures=[PACING,CORRECTION,'void func_0093BD84(ppu_context* ctx) {\n    PPU_MM_SNAP\n','void func_0093BE84(ppu_context* ctx) {\n    PPU_MM_SNAP\n']
    present=[sum(s.count(sig) for s in sources.values()) for sig in signatures]
    names=('void func_001A33CC(', 'void func_0016EEBC(', 'void func_0093BD84(', 'void func_0093BE84(')
    if not any(present) and not any(name in s for name in names for s in sources.values()):return
    if present != [1]*4:raise ValueError('Missing or ambiguous title pacing diagnostic function')
    for sig in signatures:
        p=next(p for p,s in sources.items() if sig in s)
        s=edits.get(p,sources[p]);start=s.index(sig);end=s.index('\n}\n',start)+3
        body=s[start:end]
        if sig==PACING and body.count('loc_001A3C34:\n')!=1:
            raise ValueError('Missing pacing poll instruction anchor')
        replacements=[]
        if sig==PACING:
            replacements=[(PACING,PACING+'    tb_pacing_hook(ctx, 0);\n'),(POLL,POLL.replace('        if (','        tb_pacing_hook(ctx, 1);\n        if (',1))]
        elif sig==CORRECTION:replacements=[(STORE,STORE+'        tb_pacing_hook(ctx, 2);\n')]
        else:
            nid,op=('1F953B9F',5) if '0093BD84' in sig else ('9647570B',3)
            old=f'        ps3_hle_call(0x{nid}u, ctx); return;  /* import stub */\n'
            new=f'        tb_pacing_hook(ctx, {op});\n        ps3_hle_call(0x{nid}u, ctx);\n        tb_pacing_hook(ctx, {op+1});\n        return;  /* import stub */\n'
            replacements=[(old,new)]
        for old,new in replacements:
            if body.count(new)==1:continue
            if body.count(old)!=1:raise ValueError('Unexpected title pacing diagnostic instruction layout')
            body=body.replace(old,new,1)
        s=s[:start]+body+s[end:]
        if DECLARATION not in s:
            if s.count(INCLUDE)!=1:raise ValueError('Missing title include for pacing diagnostic')
            s=s.replace(INCLUDE,INCLUDE+DECLARATION,1)
        edits[p]=s
    for p,s in edits.items():
        if s!=sources[p]:p.write_text(s)
