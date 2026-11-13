#!/usr/bin/env python3
"""Capture repeatable local rendering scenarios or measure them without screenshots."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import threading
import time
import tomllib
import uuid

from boot_session import run_session
from port import ROOT, launch_environment, positive_seconds, resolve
from verify_vulkan_rendering import inspect_frame


def summarize_profile(path, warmup):
    if not path.exists():
        return None
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    selected = [row for row in rows if row['elapsed_ns'] >= warmup * 1e9]
    if len(selected) < 2:
        return dict(error='Insufficient steady-state samples', snapshots=len(rows))
    first, last = selected[0], selected[-1]
    seconds = (last['elapsed_ns'] - first['elapsed_ns']) / 1e9
    timings = {}
    for name, end in last['timings'].items():
        start = first['timings'][name]
        n, ns = end['n'] - start['n'], end['ns'] - start['ns']
        bins = [b - a for a, b in zip(start['log2_ns_bins'], end['log2_ns_bins'])]
        def percentile(fraction):
            target, total = max(1, n * fraction), 0
            for i, value in enumerate(bins):
                total += value
                if total >= target:
                    return [2 ** i / 1e6, 2 ** (i + 1) / 1e6 if i < 31 else None]
            return None
        timings[name] = dict(n=n, seconds=ns / 1e9, mean_ms=ns / n / 1e6 if n else None,
                            p50_ms_bounds=percentile(.5) if n else None,
                            p95_ms_bounds=percentile(.95) if n else None)
    return dict(window_seconds=seconds, first_elapsed_seconds=first['elapsed_ns'] / 1e9,
                last_elapsed_seconds=last['elapsed_ns'] / 1e9,
                presents_per_second=timings['frame-interval']['n'] / seconds,
                timings=timings, scope='Inclusive CPU wall times overlap; do not sum them. Histograms use power-of-two bins. Presentation cadence is not simulation speed or GPU execution time.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='Unique ignored analysis/rendering subdirectory')
    parser.add_argument('--build-dir', default='build-vulkan', help='Build containing TankBattlesRecomp')
    parser.add_argument('--content-root', help='Independent local title directory for cache tests; default shares purchased title')
    parser.add_argument('--timeout', type=positive_seconds, default=24)
    parser.add_argument('--warmup', type=positive_seconds, default=10)
    parser.add_argument('--capture', action='store_true', help='Save periodic framebuffer images; adds substantial IO cost')
    parser.add_argument('--wait-trace', action='store_true', help='Record a bounded SDK PPU/SPU wait trace; separate diagnostic run')
    parser.add_argument('--profile', action='store_true', help='Enable bounded inclusive CPU scope timings')
    parser.add_argument('--cadence-only', action='store_true', help='Measure only presentation cadence, leaving scope timers disabled')
    parser.add_argument('--driver', choices=['x11', 'wayland'], default='x11')
    parser.add_argument('--focus-window', action='store_true', help='Focus only the test window through Hyprland and record its visibility')
    parser.add_argument('--no-gamepad', action='store_true', help='Exclude physical gamepads to keep passive timing scenes stable')
    parser.add_argument('--actions', help='JSON array of {seconds, key, hold_seconds}; keyboard scenario on this window only')
    parser.add_argument('--input-trace', action='store_true', help='Trace physical and guest button changes; requires --resources')
    parser.add_argument('--resources', action='store_true', help='Bounded packaged read and imported media-call trace')
    parser.add_argument('--textures', action='store_true', help='Capture bounded pre-GPU decoded textures and transfer metadata')
    parser.add_argument('--passes', action='store_true', help='Save up to 16 diagnostic render passes; substantial overhead')
    parser.add_argument('--batch', choices=['0', '1'], default='0')
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    output = resolve(args.output)
    if not output.is_relative_to(ROOT / 'analysis/rendering'):
        parser.error('--output must be inside ignored analysis/rendering/')
    if output.exists():
        parser.error('Use a new output directory to preserve previous evidence')
    if args.actions and args.driver != 'x11':
        parser.error('Automated keyboard actions require --driver x11')
    if args.profile and args.cadence_only:
        parser.error('Choose --profile or --cadence-only')
    actions = json.loads(resolve(args.actions).read_text()) if args.actions else []
    for action in actions:
        if not isinstance(action.get('seconds'), (int, float)) or action['seconds'] < 0:
            parser.error('Each action needs nonnegative seconds')
        if not re.fullmatch(r'[A-Za-z0-9_]+', action.get('key', '')):
            parser.error('Each action needs a single X11 key name')
    output.mkdir(parents=True)
    config = tomllib.loads((ROOT / 'config.toml').read_text())
    # Isolate native save/cache writes, while retaining the purchased content and entitlement.
    hdd = output / 'profile/dev_hdd0'
    (hdd / 'game').mkdir(parents=True)
    title_id = config['game'].get('title_id', 'NPUB30154')
    source_hdd = resolve(config['paths']['hdd0_root'])
    content = resolve(args.content_root) if args.content_root else source_hdd / 'game' / title_id
    if not (content / 'USRDIR/EBOOT.elf').is_file() or not (content / 'PARAM.SFO').is_file():
        parser.error('Content root needs PARAM.SFO and USRDIR/EBOOT.elf')
    (hdd / 'game' / title_id).symlink_to(content, target_is_directory=True)
    entitlement = source_hdd / 'home/00000001/exdata'
    if entitlement.exists():
        shutil.copytree(entitlement, hdd / 'home/00000001/exdata')
    env = launch_environment(config, str(content / 'PARAM.SFO'))
    for name in tuple(env):
        if name.startswith(('TB_', 'PS3RECOMP_VK_', 'RSX_ENGINE_', 'VK_DRAW_', 'PS3_FTRACE')):
            env.pop(name)
    title = 'Tank Battles scenario ' + uuid.uuid4().hex[:12]
    env.update(PS3_VFS_ROOT=str(content), PS3_GAMEDATA_ROOT=str(output / 'profile'), PS3_TITLE=title, TB_EXECUTION_MODE='strict', SDL_VIDEODRIVER=args.driver,
               SDL_AUDIODRIVER='dummy', PS3RECOMP_VK_WINDOW='1', PS3RECOMP_VK_GUEST_PROGRAMS='1',
               VK_DRAW_BATCH=args.batch, PS3_HDD0_ROOT=str(hdd), PS3_HDD1_ROOT=str(output / 'profile/dev_hdd1'))
    if args.actions or args.no_gamepad:
        # An explicit keyboard test profile: avoid a connected controller suppressing fallback.
        env['SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT'] = '0x0000/0x0000'
    if args.profile or args.cadence_only:
        env['TB_RENDER_PROFILE'] = str(output / 'timings.jsonl')
        if args.cadence_only:
            env['TB_RENDER_PROFILE_MODE'] = 'cadence'
    if args.input_trace and not args.resources:
        parser.error('--input-trace requires --resources')
    if args.input_trace:
        env['TB_INPUT_TRACE']='1'
    if args.resources:
        from resource_reports import write_media_watch
        directory=output/'resources'
        directory.mkdir()
        write_media_watch(directory, config)
        env['TB_RESOURCE_TRACE']=str(directory)
    if args.textures:
        textures = output / 'textures'
        textures.mkdir()
        (textures / 'shaders').mkdir()
        env.update(TB_RSX_TEXTURE_CAPTURE=str(textures), RSX_ENGINE_SHADER_DUMP=str(textures / 'shaders'))
    if args.wait_trace:
        env.update(PS3_FTRACE='10:5:' + str(output / 'wait-trace.txt'), PS3_FTRACE_CAP='200000', PS3_FTRACE_MIN_US='50')
    if args.passes:
        passes = output / 'passes'
        passes.mkdir()
        env.update(RSX_ENGINE_PASS_DUMP=str(passes), RSX_ENGINE_PASS_DUMP_DRAWS='1',
                   RSX_ENGINE_PASS_DUMP_AFTER='12', RSX_ENGINE_PASS_DUMP_MAX='16')
    frame = output / 'latest.ppm'
    if args.capture:
        env.update(PS3RECOMP_VK_DUMP=str(frame), PS3RECOMP_VK_DUMP_EVERY='15')
    stop, events, captures = threading.Event(), [], []
    window_states = []
    started = time.monotonic()
    def watch():
        index, next_capture, next_window = 0, 4.0, 0.0
        focused = False
        try:
            while not stop.wait(.1):
                elapsed = time.monotonic() - started
                if elapsed >= next_window and shutil.which('hyprctl') and os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
                    next_window = elapsed + 1
                    response = subprocess.run(['hyprctl', '-j', 'clients'], capture_output=True, text=True, timeout=3)
                    if response.returncode == 0:
                        clients = [c for c in json.loads(response.stdout) if c.get('title') == title]
                        if len(clients) == 1:
                            c = clients[0]
                            window_states.append(dict(elapsed_seconds=elapsed, visible=c.get('visible'), hidden=c.get('hidden'),
                                size=c.get('size'), monitor=c.get('monitor'), workspace=c.get('workspace', {}).get('id')))
                            if args.focus_window and not focused:
                                attempt = subprocess.run(['hyprctl', 'dispatch', 'focuswindow', 'address:' + c['address']],
                                                         capture_output=True, text=True, timeout=3)
                                events.append(dict(elapsed_seconds=elapsed, focus_succeeded=attempt.returncode == 0,
                                                   focus_result=attempt.stdout.strip(), focus_error=attempt.stderr.strip()))
                                focused = True
                if index < len(actions) and elapsed >= actions[index]['seconds']:
                    action = actions[index]
                    found = subprocess.run(['xdotool', 'search', '--name', '^' + re.escape(title) + '$'], capture_output=True, text=True, check=True, timeout=3).stdout.splitlines()
                    if len(found) != 1:
                        raise RuntimeError('Expected one uniquely named scenario window')
                    window = found[0]
                    subprocess.run(['xdotool', 'windowfocus', window], check=True, capture_output=True, timeout=3)
                    subprocess.run(['xdotool', 'keydown', '--window', window, action['key']], check=True, capture_output=True, timeout=3)
                    try:
                        stop.wait(action.get('hold_seconds', .15))
                    finally:
                        subprocess.run(['xdotool', 'keyup', '--window', window, action['key']], check=True, capture_output=True, timeout=3)
                    events.append(dict(elapsed_seconds=elapsed, action=action))
                    index += 1
                if args.capture and elapsed >= next_capture and len(captures) < 24:
                    next_capture += 2
                    if frame.exists():
                        target = output / f'frame-{len(captures):02}.ppm'
                        target.write_bytes(frame.read_bytes())
                        try:
                            info = inspect_frame(target, target.with_suffix('.png'))
                            captures.append(dict(elapsed_seconds=elapsed, **info))
                        except ValueError:
                            target.unlink(missing_ok=True)  # Writer was between header and final pixel.
        except Exception as error:
            events.append(dict(error=str(error), elapsed_seconds=time.monotonic() - started))
    worker = threading.Thread(target=watch, daemon=True)
    worker.start()
    try:
        status, session = run_session([str(resolve(args.build_dir) / 'TankBattlesRecomp'), str(content / 'USRDIR/EBOOT.elf')],
            cwd=ROOT, env=env, timeout=args.timeout, log_path=output / 'startup.log', request_snapshot=True)
    finally:
        stop.set()
        worker.join(timeout=5)
    if args.capture and frame.exists():
        final = output / 'final.ppm'
        final.write_bytes(frame.read_bytes())
        try:
            info=inspect_frame(final, output / 'final.png')
            captures.append(dict(elapsed_seconds=time.monotonic()-started, final=True, **info))
        except ValueError:
            final.unlink(missing_ok=True)
    text = (output / 'startup.log').read_text(errors='replace')
    counts = {k: int(v, 16) for k, v in re.findall(r'\[execution-count\] ([a-z-]+)=0x([0-9A-Fa-f]+)', text)}
    failures = ('ppu-miss', 'hle-missing', 'spu-interpreter', 'spu-untranslated', 'ppu-recovery')
    report = dict(session=session, window_states=window_states, focus_requested=args.focus_window, actions=events, captures=captures, driver=args.driver, batch=args.batch, no_gamepad=args.no_gamepad,
        profile_enabled=args.profile, cadence_only=args.cadence_only, capture_enabled=args.capture, pass_capture_enabled=args.passes, texture_capture_enabled=args.textures, content_root=str(content), build_dir=str(resolve(args.build_dir)), wait_trace_enabled=args.wait_trace, audio='dummy', saves='isolated new profile',
        elapsed_origin='launcher monotonic start; actions are elapsed-time based, verify actual reached screens visually',
        strict_counts=counts, strict_passed=all(counts.get(k) == 0 for k in failures),
        timing=summarize_profile(output / 'timings.jsonl', args.warmup),
        source_overlay=json.loads((resolve(args.build_dir) / 'instrumented_sdk/manifest.json').read_text()),
        profile_source_sha256=hashlib.sha256((ROOT / 'rendering_profile.cpp').read_bytes()).hexdigest(),
        main_source_sha256=hashlib.sha256((ROOT / 'main.cpp').read_bytes()).hexdigest(),
        settings={k: v for k, v in env.items() if k.startswith(('TB_RENDER', 'TB_RESOURCE', 'PS3RECOMP_VK_', 'VK_DRAW_', 'SDL_', 'GCM_', 'PAD_', 'PS3_EDAT', 'PS3_FSLOG'))})
    (output / 'session.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(status=status, strict_passed=report['strict_passed'], captures=len(captures), timing=report['timing']), indent=2))
    timing_failed = (args.profile or args.cadence_only) and (not report['timing'] or 'error' in report['timing'])
    if status != 124 or not report['strict_passed'] or timing_failed or any('error' in event for event in events):
        raise SystemExit('Incomplete scenario; inspect local session.json and startup.log')


if __name__ == '__main__':
    main()
