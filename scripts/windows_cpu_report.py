"""Match Windows OS CPU-time deltas to completed frames on the same QPC clock."""
import argparse
import json
from pathlib import Path


def analyze(directory):
    directory=Path(directory)
    required=('measurement.json','observations.jsonl','gameplay.jsonl')
    if any(not (directory/name).is_file() for name in required):
        return dict(status='unavailable',reason='Need measurement, resource observations and gameplay events')
    measurement=json.loads((directory/'measurement.json').read_text())
    interval=measurement.get('interval_seconds')
    events=[json.loads(line) for line in (directory/'gameplay.jsonl').read_text().splitlines()]
    if not interval or not events:
        return dict(status='unavailable',reason='No measured gameplay interval')
    origin=events[0]['monotonic_ns']-events[0]['elapsed_ns']
    begin,end=(origin+round(t*1e9) for t in interval)
    samples=[]
    for line in (directory/'observations.jsonl').read_text().splitlines():
        sample=json.loads(line).get('resources',{})
        if (sample.get('status')=='available' and 'process_cpu_ns' in sample and
                begin<=sample['monotonic_ns']<=end):
            samples.append(sample)
    if len(samples)<2:
        return dict(status='unavailable',reason='Fewer than two OS CPU snapshots inside the interval')
    first,last=samples[0],samples[-1]
    start,stop=first['monotonic_ns'],last['monotonic_ns']
    if stop<=start:raise ValueError('OS CPU snapshot clock did not advance')
    frames=sum(start-origin<=t<stop-origin for row in events for t in row['events'][1])
    if not frames:return dict(status='unavailable',reason='No completed frames between OS snapshots')
    before={t['tid']:t for t in first['threads']}
    after={t['tid']:t for t in last['threads']}
    threads=[]
    for tid in before.keys() & after.keys():
        delta=after[tid]['cpu_ns']-before[tid]['cpu_ns']
        if delta<0:raise ValueError('Thread CPU counter regressed: '+str(tid))
        threads.append(dict(tid=tid,name=after[tid]['name'],cpu_ns=delta,
                            cpu_ms_per_frame=delta/1e6/frames,logical_cores=delta/(stop-start)))
    process=last['process_cpu_ns']-first['process_cpu_ns']
    if process<0:raise ValueError('Process CPU counter regressed')
    residual=process-sum(t['cpu_ns'] for t in threads)
    return dict(status='observed',method='GetThreadTimes/GetProcessTimes user plus kernel CPU',
                coverage_seconds=(stop-start)/1e9,measurement_seconds=interval[1]-interval[0],
                sample_window_qpc_ns=[start,stop],completed_frames=frames,
                process_cpu_ms_per_frame=process/1e6/frames,process_logical_cores=process/(stop-start),
                threads=sorted(threads,key=lambda t:t['cpu_ns'],reverse=True),
                unmatched_thread_ids=sorted(before.keys() ^ after.keys()),
                unattributed_cpu_ms_per_frame=residual/1e6/frames,
                thread_coverage=last.get('thread_coverage','main_walker_present'),
                identity={key:measurement.get(key) for key in ('executable_sha256','runtime_source_commit','render_scale','scene')},
                raw_spu_idle=measurement.get('launch_environment',{}).get('TB_RAW_SPU_IDLE','runtime default'),
                session_checks=dict({key:measurement.get(key) for key in ('strict_passed','live_gameplay_verified','visible_entire_interval','overlay_consistent','renderer_failures','session')},
                                    boundary_rejected=(directory/'invalid-scene.json').exists(),
                                    pending_review=bool((measurement.get('scene_evidence') or {}).get('pending_review'))),
                limitations=['CPU time is execution in user/kernel mode, not time blocked or runnable.',
                             'Snapshots cover a subset of the measured interval; frames use the same bounds.',
                             'Only threads present at both bounds have CPU deltas; the residual also includes query skew.',
                             'These counters do not identify scheduler ready time, stacks or CPU frequency.'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--json',type=Path)
    args=parser.parse_args()
    result=analyze(args.directory)
    text=json.dumps(result,indent=2)+'\n'
    if args.json:args.json.write_text(text)
    else:print(text,end='')
