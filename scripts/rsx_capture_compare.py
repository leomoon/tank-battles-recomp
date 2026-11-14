#!/usr/bin/env python3
"""Decode local RPCS3 v6 captures and compare corresponding native RSX draws.

The installed RPCS3 serializer writes VP words followed by 16384 registers.
It omits inherited transform constants in ordinary RRC captures. Preserve
that absence instead of treating unknown constants as zero. Capture payloads
contain purchased game data and belong only in ignored local directories.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import struct

REG_BYTES=65536
VP_BYTES=544*16
CONST_BYTES=512*16
PIPELINE={
    0x208:'surface_format',0x220:'color_targets',0x300:'dither',
    0x304:'alpha_test',0x308:'alpha_func',0x30c:'alpha_ref',
    0x310:'blend_enable',0x314:'blend_src',0x318:'blend_dst',
    0x31c:'blend_color',0x320:'blend_equation',0x324:'color_mask',
    0xa6c:'depth_func',0xa70:'depth_write',0xa74:'depth_test',
    0x8cc:'fog_mode',0x1d78:'zmin_max_control',
}

def sha(data):return hashlib.sha256(data).hexdigest()

class Reader:
    def __init__(self,data):self.data=data;self.pos=0
    def raw(self,n):
        if n<0 or self.pos+n>len(self.data):raise ValueError('Truncated RSX capture')
        result=self.data[self.pos:self.pos+n];self.pos+=n;return result
    def u32(self):return struct.unpack('<I',self.raw(4))[0]
    def u64(self):return struct.unpack('<Q',self.raw(8))[0]
    def vle(self):
        value=0
        for shift in range(0,35,7):
            b=self.raw(1)[0];value|=(b&127)<<shift
            if not b&128:
                if value>512*1024*1024:raise ValueError('Capture container exceeds diagnostic limit')
                return value
        raise ValueError('Invalid capture container length')

def read_rrc(path):
    path=Path(path)
    with gzip.open(path,'rb') as f:data=f.read(512*1024*1024+1)
    if len(data)>512*1024*1024:raise ValueError('Capture exceeds 512 MiB diagnostic limit')
    r=Reader(data)
    if (r.raw(4),r.u32(),r.u32())!=(b'RRC\0',6,1):raise ValueError('Expected little-endian RPCS3 RRC version 6')
    tiles={}
    for _ in range(r.vle()):
        state=r.raw(432);tiles[r.u64()]=state
    memory={}
    for _ in range(r.vle()):
        offset,location,blob=r.u32(),r.u32(),r.u64();memory[r.u64()]=(location,offset,blob)
    blobs={}
    for _ in range(r.vle()):
        blob=r.raw(r.vle());blobs[r.u64()]=blob
    displays={}
    for _ in range(r.vle()):
        state=r.raw(132);displays[r.u64()]=state
    commands=[]
    for _ in range(r.vle()):
        cmd,value=r.u32(),r.u32();changes=[r.u64() for _ in range(r.vle())]
        tile,display=r.u64(),r.u64()
        if any(k not in memory for k in changes):raise ValueError('Missing referenced memory block')
        if tile and tile not in tiles or display and display not in displays:raise ValueError('Missing captured display/tile state')
        commands.append((cmd,value,changes,tile,display))
    vp=r.raw(VP_BYTES)
    # Ordinary v6 captures exclude transform constants (rsx_serde.hpp).
    regs=list(struct.unpack('<16384I',r.raw(REG_BYTES)))
    if r.pos!=len(data):raise ValueError('Unsupported initial-state layout or trailing capture data')
    if any(blob not in blobs for loc,off,blob in memory.values()):raise ValueError('Missing captured memory data')
    return dict(regs=regs,vp=vp,commands=commands,memory=memory,blobs=blobs,tiles=tiles,displays=displays)

def fp_program(raw):
    code=bytearray();constants=bytearray();off=0
    while off+16<=len(raw):
        inst=raw[off:off+16];off+=16;words=[]
        for w in struct.unpack('>4I',inst):words.append(((w<<16)|(w>>16))&0xffffffff)
        code.extend(inst)
        if any(w&3==2 for w in words[1:]):
            if off+16>len(raw):return None
            constants.extend(raw[off:off+16]);off+=16
        if words[0]&1:return dict(code_sha256=sha(code),constants_sha256=sha(constants),bytes=off,instructions=len(code)//16)
    return None

def vp_program(vp,start):
    if start>=544:return None
    for end in range(start,544):
        if struct.unpack_from('<I',vp,end*16+12)[0]&1:
            return dict(code_sha256=sha(vp[start*16:(end+1)*16]),instructions=end-start+1)
    return None

def snapshot(regs,vp,fp,number,primitive,count,resources=None):
    textures=[]
    for unit in range(16):
        b=(0x1a00+unit*32)//4
        if not regs[b+3]&0x80000000 or not regs[b+6]>>16 or not regs[b+6]&65535:continue
        fmt=regs[b+1];rect=regs[b+6]
        item=dict(unit=unit,location=(fmt&3)-1,offset=regs[b],format=(fmt>>8)&255,
                  width=rect>>16,height=rect&65535,remap=regs[b+4],wrap=regs[b+2],
                  filter=regs[b+5],control=regs[b+3],size1=regs[0x1840//4+unit])
        if resources and unit in resources:item.update(resources[unit])
        textures.append(item)
    return dict(draw=number,primitive=primitive,count=count,fp=fp,vp=vp_program(vp,regs[0x1ea0//4]),
                pipeline={name:regs[address//4] for address,name in PIPELINE.items()},
                target=dict(offset=regs[0x210//4],zeta=regs[0x214//4],clip=[regs[0x200//4]>>16,regs[0x204//4]>>16],pitch=regs[0x20c//4]),
                viewport=[regs[0xa00//4],regs[0xa04//4]],textures=textures)

def reference_manifest(path):
    cap=read_rrc(path);regs=cap['regs'];vp=bytearray(cap['vp']);draws=[];methods=Counter();active={}
    remaining=0;method=0;noninc=False;primitive=0;count=0
    def memory_at(location,offset):
        candidates=[(off,blob) for (loc,off),blob in active.items() if loc==location and off<=offset<off+len(blob)]
        if not candidates:return b''
        off,blob=max(candidates,key=lambda v:v[0]);return blob[offset-off:]
    for cmd,value,changes,tile,display in cap['commands']:
        for key in changes:
            loc,off,blob=cap['memory'][key];active[loc,off]=cap['blobs'][blob]
        if cmd:
            if remaining:raise ValueError('Command header before previous command completed')
            remaining=(cmd>>18)&0x7ff;method=cmd&0xfffc;noninc=bool(cmd&0x40000000)
        elif not remaining:continue # Initial NOP carries display/tile state only.
        if not remaining:continue
        current=method;remaining-=1
        if not noninc:method+=4
        methods[f'{current:04x}']+=1
        regs[current//4]=value
        if 0xb80<=current<0xc00:
            lane=((current-0xb80)//4)&3;slot=regs[0x1e9c//4]
            if slot<544:struct.pack_into('<I',vp,slot*16+lane*4,value)
            if lane==3:regs[0x1e9c//4]+=1
        if current==0x1808:
            if value:primitive=value;count=0
            else:
                fp_addr=regs[0x8e4//4];fp=fp_program(memory_at((fp_addr&3)-1,fp_addr&~3))
                resource={}
                for u in range(16):
                    base=(0x1a00+u*32)//4;fmt=regs[base+1]
                    raw=memory_at((fmt&3)-1,regs[base])
                    if raw:resource[u]=dict(captured_bytes=len(raw),raw_sha256=sha(raw))
                draws.append(snapshot(regs,vp,fp,len(draws),primitive,count,resource))
        if current in (0x1814,0x1824):count+=((value>>24)&255)+1
    if remaining:raise ValueError('Incomplete final RSX command')
    return dict(source=str(path),kind='rpcs3-v6',draws=draws,methods=dict(methods),
                memory_blocks=len(cap['memory']),memory_bytes=sum(map(len,cap['blobs'].values())),
                initial_vp=vp_program(cap['vp'],cap['regs'][0x1ea0//4]),
                limitations=['RPCS3 ordinary RRC captures omit inherited transform constants.',
                    'A capture is one frame, not temporal validation. Resource hashes compare guest bytes, not GPU rendered surfaces.',
                    'Active texture memory lookup is a diagnostic address lookup; overlapping captured ranges need replay before equivalence can be claimed.'])

def native_manifest(prefix):
    prefix=Path(prefix)
    if not prefix.with_suffix('.done').exists():raise ValueError('Native frame did not complete')
    data=prefix.with_suffix('.methods').read_bytes()
    if len(data)%12:raise ValueError('Truncated native method stream')
    draws=[];methods=Counter();transfers=Counter();primitive=0;count=0;resources=defaultdict(dict)
    resource_path=prefix.with_suffix('.resources')
    if resource_path.exists():
        for line in resource_path.read_text().splitlines():
            draw,unit,loc,off,size,hashname=line.split();raw=(prefix.parent/f'memory-{hashname}.raw').read_bytes()
            if len(raw)!=int(size):raise ValueError('Truncated native texture memory')
            resources[int(draw)][int(unit)]=dict(captured_bytes=int(size),raw_sha256=sha(raw))
    for kind,method,arg in struct.iter_unpack('<III',data):
        if kind:transfers[f'{kind-1}:{method:04x}']+=1;continue
        methods[f'{method:04x}']+=1
        if method==0x1808:
            if arg:primitive=arg;count=0
            else:
                state=(prefix.parent/f'{prefix.name}-draw-{len(draws):04d}.state').read_bytes();r=Reader(state)
                regs=list(struct.unpack('<16384I',r.raw(REG_BYTES)));vp=r.raw(VP_BYTES);r.raw(CONST_BYTES)
                fp=fp_program(r.raw(r.u32()))
                if r.pos!=len(state):raise ValueError('Trailing native draw state')
                draws.append(snapshot(regs,vp,fp,len(draws),primitive,count,resources[len(draws)]))
        if method in (0x1814,0x1824):count+=((arg>>24)&255)+1
    return dict(source=str(prefix),kind='native',draws=draws,methods=dict(methods),transfers=dict(transfers))

def draw_key(draw):
    return (draw['primitive'],draw['count'],(draw['fp'] or {}).get('code_sha256'),
            tuple((t['unit'],t['format'],t['width'],t['height']) for t in draw['textures']))

def compare(reference,native):
    candidates=defaultdict(list)
    for draw in native['draws']:candidates[draw_key(draw)].append(draw)
    matches=[];unmatched=[]
    for draw in reference['draws']:
        options=candidates.get(draw_key(draw),[])
        if not options:unmatched.append(draw['draw']);continue
        # Repeated font/UI draws are ambiguous. Compare all candidate state
        # variants; never claim matching solely from one nearest draw number.
        variants=[]
        for other in options:
            differences={key:[value,other['pipeline'][key]] for key,value in draw['pipeline'].items() if value!=other['pipeline'][key]}
            textures=[]
            for a,b in zip(draw['textures'],other['textures']):
                textures.append(dict(unit=a['unit'],same_captured_length=a.get('captured_bytes')==b.get('captured_bytes'),
                    same_guest_bytes=a.get('raw_sha256')==b.get('raw_sha256') if a.get('raw_sha256') and b.get('raw_sha256') else None,
                    state_differences={k:[a[k],b[k]] for k in ('remap','wrap','filter','control','size1') if a[k]!=b[k]}))
            variants.append(dict(native_draw=other['draw'],pipeline_differences=differences,textures=textures,
                same_reconstructed_vp=draw['vp']==other['vp']))
        matches.append(dict(reference_draw=draw['draw'],candidates=variants))
    return dict(reference=reference['source'],native=native['source'],reference_draws=len(reference['draws']),native_draws=len(native['draws']),
        matched_reference_draws=len(matches),unmatched_reference_draws=unmatched,matches=matches,
        reference_methods_not_written_in_native_scope=sorted(set(reference['methods'])-set(native['methods'])),
        limitations='Candidate alignment uses primitive, vertex count, fragment shader instructions and texture dimensions/formats. Vertex shader differences are reported separately. Repeated draws remain ambiguous. A method absent from this frame can be inherited from earlier frames, or recorded at the separate transfer ingress. Different animation/game state is not a missing implementation. No visual fidelity or replay equivalence is asserted.')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--reference',required=True,type=Path);p.add_argument('--native',type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();ref=reference_manifest(a.reference);result=dict(reference=ref)
    if a.native:
        native=native_manifest(a.native);result.update(native=native,comparison=compare(ref,native))
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(reference_draws=len(ref['draws']),native_draws=len(result.get('native',{}).get('draws',[])),report=str(a.output))))

if __name__=='__main__':main()
