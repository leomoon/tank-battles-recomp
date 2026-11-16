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
For each guest PPU thread it splits into blocked (syscall, usleep poll or HLE
import) and running. Put latency is the delay from the guest moving the FIFO
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
    return dict(frames=count, fps=1000.0 / frame_mean, vblank_hz=vblanks * 1000.0 / (last - first) if vblanks else None, frame_ms=summary([r['frame_ms'] for r in rows]),
                walker_ms_per_frame=walker_mean, present_thread_ms_per_frame=present_thread, walker_accounted_fraction=walker_sum / frame_mean,
                guest_threads=guests, put_to_drain_ms=summary(latency), put_advances=len(latency),
                async_submits_per_frame=len(submits) / count,
                idle_submits_per_frame=sum(1 for s in submits if s[2]) / count,
                publications_per_frame=publications / count, fence_waits_per_frame=fence_waits / count,
                drains_per_frame=sum(1 for t in drain_starts if first <= t < last) / count)


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
    p = result['put_to_drain_ms']
    if p:
        lines.append(f"put->drain ms: mean {p['mean']:.3f} p50 {p['p50']:.3f} p95 {p['p95']:.3f} max {p['max']:.3f} "
                     f"({result['put_advances']} advances)")
    if result.get('fence_waits_by_site'):
        lines.append('fence waits per frame by site (count, ms):')
        for label, n, ms in result['fence_waits_by_site'][:8]:
            lines.append(f"  {label:44s} {n:6.2f} {ms:7.3f}")
    lines.append('guest threads (ms/frame):')
    for g in result['guest_threads'][:8]:
        top = ', '.join(f"{label} {ms:.2f}" for label, ms in g['top_blocking_ms_per_frame'])
        lines.append(f"  {g['tid']:>3} {g['name'][:24]:24s} running {g['running_ms']:6.2f} blocked {g['blocked_ms']:6.2f}  [{top}]")
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
