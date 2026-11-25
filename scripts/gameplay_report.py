"""Validate matched live-gameplay windows and summarize exact monotonic cadence."""
import json,math,re
from pathlib import Path

STREAMS=('guest_flip','completed_frame','window_present')
COMPAT=('scene','guest_resolution','internal_surfaces','window_size','gpu','driver','audio','input_source','build_type','compiler','effective_flags','diagnostics','sdk_verbose','overlay','executable_sha256','sdk_manifest_sha256','source_commit','source_inputs_sha256','submission_mode','texture_decode_mode','depth_conversion','color_readback_mode','draw_remap_mode','fence_wait_mode','label_publication','batch_submission','surface_blits','vblank_hz','actual_audio','swapchain_sizes','render_scale','window_mode','fxaa','raw_spu_idle')

class FrameProgress:
    """Detect a stopped completed-frame stream inside an active timing window."""
    def __init__(self):
        self.frames=None
        self.advanced_ns=None
        self.active=False

    def observe(self,frames,now_ns,active):
        entering=active and not self.active
        self.active=active
        if not active or entering or self.frames is None or frames!=self.frames:
            self.frames=frames
            self.advanced_ns=now_ns
            return None
        if now_ns-self.advanced_ns>=5_000_000_000:
            return dict(reason='No completed game frames for five seconds',completed_frames=frames,
                        last_progress_qpc_ns=self.advanced_ns,observed_qpc_ns=now_ns)
        return None

def renderer_failures(log):
    pattern=r'\[(?:rsx engine|RSX vulkan)\].*(?:failed|unsupported|unhandled|skipped|object table full|stale .* release)'
    failures=[]
    for line in log.splitlines():
        if re.search(pattern,line,re.I) or re.search(r'\[cellGcmSys\].*FIFO (?:resync|walker circling)',line):
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
    # A route run with --pending-review keeps its boundary images for later
    # human review when countdown OCR fails; it is timing-diagnostic only.
    pending=bool((m.get('scene_evidence') or {}).get('pending_review'))
    if not m['strict_passed'] or not (m['live_gameplay_verified'] or pending) or m['phase']!='gameplay':raise ValueError('Need strict-clean verified live gameplay')
    begin,end=m['interval_seconds'];begin_ns,end_ns=round(begin*1e9),round(end*1e9);duration=(end_ns-begin_ns)/1e9
    if minimum_seconds != 60 and (not (m['diagnostics'].get('scopes') or m.get('quick_bench')) or not 10<=minimum_seconds<=60):raise ValueError('Short intervals require an explicit CPU/GPU profile or quick bench')
    if duration<minimum_seconds:raise ValueError('Need complete requested steady interval')
    if not m['visible_entire_interval']:raise ValueError('Hidden-window interval')
    if m.get('overlay_consistent') is False:raise ValueError('Overlay setting changed during interval')
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
    return dict(manifest=m,cadence=out,scene_verification='pending human review of retained boundary images' if pending else 'verified',baseline_eligible=not pending and m['live_gameplay_verified'] and not m.get('quick_bench',False) and not any(m['diagnostics'].values()) and m.get('sdk_verbose') is False,scope='Timing measures selected completed guest serials, not pixel uniqueness or simulation speed. Confirm scene and wall-clock progression separately.')

def compare(reports,allowed_variants=(),candidate_plan=None):
    if set(allowed_variants)-{'overlay','texture_decode_mode','depth_conversion','color_readback_mode','draw_remap_mode','fence_wait_mode','raw_spu_idle'}:raise ValueError('Unsupported comparison variant')
    if not reports or any(not r['baseline_eligible'] for r in reports):raise ValueError('Comparison needs clean baselines with verbose SDK logging explicitly disabled; keep profiles separate')
    build_variants=()
    if candidate_plan is not None:
        from build_identity import validate_candidate_plan
        build_variants=validate_candidate_plan(reports,candidate_plan)
    if 'texture_decode_mode' in allowed_variants and {r['manifest'].get('texture_decode_mode') for r in reports}!={'reference','specialized'}:raise ValueError('Decoder comparison requires exact reference and specialized modes')
    if 'depth_conversion' in allowed_variants and {r['manifest'].get('depth_conversion') for r in reports}!={'reference','gpu'}:raise ValueError('Depth comparison requires exact reference and gpu modes')
    if 'color_readback_mode' in allowed_variants and {r['manifest'].get('color_readback_mode') for r in reports}!={'reference','cached'}:raise ValueError('Color readback comparison requires reference and cached modes')
    if 'draw_remap_mode' in allowed_variants and {r['manifest'].get('draw_remap_mode') for r in reports}!={'reference','cached'}:raise ValueError('Draw remap comparison requires reference and cached modes')
    if 'fence_wait_mode' in allowed_variants and {r['manifest'].get('fence_wait_mode') for r in reports}!={'blocking','poll'}:raise ValueError('Fence comparison requires blocking and poll modes')
    if 'raw_spu_idle' in allowed_variants and {r['manifest'].get('raw_spu_idle') for r in reports}!={'0','1'}:raise ValueError('Raw-SPU comparison requires explicit polling (0) and idle wait (1) modes')
    first=reports[0]['manifest']
    for r in reports[1:]:
        for key in COMPAT:
            if key in allowed_variants or key in build_variants:continue
            if r['manifest'].get(key)!=first.get(key):raise ValueError('Incompatible '+key)
    return reports
