"""Validate captured arithmetic against the original title's pacing operations."""
import argparse,bisect,json,statistics
from pathlib import Path
from online_pacing_model import f32,pacing_deadline,signed32


def audit(path, phases=None, gameplay=None, allow_dropped=False):
    rows=[json.loads(line) for line in Path(path).read_text().splitlines()]
    header=rows[0];events=sorted(rows[1:],key=lambda e:e['ns'])
    if header.get('schema')!=1 or header.get('clock')!='host_monotonic_ns':raise ValueError('Unsupported pacing schema or clock')
    if header['events']!=len(events):raise ValueError('Incomplete pacing events')
    if any(not header['start_ns']<=e['ns']<=header['end_ns'] for e in events):raise ValueError('Event outside trace window')
    if not header.get('complete'):raise ValueError('Audit needs a complete lossless trace')
    if header['dropped'] and not allow_dropped:raise ValueError('Audit needs a complete lossless trace')
    def stats(values):
        if not values:return None
        v=sorted(values)
        return dict(count=len(v),mean=statistics.mean(v),min=v[0],p95=v[min(len(v)-1,int(.95*len(v)))],max=v[-1])
    decisions=[e for e in events if e['kind'] in (1,2)]
    mismatches=[e['ns'] for e in decisions if e['deadline_guest_us'] is None or pacing_deadline(e['previous_guest_us'],e['interval_bits'],e['correction_u32'])!=f32(e['deadline_guest_us'])]
    samples=[e for e in events if e['kind']==6]
    sample_mismatches=[e['ns'] for e in samples if (e['observed_guest_us']-e['previous_guest_us'])&0xffffffff!=e['correction_u32']]
    peaks=[e for e in events if e['kind']==7]
    peak_mismatches=[e['ns'] for e in peaks if max(e['previous_guest_us'],e['observed_guest_us'])!=e['correction_u32']]
    peak_ns=[e['ns'] for e in peaks];writes=[e for e in events if e['kind']==3]
    matched=0;different=[];unmatched=0
    for e in writes:
        i=bisect.bisect_right(peak_ns,e['ns'])-1
        if i<0:unmatched+=1
        elif peaks[i]['correction_u32']>>1==e['correction_u32']:matched+=1
        else:different.append(e['ns'])
    result=dict(header=header,lossless=not header['dropped'],decisions=len(decisions),deadline_mismatches=mismatches,
                sample_subtraction_mismatches=sample_mismatches,peak_selection_mismatches=peak_mismatches,
                correction_writes_matching_peak_half=matched,unmatched_boundary_writes=unmatched,
                correction_half_mismatches=different,reset_events=sum(e['kind']==9 for e in events),
                nonzero_reset_events=sum(e['kind']==9 and e['correction_u32']!=0 for e in events))
    result['arithmetic_valid']=not any((mismatches,sample_mismatches,peak_mismatches,different,result['nonzero_reset_events']))
    result['phases']=[]
    frame_rows=[json.loads(l) for l in Path(gameplay).read_text().splitlines()] if gameplay else []
    for phase in phases or []:
        start,end=phase['start_ns'],phase['end_ns']
        if not header['start_ns']<=start<end<=header['end_ns']:raise ValueError('Phase outside trace')
        selected=[e for e in events if start<=e['ns']<end]
        ends=[e for e in selected if e['kind']==2]
        sends=[e for e in selected if e['kind']==8]
        resets=[e for e in selected if e['kind']==9]
        observed=[signed32(e['correction_u32']) for e in selected if e['kind']==6]
        correction=[e['correction_u32'] for e in selected if e['kind']==3]
        row=dict(phase,correction_writes_us=stats(correction),correction_at_pacing_exit_us=stats([e['correction_u32'] for e in ends]),
                 signed_remote_minus_local_snapshot_us=stats(observed),sender_snapshot_age_us=stats([signed32(e['observed_guest_us']-e['previous_guest_us']) for e in sends]),
                 pacing_exit_elapsed_us=stats([e['observed_guest_us']-e['previous_guest_us'] for e in ends]))
        row['pacing_returns_per_second']=len(resets)/((end-start)/1e9)
        row['returns_setting_skip_phase']=sum(bool(e.get('flags',0)&0xff0000) for e in resets)
        if frame_rows:
            frames=[]
            for r in frame_rows:
                origin=r['monotonic_ns']-r['elapsed_ns']
                frames.extend(origin+t for t in r['events'][1] if start<=origin+t<end)
            row['completed_fps']=len(frames)/((end-start)/1e9)
        result['phases'].append(row)
    result['scope']='Retained numerical title arithmetic and local-clock phase evidence only; dropped events prevent complete timeline claims. Not PS3 reference behavior or proof of safe synchronization removal.'
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('trace');p.add_argument('--phases',type=Path);p.add_argument('--gameplay',type=Path);p.add_argument('--json',type=Path)
    p.add_argument('--allow-dropped',action='store_true',help='Audit retained events only; explicitly mark the timeline lossy')
    a=p.parse_args()
    r=audit(a.trace,json.loads(a.phases.read_text()) if a.phases else None,a.gameplay,a.allow_dropped)
    text=json.dumps(r,indent=2)+'\n'
    if a.json:a.json.write_text(text)
    print(text,end='')
