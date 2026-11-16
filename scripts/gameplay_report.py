"""Validate matched live-gameplay windows and summarize exact monotonic cadence."""
import json,math,re
from pathlib import Path

STREAMS=('guest_flip','completed_frame','window_present')
COMPAT=('scene','guest_resolution','internal_surfaces','window_size','gpu','driver','audio','input_source','build_type','compiler','effective_flags','diagnostics','sdk_verbose','overlay','executable_sha256','sdk_manifest_sha256','source_commit','source_inputs_sha256','submission_mode','texture_decode_mode','depth_conversion','actual_audio','swapchain_sizes')

def renderer_failures(log):
    pattern=r'\[(?:rsx engine|RSX vulkan)\].*(?:failed|unsupported|unhandled|skipped|object table full|stale .* release)'
    failures=[]
    for line in log.splitlines():
        if re.search(pattern,line,re.I):
            failures.append(line)
            if len(failures)>=64:break
    return failures

def percentile(values,q):
    values=sorted(values)
    return values[min(len(values)-1,max(0,math.ceil(len(values)*q)-1))]

def summarize(directory, minimum_seconds=60):
    directory=Path(directory);m=json.loads((directory/'measurement.json').read_text())
    if (directory/'invalid-scene.json').exists():raise ValueError('Ending scene was not live gameplay')
    session=m.get('session',{})
    if session.get('interrupted') or session.get('launcher_exit_status',0) not in (0,124):raise ValueError('Failed or interrupted session')
    if not m['strict_passed'] or not m['live_gameplay_verified'] or m['phase']!='gameplay':raise ValueError('Need strict-clean verified live gameplay')
    begin,end=m['interval_seconds'];begin_ns,end_ns=round(begin*1e9),round(end*1e9);duration=(end_ns-begin_ns)/1e9
    if minimum_seconds != 60 and (not m['diagnostics'].get('scopes') or not 10<=minimum_seconds<=60):raise ValueError('Short intervals require an explicit CPU/GPU profile')
    if duration<minimum_seconds:raise ValueError('Need complete requested steady interval')
    if not m['visible_entire_interval']:raise ValueError('Hidden-window interval')
    if m.get('renderer_failures'):raise ValueError('Renderer failure in interval/run')
    log=directory/'startup.log'
    if log.exists() and renderer_failures(log.read_text(errors='replace')):
        raise ValueError('Renderer failure in retained startup log')
    if not m.get('actual_audio'):raise ValueError('Actual host audio backend not recorded')
    if any(m['diagnostics'].get(k) for k in ('video','screenshots','textures','resources','input_trace','wait_trace','passes')):raise ValueError('Heavy diagnostics in baseline')
    rows=[json.loads(l) for l in (directory/'gameplay.jsonl').read_text().splitlines()]
    if not rows or rows[0]['elapsed_ns']/1e9>begin or rows[-1]['elapsed_ns']/1e9<end:raise ValueError('Incomplete timing coverage')
    out={}
    for k,name in enumerate(STREAMS):
        events=[t for r in rows for t in r['events'][k]]
        if any(b<a for a,b in zip(events,events[1:])):raise ValueError('Nonmonotonic events')
        selected=[t for t in events if begin_ns<=t<end_ns]
        if len(selected)<2:raise ValueError('Insufficient '+name)
        times=[(b-a)/1e6 for a,b in zip(selected,selected[1:])]
        out[name]=dict(frames=len(selected),fps=len(selected)/duration,mean_ms=sum(times)/len(times),p50_ms=percentile(times,.5),p95_ms=percentile(times,.95),p99_ms=percentile(times,.99),max_ms=max(times))
    return dict(manifest=m,cadence=out,baseline_eligible=not any(m['diagnostics'].values()) and m.get('sdk_verbose') is False,scope='Timing measures selected completed guest serials, not pixel uniqueness or simulation speed. Confirm scene and wall-clock progression separately.')

def compare(reports,allowed_variants=(),candidate_plan=None):
    if set(allowed_variants)-{'overlay','texture_decode_mode','depth_conversion'}:raise ValueError('Unsupported comparison variant')
    if not reports or any(not r['baseline_eligible'] for r in reports):raise ValueError('Comparison needs clean baselines with verbose SDK logging explicitly disabled; keep profiles separate')
    build_variants=()
    if candidate_plan is not None:
        from build_identity import validate_candidate_plan
        build_variants=validate_candidate_plan(reports,candidate_plan)
    if 'texture_decode_mode' in allowed_variants and {r['manifest'].get('texture_decode_mode') for r in reports}!={'reference','specialized'}:raise ValueError('Decoder comparison requires exact reference and specialized modes')
    if 'depth_conversion' in allowed_variants and {r['manifest'].get('depth_conversion') for r in reports}!={'reference','gpu'}:raise ValueError('Depth comparison requires exact reference and gpu modes')
    first=reports[0]['manifest']
    for r in reports[1:]:
        for key in COMPAT:
            if key in allowed_variants or key in build_variants:continue
            if r['manifest'].get(key)!=first.get(key):raise ValueError('Incompatible '+key)
    return reports
