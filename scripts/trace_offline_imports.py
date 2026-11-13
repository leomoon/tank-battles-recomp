#!/usr/bin/env python3
"""Record local static call sites and bounded observations for PSN-related imports."""
import json
from pathlib import Path
import re
from port import ROOT

LIBRARIES={'sceNp','sceNp2','sceNpTus','cellNetCtl','cellSysutilAvc2'}
# Names resolved by hashing RPCS3's public export names with the PS3 NID salt.
RESOLVED={
 '0x481CE0E8':'sceNpBasicAbortGui','0xDB2E4DC2':'sceNpScoreGetGameDataAsync',
 '0xF76847C2':'sceNpScoreRecordGameDataAsync','0x430CE063':'sceNpMatching2AbortContextStart',
 '0xBB2877F2':'sceNpTusGetMultiSlotVariableAsync',
}

def main():
    report=json.loads((ROOT/'analysis/step7/import-audit.json').read_text())
    imports=[dict(item) for item in report['imports'] if item['library'] in LIBRARIES]
    indexed={int(item['stub'],16):item for item in imports}
    for item in imports:
        item['call_sites']=[]
        item['resolved_name']=item['resolved_name'] or RESOLVED.get(item['nid'])
    current=None
    declaration=re.compile(r'^void func_([0-9A-Fa-f]+)\(')
    invocation=re.compile(r'ctx->lr = (0x[0-9A-Fa-f]+); func_([0-9A-Fa-f]+)\(ctx\)')
    for source in (ROOT/'recompiled').glob('ppu_recomp_*.cpp'):
        with source.open() as stream:
            for line in stream:
                match=declaration.match(line)
                if match:current='0x'+match[1]
                if 'ctx->lr =' not in line:continue
                match=invocation.search(line)
                if match and int(match[2],16) in indexed:
                    indexed[int(match[2],16)]['call_sites'].append(dict(function=current,return_address=match[1]))
    log=(ROOT/'analysis/step7/game-boot.log').read_text(errors='replace')
    aliases={int(alias,16):int(item['nid'],16) for item in imports for alias in item['port_override_aliases']}
    observed={}
    for nid,lr in re.findall(r'\[HLETRACE\] nid=(0x[0-9A-Fa-f]+).*?lr=(0x[0-9A-Fa-f]+)',log):
        number=int(nid,16)
        observed.setdefault(aliases.get(number,number),set()).add(lr)
    for item in imports:
        item['bounded_boot_return_addresses']=sorted(observed.get(int(item['nid'],16),set()))
        item['observed_in_bounded_boot']=bool(item['bounded_boot_return_addresses'])
    result=dict(trace_aliases='Private port NIDs mapped back using the captured registration audit.',imports=imports,observed=[dict(library=i['library'],name=i['resolved_name'],nid=i['nid'],
                return_addresses=i['bounded_boot_return_addresses']) for i in imports if i['observed_in_bounded_boot']],
                categories={
                 'local_setup':'NP/NP2, TUS, Score, Matching2 initialization and local context registration may succeed without PSN.',
                 'online_requests':'Leaderboard transactions, invitations, rooms, signaling connections and voice sessions fail offline.',
                 'drm':'DRM assets and purchased unlock verification are local and remain independent of PSN.'},
                limitations=['Static call sites are not proof that a path was reached.',
                             'The raw-SPU wait prevents observation of later title/menu initialization.',
                             'The missing-unlock guest worker polls NP status with a 10 ms sleep; the port does not fabricate sign-in to terminate it.'])
    (ROOT/'analysis/step7/network-trace.json').write_text(json.dumps(result,indent=2)+'\n')
    print('Recorded',len(imports),'imports and',sum(len(i['call_sites']) for i in imports),'static call sites.')
    print('Bounded boot observed:',', '.join(i['resolved_name'] or i['nid'] for i in imports if i['observed_in_bounded_boot']))

if __name__=='__main__':main()
