"""Attach pacing diagnostics and an opt-in experiment at exact title anchors."""
from pathlib import Path

DECLARATION='extern "C" void tb_pacing_hook(ppu_context*, unsigned);\n'
EXPERIMENT_DECLARATION='extern "C" void tb_pacing_experiment(ppu_context*, unsigned);\n'
INCLUDE='#include "ppu_recomp.h"\n'
PACING='void func_001A33CC(ppu_context* ctx) {\n    PPU_MM_SNAP\n'
CORRECTION='void func_0016EEBC(ppu_context* ctx) {\n    PPU_MM_SNAP\n'
STORE='loc_0016F304:\n        ctx->gpr[31] = vm_read32(ctx->gpr[2] + -0x2534);\n        ctx->gpr[11] = vm_read32(ctx->gpr[28] + 0x24);\n        vm_write32(ctx->gpr[31] + 0x0, ctx->gpr[11]);\n'
POLL='        { double a = ctx->fpr[1]; double b = ctx->fpr[0]; uint32_t cr_val = (a != a || b != b) ? 1 : (a < b) ? 8 : (a > b) ? 4 : 2; ctx->cr = (ctx->cr & ~(0xFu << 4)) | (cr_val << 4); }\n        if (((ctx->cr >> 7) & 1)) goto loc_001A3C34;\n'
CALIBRATION='        ctx->gpr[21] = ctx->gpr[22] - ctx->gpr[23];\n        vm_write64(ctx->gpr[3] + 0x18, ctx->gpr[21]);\n'
FRAME_SAMPLE='        ctx->gpr[28] = vm_read32(ctx->gpr[3] + 0x20);\n'
PHASE_REQUEST='        ctx->gpr[31] = vm_read32(ctx->gpr[2] + -0x2540);\n'
MAIN='void func_000E0A68(ppu_context* ctx) {\n    PPU_MM_SNAP\n'
UPDATE_CALL='        ctx->lr = 0x000E0DA8; func_0001F518(ctx); DRAIN_TRAMPOLINE(ctx);\n'
RENDER_GATE='        ctx->gpr[29] = vm_read8(ctx->gpr[7] + 0x0);\n'
ROLE_IDS='        ctx->gpr[7] = ctx->gpr[29] ^ ctx->gpr[3];\n'


def patch_pacing(directory):
    paths=list(Path(directory).glob('ppu_recomp_*.cpp'))
    sources={p:p.read_text() for p in paths}
    edits={}
    # Validate all present functions before writing any file. Small game-free
    # fixtures contain none; a partially present title is rejected.
    signatures=[PACING,CORRECTION,'void func_0093BD84(ppu_context* ctx) {\n    PPU_MM_SNAP\n','void func_0093BE84(ppu_context* ctx) {\n    PPU_MM_SNAP\n']
    signatures+=['void func_0016DD64(ppu_context* ctx) {\n    PPU_MM_SNAP\n', 'void func_0015B0BC(ppu_context* ctx) {\n    PPU_MM_SNAP\n']
    signatures+=['void func_001D511C(ppu_context* ctx) {\n    PPU_MM_SNAP\n']
    signatures+=['void func_0015361C(ppu_context* ctx) {\n    PPU_MM_SNAP\n']
    lifecycle=['00169438','001696DC','00170A60','001D3A2C','001A3368']
    signatures += [MAIN]
    signatures += [f'void func_{name}(ppu_context* ctx) {{\n    PPU_MM_SNAP\n' for name in lifecycle]
    present=[sum(s.count(sig) for s in sources.values()) for sig in signatures]
    names=tuple(sig.split('ppu_context*')[0] for sig in signatures)
    if not any(present) and not any(name in s for name in names for s in sources.values()):return
    if present != [1]*len(signatures):raise ValueError('Missing or ambiguous title pacing diagnostic function')
    for sig in signatures:
        p=next(p for p,s in sources.items() if sig in s)
        s=edits.get(p,sources[p]);start=s.index(sig);end=s.index('\n}\n',start)+3
        body=s[start:end]
        if sig==PACING and body.count('loc_001A3C34:\n')!=1:
            raise ValueError('Missing pacing poll instruction anchor')
        replacements=[]
        if any(name in sig for name in lifecycle):
            if '00169438' in sig:
                anchor='        vm_write64(ctx->gpr[31] + 0x18, ctx->gpr[3]);\n'
                replacements=[(anchor,anchor+'        tb_pacing_experiment(ctx, 15);\n')]
            elif '001A3368' in sig:
                anchor='        vm_write64(ctx->gpr[9] + 0x110, ctx->gpr[3]);\n'
                replacements=[(anchor,anchor+'        tb_pacing_experiment(ctx, 15);\n')]
            else:replacements=[(sig,sig+'    tb_pacing_experiment(ctx, 15);\n')]
        elif sig==MAIN:
            replacements=[(RENDER_GATE,RENDER_GATE+'        tb_pacing_hook(ctx, 17);\n        tb_pacing_experiment(ctx, 17);\n'), (UPDATE_CALL,'        tb_pacing_hook(ctx, 18);\n'+UPDATE_CALL)]
        elif sig==PACING:
            body=body.replace('        tb_pacing_hook(ctx, 1);\n        if (', '        tb_pacing_hook(ctx, 1);\n        tb_pacing_experiment(ctx, 16);\n        if (')
            replacements=[(PACING,PACING+'    tb_pacing_hook(ctx, 0);\n'),(POLL,POLL.replace('        if (','        tb_pacing_hook(ctx, 1);\n        tb_pacing_experiment(ctx, 16);\n        if (',1))]
        elif sig==CORRECTION:
            peak='loc_0016F014:\n        ctx->gpr[30] = (int64_t)(int32_t)(0);\n'
            replacements=[(STORE,STORE+'        tb_pacing_hook(ctx, 2);\n'),(peak,peak+'        tb_pacing_hook(ctx, 8);\n')]
        elif '0016DD64' in sig:
            anchor='        ctx->gpr[5] = ctx->gpr[26] - ctx->gpr[4];\n'
            replacements=[(anchor,anchor+'        tb_pacing_hook(ctx, 7);\n'),
                          (FRAME_SAMPLE,FRAME_SAMPLE+'        tb_pacing_hook(ctx, 13);\n'),
                          (PHASE_REQUEST,'        tb_pacing_hook(ctx, 12);\n        tb_pacing_experiment(ctx, 12);\n'+PHASE_REQUEST)]
        elif '0015361C' in sig:
            replacements=[(ROLE_IDS,ROLE_IDS+'        tb_pacing_hook(ctx, 14);\n')]
        elif '0015B0BC' in sig:
            anchor='        vm_write32(ctx->gpr[28] + 0x8, ctx->gpr[4]);\n'
            replacements=[(anchor,anchor+'        tb_pacing_hook(ctx, 9);\n')]
        elif '001D511C' in sig:
            replacements=[(CALIBRATION,CALIBRATION+'        tb_pacing_hook(ctx, 11);\n')]
        else:
            nid,op=('1F953B9F',5) if '0093BD84' in sig else ('9647570B',3)
            old=f'        ps3_hle_call(0x{nid}u, ctx); return;  /* import stub */\n'
            new=f'        tb_pacing_hook(ctx, {op});\n        ps3_hle_call(0x{nid}u, ctx);\n        tb_pacing_hook(ctx, {op+1});\n        return;  /* import stub */\n'
            replacements=[(old,new)]
        if sig==PACING:
            reset='        vm_write32(ctx->gpr[31] + 0x124, ctx->gpr[0]);\n'
            hooked=reset+'        tb_pacing_hook(ctx, 10);\n'
            if body.count(reset)!=3:raise ValueError('Unexpected pacing reset layout')
            if body.count(hooked)!=3:
                if 'tb_pacing_hook(ctx, 10)' in body:raise ValueError('Unexpected pacing reset layout')
                body=body.replace(reset,hooked)
        for old,new in replacements:
            if body.count(new)==1:continue
            if body.count(old)!=1:raise ValueError('Unexpected title pacing diagnostic instruction layout')
            body=body.replace(old,new,1)
        # Append provenance hooks after existing numeric observations. The
        # recorder remains read-only and sees the original operands first.
        for op in (2,7,8,11,12,14):
            old=f'        tb_pacing_hook(ctx, {op});\n'
            new=old+f'        tb_pacing_experiment(ctx, {op});\n'
            if old in body and new not in body:body=body.replace(old,new)
        s=s[:start]+body+s[end:]
        if DECLARATION not in s:
            if s.count(INCLUDE)!=1:raise ValueError('Missing title include for pacing diagnostic')
            s=s.replace(INCLUDE,INCLUDE+DECLARATION,1)
        if EXPERIMENT_DECLARATION not in s:s=s.replace(INCLUDE,INCLUDE+EXPERIMENT_DECLARATION,1)
        edits[p]=s
    for p,s in edits.items():
        if s!=sources[p]:p.write_text(s)
