#!/usr/bin/env python3
"""Compare saved local rendering session reports without launching a game."""
import argparse
import json
from pathlib import Path
from statistics import mean
from port import resolve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', nargs='+')
    parser.add_argument('--output', required=True)
    parser.add_argument('--gameplay', action='store_true', help='Compare strict validated live-gameplay manifests and exact cadence')
    parser.add_argument('--overlay-ab',action='store_true',help='Explicit gameplay-only overlay on/off comparison; all other manifest fields must match')
    parser.add_argument('--candidate-plan',help='Explicit JSON baseline/candidate build identities and reason; gameplay only')
    parser.add_argument('--texture-decode-ab',action='store_true',help='Explicit reference/specialized decoder comparison on an otherwise identical build')
    parser.add_argument('--depth-gpu-ab',action='store_true',help='Explicit same-build reference/GPU depth comparison')
    parser.add_argument('--color-readback-ab',action='store_true',help='Explicit same-build reference/cached color readback comparison')
    args = parser.parse_args()
    if args.depth_gpu_ab and not args.gameplay:parser.error('--depth-gpu-ab requires --gameplay')
    if args.color_readback_ab and not args.gameplay:parser.error('--color-readback-ab requires --gameplay')
    if args.texture_decode_ab and not args.gameplay:parser.error('--texture-decode-ab requires --gameplay')
    if args.candidate_plan and not args.gameplay:parser.error('--candidate-plan requires --gameplay')
    if args.gameplay:
        from gameplay_report import summarize, compare
        try:
            result=compare([summarize(resolve(p)) for p in args.runs],allowed_variants=tuple(k for k,on in [('overlay',args.overlay_ab),('texture_decode_mode',args.texture_decode_ab),('depth_conversion',args.depth_gpu_ab),('color_readback_mode',args.color_readback_ab)] if on),candidate_plan=json.loads(resolve(args.candidate_plan).read_text()) if args.candidate_plan else None)
        except (ValueError,KeyError) as error:
            parser.error(str(error))
        target=resolve(args.output);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,indent=2)+'\n');return
    reports = []
    for value in args.runs:
        path = resolve(value) / 'session.json'
        report = json.loads(path.read_text())
        if not report.get('strict_passed') or report['session']['launcher_exit_status'] != 124 or any('error' in e for e in report.get('actions', [])):
            parser.error('Exclude failed or interrupted scenarios: ' + str(path))
        timing = report.get('timing')
        if not timing or 'error' in timing:
            parser.error('No steady timing summary in ' + str(path))
        reports.append(dict(path=str(path), profile=report['profile_enabled'],
            cadence_only=report.get('cadence_only', False), capture=report['capture_enabled'],
            batch=report['batch'], driver=report['driver'], strict_passed=report['strict_passed'],
            timing=timing))
    groups = {}
    for report in reports:
        key = f"{report['driver']}:batch={report['batch']}:scopes={report['profile']}:capture={report['capture']}"
        groups.setdefault(key, []).append(report['timing']['presents_per_second'])
    result = dict(runs=reports, cadence_groups={k: dict(runs=len(v), mean_fps=mean(v), min_fps=min(v), max_fps=max(v)) for k, v in groups.items()},
        limitations='Inclusive CPU scope times overlap. Cadence-only still has frame telemetry and normal diagnostics. Capture/pass-dump runs are not performance baselines. Repeated ranges describe variability, not a controlled causal speedup or pure profiler overhead. RPCS3 compilation time is excluded from steady comparisons.')
    target = resolve(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + '\n')
    for key, group in result['cadence_groups'].items():
        print(key, group)


if __name__ == '__main__':
    main()
