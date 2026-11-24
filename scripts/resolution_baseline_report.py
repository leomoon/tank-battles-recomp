"""Summarize retained resolution-baseline evidence without promoting rejected runs."""
import argparse
import json
from pathlib import Path


def resource_roles(evidence, materializations):
    """Join proven resolves and actually converted depth consumers by address."""
    roles={}
    sampled={(r['location'],int(r['offset'],16)) for r in materializations.get('resources',[])
             if r['operation']=='depth_gpu_alias' and r['calls']>0}
    for frame in evidence.get('frames',{}).values():
        for route in frame.get('qualified_resolves',[]):
            for surface in frame['surfaces']:
                if (surface['color']['location'],surface['color']['offset'])!=(route['color_location'],int(route['color'],16)):continue
                roles[surface['color']['location'],int(route['color'],16)]='scene_color'
                roles[surface['zeta'][0],int(route['zeta'],16)]='scene_depth'
            display=evidence['displays'][str(route['buffer'])]
            roles[display['location'],display['offset']]='display_resolve'
        for surface in frame.get('surfaces',[]):
            depth=tuple(surface['zeta'][:2])
            if surface['target']==0 and depth in sampled:
                roles[depth]='sampled_depth_only'
    return [dict(location=location,offset=hex(offset),role=role)
            for (location,offset),role in sorted(roles.items())]


def resource_summary(rows):
    samples=[r['resources'] for r in rows if r.get('resources',{}).get('status')=='available']
    if len(samples)<2:return dict(status='unavailable',reason='Fewer than two resource snapshots')
    first,last=samples[0],samples[-1]
    seconds=(last['monotonic_ns']-first['monotonic_ns'])/1e9
    if seconds<=0:raise ValueError('Non-increasing resource snapshot time')
    delta=last['process_cpu_ns']-first['process_cpu_ns']
    if delta<0:raise ValueError('Process CPU time regressed')
    start={r['tid']:r for r in first['threads']}
    threads=[]
    for r in last['threads']:
        previous=start.get(r['tid'])
        if previous and r['name']==previous['name'] and r['cpu_ns']>=previous['cpu_ns']:
            cpu=r['cpu_ns']-previous['cpu_ns']
            threads.append(dict(name=r['name'],cpu_ms=cpu/1e6,logical_cores=cpu/1e9/seconds))
    return dict(status='observed',samples=len(samples),sample_window_seconds=seconds,
                process_cpu_ms=delta/1e6,process_logical_cores=delta/1e9/seconds,
                threads=threads,peak_working_set_bytes=max(r['working_set_bytes'] for r in samples),
                peak_private_bytes=max(r['private_bytes'] for r in samples),
                limitations=['CPU deltas cover the retained snapshot window, not necessarily the full gameplay interval.',
                             'Working set/private bytes exclude a validated per-process Intel GPU residency measurement.'])


def report(directory):
    directory=Path(directory)
    manifest=json.loads((directory/'measurement.json').read_text())
    result=dict(scene=manifest['scene'],executable_sha256=manifest['executable_sha256'],
                runtime_manifest_sha256=manifest.get('sdk_manifest_sha256'),
                runtime_source=manifest.get('runtime_source_commit'),requested_scale=manifest.get('render_scale'),
                diagnostics=manifest.get('diagnostics'),thermal=dict(status='unavailable',reason='No validated sampler'),
                clocks=dict(status='unavailable',reason='No validated CPU/GPU clock sampler'),
                power=dict(status='unavailable',reason='No validated package/GPU power sampler'),
                gpu_residency=dict(status='unavailable',reason='No validated per-process Intel GPU residency sampler'))
    from gameplay_report import summarize
    try:
        summary=summarize(directory,minimum_seconds=round(manifest['interval_seconds'][1]-manifest['interval_seconds'][0]))
        result.update(baseline_eligible=summary['baseline_eligible'],cadence=summary['cadence'])
    except (ValueError,TypeError) as error:
        result.update(baseline_eligible=False,measurement_rejection=str(error))
    interval=manifest.get('interval_seconds')
    rows=[json.loads(line) for line in (directory/'observations.jsonl').read_text().splitlines()]
    result['resources']=resource_summary([r for r in rows if interval and interval[0]<=r['seconds']<=interval[1]])
    trace=directory/'frame-budget.trace'
    if trace.exists():
        from frame_budget import report as frame_report
        try:result['frame_budget']=frame_report(trace,manifest.get('build_directory',''),directory/'frame-budget.json')
        except ValueError as error:result['frame_budget']=dict(status='unavailable',reason=str(error))
    else:result['frame_budget']=dict(status='unavailable',reason='No diagnostic trace requested or captured')
    evidence=directory/'render-scale-evidence.json'
    if evidence.exists() and 'materializations' in result['frame_budget']:
        result['resource_roles']=resource_roles(json.loads(evidence.read_text()),result['frame_budget']['materializations'])
        lookup={(r['location'],r['offset']):r['role'] for r in result['resource_roles']}
        for row in result['frame_budget']['materializations'].get('resources',[]):
            row['role']=lookup.get((row['location'],row['offset']),'unclassified')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    result=report(args.directory)
    path=args.directory/'resolution-baseline.json'
    path.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='frame_budget'},indent=2))
