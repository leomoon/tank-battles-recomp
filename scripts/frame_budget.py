#!/usr/bin/env python3
"""Per-frame walker and guest time budget from a TB_FRAME_BUDGET timeline.

  scripts/frame_budget.py report <trace> [--build-dir build-vulkan] [--json out.json]
  scripts/frame_budget.py run --output analysis/performance/<new> [script_gameplay.py options]

The trace is the toolkit's PS3_FTRACE text format (include/ps3emu/ps3_ftrace.h),
recorded only inside verified live gameplay by measure_gameplay.py --frame-budget.
A frame is the time between two flips the RSX walker reached (FLIP_FIFO).

For the walker thread every frame splits into exclusive parts that add up to
the frame: FIFO drain (of which GPU fence waits are a subset), swapchain
present, frame-clock sleep and the remainder (ticks, event pumping, loop).
For each guest PPU thread it splits into traced calls (syscall, usleep poll or
HLE import) and untraced wall time, which also includes scheduling and untraced
waiting. Separate Windows OS counters measure CPU execution. Put latency is the delay from the guest moving the FIFO
put register to the next drain that starts after it.
"""
import argparse
import bisect
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

SYSCALL, USLEEP, DRAIN, FLIP_FIFO, FLIP_REQ, PRESENT, HLE = 1, 2, 7, 8, 9, 10, 15
CLOCK_SLEEP, PUT_ADVANCE, FENCE_WAIT, DRAIN_CALL, VBLANK, PRESENT_FINISH, BATCH_SUBMIT, PUBLISH = 16, 17, 18, 19, 20, 21, 22, 23
FLIP_SEQUENCE, FLIP_EXECUTED, FLIP_TICK, GCM_CALLBACK = 33, 34, 35, 36
SEMAPHORE_WAIT, SEMAPHORE_POST, PRESENT_QUEUE, PRESENT_JOB = 37, 38, 39, 40
COUNT, GPU_CMD, GPU_PASS = 24, 25, 26
# frame_budget.h TB_FC_* ids, in order from 1.
COUNTER_NAMES = ('queue_submit', 'alloc_memory', 'create_image', 'create_buffer', 'render_pass', 'barrier',
                 'descriptor_set_alloc', 'descriptor_write', 'clear_image', 'copy_image_to_buffer',
                 'copy_buffer_to_image', 'copy_image', 'blit_image', 'create_pipeline', 'draw', 'dispatch',
                 'create_framebuffer', 'create_shader', 'sync_submit_wait', 'texture_hash_bytes',
                 'texture_upload_texels', 'guest_color_read_bytes', 'guest_depth_read', 'sparse_hash_bytes',
                 'logical_draw', 'logical_scene_candidate', 'logical_depth_only',
                 'engine_native_draw', 'engine_scaled_draw', 'color_readback', 'color_readback_bytes',
                 'scale_skip_alias', 'scale_skip_stencil', 'scale_resource_reject',
                 'scale_present_native', 'scale_present_scaled', 'scale_fallback_present',
                 'depth_materialize', 'depth_materialize_bytes', 'gpu_depth_convert', 'gpu_depth_convert_bytes',
                 'color_materialize', 'color_materialize_bytes', 'scale_skip_geometry', 'scale_empty_draw',
                 'scale_materialize', 'scale_recovery', 'present_readback_bytes',
                 'present_copy_bytes', 'scale_resolve_bytes', 'scale_retired_bytes',
                 'alloc_memory_bytes', 'fxaa_texels')


def materializations(spans):
    """Identify actual stores/alias conversions by guest resource, never by size."""
    flips=[s[4] for s in spans if s[0]==FLIP_FIFO]
    if len(flips)<2:return dict(status='unavailable',reason='No complete diagnostic frames')
    first,last=min(flips),max(flips)
    kinds={30:'color_guest_store',31:'depth_guest_store',32:'depth_gpu_alias'}
    if not any(s[0] in kinds or s[0]==COUNT and s[2]==43 for s in spans):
        return dict(status='unavailable',reason='Runtime has no resource materialization schema')
    totals={}
    for kind,_,address,amount,t,_ in spans:
        if kind not in kinds or not first<=t<last:continue
        key=(kinds[kind],address>>31,address&0x7fffffff)
        count,size=totals.get(key,(0,0));totals[key]=(count+1,size+amount)
    return dict(status='observed',frames=len(flips)-1,resources=[
        dict(operation=op,location=location,offset=hex(offset),calls=count,bytes=size,
             calls_per_frame=count/(len(flips)-1),bytes_per_frame=size/(len(flips)-1))
        for (op,location,offset),(count,size) in sorted(totals.items())],
        limitations=['Completed full guest stores only; aborted partial writes are excluded.',
                     'GPU aliases are conversions, not CPU guest writes. Roles require resolve/consumer evidence.'])


def render_scale_observations(spans):
    """Observed events, not settings or allocations inferred to be active frames."""
    flips = [s[4] for s in spans if s[0] == FLIP_FIFO]
    if len(flips) < 2:
        return dict(status='unavailable', reason='No complete diagnostic frame interval')
    first,last=min(flips),max(flips)
    rows = [s for s in spans if first <= s[4] < last]
    pairs = sorted({(s[2], s[3]) for s in rows if s[0] == 27})
    return dict(status='observed' if pairs else 'unavailable',
                requested_effective_percent=pairs,
                scene_candidate_extents=sorted({(s[2], s[3]) for s in rows if s[0] == 28}),
                present_source_extents=sorted({(s[2], s[3]) for s in rows if s[0] == 29}))
BUDGET_MS = 1000.0 / 60.0
# vulkan_frame_counters.inc TB_TS_TIMED kinds, timed outside render passes.
GPU_COMMAND_NAMES = {1: 'clear-color', 2: 'clear-depth', 3: 'copy-image-to-buffer', 4: 'copy-buffer-to-image',
                     5: 'copy-image', 6: 'blit-image', 7: 'dispatch', 8: 'fxaa'}
SYSCALL_NAMES = {43: 'thread_yield', 44: 'thread_join', 85: 'event_flag_wait', 92: 'semaphore_wait',
                 97: 'lwmutex_lock', 102: 'mutex_lock', 107: 'cond_wait', 113: 'lwcond_wait',
                 130: 'event_queue_receive', 141: 'usleep', 142: 'sleep'}
WALKER_PARTS = ('drain', 'fence_wait', 'drain_cpu', 'present', 'clock_sleep', 'other')


def load(path):
    header, names, spans = '', {}, []
    with open(path) as f:
        for line in f:
            if line.startswith('#'):
                header = line.strip()
            elif line.startswith('T '):
                _, tid, name = line.rstrip('\n').split(' ', 2)
                names[int(tid)] = name
            elif line.strip():
                k, who, a, b, t0, t1 = line.split()
                spans.append((int(k), int(who), int(a, 16), int(b, 16), float(t0) / 1e3, float(t1) / 1e3))
    spans.sort(key=lambda s: s[4])
    return header, names, spans


def nid_names(build_dir):
    path = Path(build_dir) / 'ppu_hle_nids.cpp'
    if not path.exists():
        return {}
    return {int(n, 16): name for n, name in re.findall(r'ps3_hle_register\((0x[0-9A-Fa-f]+)u, "([^"]+)"', path.read_text())}


class Union:
    """Disjoint sorted union of [t0, t1) intervals with fast clipped totals."""

    def __init__(self, intervals):
        merged = []
        for a, b in sorted(intervals):
            if b <= a:
                continue
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        self.starts = [a for a, _ in merged]
        self.ends = [b for _, b in merged]
        self.prefix = [0.0]
        for a, b in merged:
            self.prefix.append(self.prefix[-1] + b - a)

    def within(self, a, b):
        if not self.starts or b <= a:
            return 0.0
        lo = bisect.bisect_right(self.ends, a)
        hi = bisect.bisect_left(self.starts, b)
        if hi <= lo:
            return 0.0
        total = self.prefix[hi] - self.prefix[lo]
        total -= max(0.0, a - self.starts[lo])
        total -= max(0.0, self.ends[hi - 1] - b)
        return total


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))]


def summary(values):
    if not values:
        return None
    return dict(mean=statistics.fmean(values), p50=percentile(values, .5), p95=percentile(values, .95), max=max(values))


def frame_dependencies(spans):
    """Match only unique sequence IDs inside the trace; never pair by proximity."""
    def unique(kind, flip_callback=False):
        values={}
        for row in spans:
            if row[0]==kind and (not flip_callback or row[3]==2):
                values.setdefault(row[2],[]).append(row)
        return {seq:rows[0] for seq,rows in values.items() if len(rows)==1}
    requests,executed,ticks,callbacks=(unique(FLIP_SEQUENCE),unique(FLIP_EXECUTED),
                                      unique(FLIP_TICK),unique(GCM_CALLBACK,True))
    pairs=[]
    for seq in requests.keys() & executed.keys() & ticks.keys() & callbacks.keys():
        req,run,tick,cb=(table[seq] for table in (requests,executed,ticks,callbacks))
        # Executed records carry a request-sequence snapshot. More than one
        # outstanding request can make it ambiguous; reject reversed order or
        # a different display buffer rather than manufacture a latency chain.
        if req[3]!=run[3] or not req[4]<=run[4]<=tick[4]<=cb[4]:continue
        pairs.append(dict(sequence=seq,request_ms=req[4],executed_ms=run[4],tick_ms=tick[4],
                          callback_start_ms=cb[4],callback_end_ms=cb[5]))
    delays={label:summary([p[end]-p[start] for p in pairs]) for label,start,end in (
        ('request_to_executed_ms','request_ms','executed_ms'),
        ('executed_to_tick_ms','executed_ms','tick_ms'),
        ('tick_to_callback_ms','tick_ms','callback_start_ms'),
        ('callback_work_ms','callback_start_ms','callback_end_ms'))}
    queues,jobs=unique(PRESENT_QUEUE),unique(PRESENT_JOB)
    waits=[jobs[seq][4]-queues[seq][4] for seq in queues.keys() & jobs.keys()
           if jobs[seq][4]>=queues[seq][4]]
    semaphores={}
    for kind,who,sem,value,start,end in spans:
        if kind not in (SEMAPHORE_WAIT,SEMAPHORE_POST):continue
        bucket=semaphores.setdefault((who,sem),dict(waits=[],timeouts=0,posts=0,tokens=0))
        if kind==SEMAPHORE_WAIT:
            bucket['waits'].append(end-start)
            bucket['timeouts']+=int(value!=0)
        else:bucket['posts']+=1;bucket['tokens']+=value
    sems=[dict(thread=who,semaphore=sem,waits=len(b['waits']),failed_waits=b['timeouts'],
               total_wait_ms=sum(b['waits']),wait_ms=summary(b['waits']),posts=b['posts'],tokens=b['tokens'])
          for (who,sem),b in semaphores.items()]
    return dict(status='observed' if ticks else 'unavailable',matched_flip_sequences=len(pairs),
                delays=delays,present_queue_delay_ms=summary(waits),
                present_job_ms=summary([r[5]-r[4] for r in spans if r[0]==PRESENT_JOB]),
                semaphores=sorted(sems,key=lambda r:-r['total_wait_ms']),sequences=sorted(pairs,key=lambda r:r['request_ms']),
                limitations=['Only unique, ordered request/executed/tick/callback sequences are matched.',
                             'Executed sequence is a request snapshot; ambiguous or reversed records are excluded.',
                             'Callback and semaphore spans are wall time and can include preemption.',
                             'Present serials belong to a separate queue; do not equate them with guest flip sequences.'])


def analyze(spans, names=None, nids=None):
    names, nids = names or {}, nids or {}
    flips = sorted(s[4] for s in spans if s[0] == FLIP_FIFO)
    frames = [(a, b) for a, b in zip(flips, flips[1:]) if b > a]
    if not frames:
        raise ValueError('fewer than two walker flips in the trace')
    walker = {k: Union((s[4], s[5]) for s in spans if s[0] == kind and s[1] == 0)
              for k, kind in (('drain', DRAIN_CALL), ('fence_wait', FENCE_WAIT), ('present', PRESENT), ('clock_sleep', CLOCK_SLEEP))}
    guest_kinds = (SYSCALL, USLEEP, HLE)
    # Guest PPU thread ids are small; a host thread making an lv2 call has none.
    threads = sorted({s[1] for s in spans if s[0] in guest_kinds and s[1] < 256})
    blocked = {t: Union((s[4], s[5]) for s in spans if s[0] in guest_kinds and s[1] == t) for t in threads}
    polls = {t: Union((s[4], s[5]) for s in spans if s[0] == USLEEP and s[1] == t) for t in threads}

    rows = []
    for a, b in frames:
        length = b - a
        row = dict(start_ms=a, frame_ms=length)
        for part in ('drain', 'fence_wait', 'present', 'clock_sleep'):
            row[part] = walker[part].within(a, b)
        row['fence_wait'] = min(row['fence_wait'], row['drain'])
        row['drain_cpu'] = row['drain'] - row['fence_wait']
        row['other'] = max(0.0, length - row['drain'] - row['present'] - row['clock_sleep'])
        row['guest'] = {t: dict(blocked=blocked[t].within(a, b), usleep=polls[t].within(a, b)) for t in threads}
        for t in threads:
            g = row['guest'][t]
            g['running'] = max(0.0, length - g['blocked'])
        rows.append(row)

    # Where each guest thread blocks, by cause, over all frames.
    first, last = frames[0][0], frames[-1][1]
    causes = {}
    for k, who, x, _, t0, t1 in spans:
        if k not in guest_kinds or t1 <= first or t0 >= last:
            continue
        label = ('usleep' if k == USLEEP else
                 'sys_' + SYSCALL_NAMES.get(x, str(x)) if k == SYSCALL else
                 nids.get(x, 'hle_%08X' % x))
        bucket = causes.setdefault(who, {})
        bucket[label] = bucket.get(label, 0.0) + min(t1, last) - max(t0, first)

    drain_starts = sorted(s[4] for s in spans if s[0] == DRAIN_CALL)
    latency = []
    for s in spans:
        if s[0] != PUT_ADVANCE or not (first <= s[4] < last):
            continue
        i = bisect.bisect_left(drain_starts, s[4])
        if i < len(drain_starts):
            latency.append(drain_starts[i] - s[4])

    count = len(rows)
    walker_mean = {part: sum(r[part] for r in rows) / count for part in WALKER_PARTS}
    frame_mean = sum(r['frame_ms'] for r in rows) / count
    guests = []
    for t in threads:
        run = sum(r['guest'][t]['running'] for r in rows) / count
        top = sorted(causes.get(t, {}).items(), key=lambda kv: -kv[1])[:6]
        guests.append(dict(tid=t, name=names.get(t, ''), running_ms=run,
                           blocked_ms=sum(r['guest'][t]['blocked'] for r in rows) / count,
                           usleep_ms=sum(r['guest'][t]['usleep'] for r in rows) / count,
                           top_blocking_ms_per_frame=[(label, total / count) for label, total in top]))
    guests.sort(key=lambda g: -g['running_ms'])
    walker_sum = sum(walker_mean[p] for p in ('drain', 'present', 'clock_sleep', 'other'))
    vblanks = sum(1 for s in spans if s[0] == VBLANK and first <= s[4] < last)
    submits = [s for s in spans if s[0] == BATCH_SUBMIT and first <= s[4] < last]
    publications = sum(1 for s in spans if s[0] == PUBLISH and first <= s[4] < last)
    fence_waits = sum(1 for s in spans if s[0] == FENCE_WAIT and first <= s[4] < last)
    finishing = Union((s[4], s[5]) for s in spans if s[0] == PRESENT_FINISH)
    present_thread = sum(finishing.within(a, b) for a, b in frames) / count
    return dict(render_scale=render_scale_observations(spans), frames=count, fps=1000.0 / frame_mean, vblank_hz=vblanks * 1000.0 / (last - first) if vblanks else None, frame_ms=summary([r['frame_ms'] for r in rows]),
                walker_ms_per_frame=walker_mean, present_thread_ms_per_frame=present_thread, walker_accounted_fraction=walker_sum / frame_mean,
                guest_threads=guests, put_to_drain_ms=summary(latency), put_advances=len(latency),
                async_submits_per_frame=len(submits) / count,
                idle_submits_per_frame=sum(1 for s in submits if s[2]) / count,
                publications_per_frame=publications / count, fence_waits_per_frame=fence_waits / count,
                drains_per_frame=sum(1 for t in drain_starts if first <= t < last) / count)


def counters_per_frame(spans, frames):
    """Mean per-frame value of each cumulative TB_FT_COUNT counter."""
    series = {}
    for k, _, cid, total, t0, _ in spans:
        if k == COUNT:
            series.setdefault(cid, []).append((t0, total))
    out = {}
    for cid, points in series.items():
        times = [p[0] for p in points]

        def at(t):
            i = bisect.bisect_right(times, t) - 1
            return points[i][1] if i >= 0 else None
        deltas = []
        for a, b in frames:
            x, y = at(a), at(b)
            if y is None:
                deltas.append(0)
            else:
                deltas.append((y - (x if x is not None else 0)) % (1 << 32) if x is not None else 0)
        name = COUNTER_NAMES[cid - 1] if 0 < cid <= len(COUNTER_NAMES) else 'counter_%d' % cid
        out[name] = sum(deltas) / len(frames)
    return out


def gpu_per_frame(spans, frames):
    """GPU execution from TB_FT_GPU_CMD/PASS, attributed to the frame in which
    the CPU collected it (one recording later; a per-frame mean, not a phase)."""
    first, last = frames[0][0], frames[-1][1]
    cmd = [s for s in spans if s[0] == GPU_CMD and first <= s[4] < last]
    if not cmd:
        return None
    passes = {}
    for k, _, extent, ns, t0, _ in spans:
        if k == GPU_PASS and first <= t0 < last:
            key = (GPU_COMMAND_NAMES.get((extent >> 16) & 0x7FFF, 'command-%d' % ((extent >> 16) & 0x7FFF))
                   if extent & 0x80000000 else '%dx%d' % (extent >> 16, extent & 0xFFFF))
            n, total = passes.get(key, (0, 0))
            passes[key] = (n + 1, total + ns)
    count = len(frames)
    return dict(busy_ms=sum(s[3] for s in cmd) / 1e6 / count, command_buffers=len(cmd) / count,
                passes_by_extent=sorted(((key, n / count, total / 1e6 / count) for key, (n, total) in passes.items()),
                                        key=lambda r: -r[2]))


def headroom(result):
    """Scope-based resource estimates; untraced wall time is not OS CPU time."""
    guests = result['guest_threads']
    walker = result['walker_ms_per_frame']
    parts = dict(main_guest_thread=guests[0]['running_ms'] if guests else 0.0,
                 walker_busy=walker['drain_cpu'] + walker['present'],
                 present_thread=result.get('present_thread_ms_per_frame') or 0.0)
    if result.get('gpu'):
        parts['gpu_busy'] = result['gpu']['busy_ms']
    limiter = max(parts, key=parts.get)
    return dict(budget_ms=BUDGET_MS, busy_ms=parts, limiter=limiter, headroom_ms=BUDGET_MS - parts[limiter],
                basis='Scope wall-time estimates: CPU entries include untraced waits and scheduling delay. See os_cpu for Windows execution counters.')


def render(result):
    lines = [f"{result['frames']} walker frames, {result['fps']:.2f} FPS; frame ms mean {result['frame_ms']['mean']:.2f} "
             f"p50 {result['frame_ms']['p50']:.2f} p95 {result['frame_ms']['p95']:.2f} max {result['frame_ms']['max']:.2f}",
             'walker ms/frame: ' + '  '.join(f"{k} {v:.2f}" for k, v in result['walker_ms_per_frame'].items()),
             f"walker parts cover {100 * result['walker_accounted_fraction']:.1f}% of frame time; "
             f"{result['drains_per_frame']:.1f} drains/frame"]
    line = (f"per frame: {result['publications_per_frame']:.1f} label/reference publications, "
            f"{result['async_submits_per_frame']:.1f} async batch submits ({result['idle_submits_per_frame']:.1f} at idle), "
            f"{result['fence_waits_per_frame']:.1f} fence waits")
    if 'gpu_submissions_per_frame' in result:
        line += (f"; {result['gpu_submissions_per_frame']:.1f} GPU submissions, "
                 f"{result['publications_per_gpu_submission']:.1f} publications per submission")
    lines.append(line)
    if result.get('present_thread_ms_per_frame'):
        lines.append(f"present thread {result['present_thread_ms_per_frame']:.2f} ms/frame (off the walker)")
    if result.get('vblank_hz'):
        lines.append(f"vblank beat {result['vblank_hz']:.2f} Hz")
    dependency=result.get('frame_dependencies',{})
    if dependency.get('status')=='observed':
        lines.append(f"sequence-linked flip chains: {dependency['matched_flip_sequences']}")
        for label,value in dependency['delays'].items():
            if value:lines.append(f"  {label}: mean {value['mean']:.3f}, p95 {value['p95']:.3f}, max {value['max']:.3f}")
    p = result['put_to_drain_ms']
    if p:
        lines.append(f"put->drain ms: mean {p['mean']:.3f} p50 {p['p50']:.3f} p95 {p['p95']:.3f} max {p['max']:.3f} "
                     f"({result['put_advances']} advances)")
    if result.get('fence_waits_by_site'):
        lines.append('fence waits per frame by site (count, ms):')
        for label, n, ms in result['fence_waits_by_site'][:8]:
            lines.append(f"  {label:44s} {n:6.2f} {ms:7.3f}")
    if result.get('gpu'):
        g = result['gpu']
        lines.append(f"GPU busy {g['busy_ms']:.2f} ms/frame over {g['command_buffers']:.1f} command buffers; render passes by target and timed commands:")
        for key, n, ms in g['passes_by_extent'][:12]:
            lines.append(f"  {key:>10s} {n:6.1f} passes {ms:7.3f} ms")
    if result.get('counters'):
        lines.append('work per frame: ' + ', '.join(f"{k} {v:.1f}" for k, v in sorted(result['counters'].items())))
    if result.get('headroom'):
        h = result['headroom']
        lines.append('scope estimates ms/frame vs %.2f budget: ' % h['budget_ms'] +
                     '  '.join(f"{k} {v:.2f}" for k, v in h['busy_ms'].items()) +
                     f"; limiter {h['limiter']}, headroom {h['headroom_ms']:+.2f} ms")
        lines.append(h['basis'])
    cpu=result.get('os_cpu',{})
    if cpu.get('status')=='observed':
        lines.append(f"Windows OS CPU execution: {cpu['process_logical_cores']:.3f} process cores, "
                     f"{cpu['process_cpu_ms_per_frame']:.2f} CPU ms/completed frame "
                     f"over {cpu['coverage_seconds']:.2f}s / {cpu['completed_frames']} completed frames")
        for thread in cpu['threads'][:8]:
            lines.append(f"  {thread['name']:30s} {thread['cpu_ms_per_frame']:6.2f} CPU ms/frame, {thread['logical_cores']:.3f} cores")
    lines.append('guest threads (ms/frame):')
    for g in result['guest_threads'][:8]:
        top = ', '.join(f"{label} {ms:.2f}" for label, ms in g['top_blocking_ms_per_frame'])
        lines.append(f"  {g['tid']:>3} {g['name'][:24]:24s} untraced {g['running_ms']:6.2f} traced calls {g['blocked_ms']:6.2f}  [{top}]")
    return '\n'.join(lines)


def wait_sites(log_path):
    path = Path(log_path)
    if not path.exists():
        return {}
    return {int(n): label for n, label in re.findall(r'^\[frame-budget\] site (\d+) (\S+)', path.read_text(errors='replace'), re.M)}


def fence_sites(spans, sites, first, last, frames):
    totals = {}
    for k, _, site, _, t0, t1 in spans:
        if k == FENCE_WAIT and first <= t0 < last:
            label = sites.get(site, 'site-%d' % site)
            n, ms = totals.get(label, (0, 0.0))
            totals[label] = (n + 1, ms + t1 - t0)
    return sorted(((label, n / frames, ms / frames) for label, (n, ms) in totals.items()), key=lambda r: -r[2])


def report(trace, build_dir='build-vulkan', json_path=None):
    header, names, spans = load(trace)
    result = analyze(spans, names, nid_names(build_dir))
    flips = sorted(s[4] for s in spans if s[0] == FLIP_FIFO)
    result['fence_waits_by_site'] = fence_sites(spans, wait_sites(Path(trace).parent / 'startup.log'),
                                                flips[0], flips[-1], result['frames'])
    # A serial site ("wait:") submits its own command buffer; slot reuse and
    # completion waits only wait for batches already counted as submitted.
    serial = sum(n for label, n, _ in result['fence_waits_by_site'] if label.startswith('wait:'))
    if result['fence_waits_by_site']:
        result['gpu_submissions_per_frame'] = result['async_submits_per_frame'] + serial
        result['publications_per_gpu_submission'] = result['publications_per_frame'] / max(result['gpu_submissions_per_frame'], 1e-9)
    frames = [(a, b) for a, b in zip(flips, flips[1:]) if b > a]
    result['counters'] = counters_per_frame(spans, frames)
    result['materializations'] = materializations(spans)
    result['gpu'] = gpu_per_frame(spans, frames)
    result['headroom'] = headroom(result)
    result['frame_dependencies'] = frame_dependencies(spans)
    from windows_cpu_report import analyze as os_cpu
    result['os_cpu'] = os_cpu(Path(trace).parent)
    result['trace_header'] = header
    if json_path:
        Path(json_path).write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    rep = commands.add_parser('report', help='Analyze an existing trace')
    rep.add_argument('trace')
    rep.add_argument('--build-dir', default='build-vulkan')
    rep.add_argument('--json')
    run = commands.add_parser('run', help='Quick 20-second Battle bench with the timeline (non-acceptance evidence)')
    run.add_argument('--output', required=True)
    run.add_argument('--build-dir', default='build-vulkan')
    run.add_argument('--seconds', type=int, default=20)
    args, extra = parser.parse_known_args()
    if args.command == 'report':
        if extra:
            parser.error('unexpected arguments: ' + ' '.join(extra))
        print(render(report(args.trace, args.build_dir, args.json)))
        return
    here = Path(__file__).resolve().parent
    command = [sys.executable, str(here / 'script_gameplay.py'), '--output', args.output, '--build-dir', args.build_dir,
               '--quick', '--frame-budget', '--profile-seconds', str(args.seconds)] + extra
    status = subprocess.run(command).returncode
    from port import resolve
    out = resolve(args.output)
    trace = out / 'frame-budget.trace'
    if trace.exists():
        result = report(trace, args.build_dir, out / 'frame-budget.json')
        print(render(result))
    else:
        print('no frame-budget trace was written', file=sys.stderr)
        status = status or 1
    raise SystemExit(status)


if __name__ == '__main__':
    main()
