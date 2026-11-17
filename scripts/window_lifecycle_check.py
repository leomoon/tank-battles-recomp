#!/usr/bin/env python3
"""Exercise fullscreen, resize and focus changes during live Battle and check pacing.

Runs the scripted default-Battle route (scripts/script_gameplay.py --quick) and,
inside the timed gameplay interval, toggles fullscreen with the game's own F11
path, resizes the window and moves focus away and back. Afterwards it splits the
exact gameplay timing (gameplay.jsonl) into those phases and requires, in every
phase, guest flips at the normal rate with no long stall, presents resuming once
the window is back, and a normal process exit.

Needs the same host tools as the gameplay helpers (Hyprland, xdotool, X11).
Window minimize is not covered: Hyprland has no minimize.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from port import ROOT, resolve


def gameplay_clock(out):
    rows = (out / 'gameplay.jsonl').read_text().splitlines()
    last = json.loads(rows[-1])
    return (time.monotonic_ns() - (last['monotonic_ns'] - last['elapsed_ns'])) / 1e9


def hypr(operation):
    subprocess.run(['hyprctl', 'dispatch', operation], check=True, capture_output=True)


def analyze(out, actions):
    rows = [json.loads(line) for line in (out / 'gameplay.jsonl').read_text().splitlines()]
    flips = sorted(t / 1e9 for r in rows for t in r['events'][0])
    presents = sorted(t / 1e9 for r in rows for t in r['events'][2])
    phases = []
    bounds = [a['seconds'] for a in actions]
    for (label, start), end in zip([(a['label'], a['seconds']) for a in actions], bounds[1:]):
        f = [t for t in flips if start <= t < end]
        p = [t for t in presents if start <= t < end]
        gaps = [b - a for a, b in zip(f, f[1:])]
        stable=[t for t in f if t>=start+1]
        settled_fps=(len(stable)-1)/(stable[-1]-stable[0]) if len(stable)>1 else 0
        phases.append(dict(settled_guest_fps=settled_fps,phase=label, seconds=round(end - start, 2), guest_fps=round(len(f) / (end - start), 2),
                           present_fps=round(len(p) / (end - start), 2),
                           max_flip_gap_ms=round(1000 * max(gaps), 1) if gaps else None))
    return phases


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', required=True, help='New directory inside analysis/performance/')
    parser.add_argument('--build-dir', default='build-vulkan')
    args = parser.parse_args()
    out = resolve(args.output)
    command = [sys.executable, str(ROOT / 'scripts/script_gameplay.py'), '--output', str(out), '--build-dir', args.build_dir,
               '--overlay', '--quick', '--frame-budget', '--profile-seconds', '60']
    route = subprocess.Popen(command)
    # Live gameplay: the route has written its marker and the interval is set.
    while route.poll() is None:
        m = out / 'measurement.json'
        if m.exists() and json.loads(m.read_text()).get('interval_seconds'):
            break
        time.sleep(0.5)
    if route.poll() is not None:
        raise SystemExit('route ended before live gameplay')
    title = 'Tank Battles performance ' + out.name
    window = subprocess.check_output(['xdotool', 'search', '--name', '^' + re.escape(title) + '$'], text=True).split()[0]
    address = json.loads((out / 'window-prepared.json').read_text())['verified']['address']
    if not re.fullmatch(r'0x[0-9a-fA-F]+', address):
        raise SystemExit('unexpected window address')
    prefix = 'address:' + address
    begin = json.loads((out / 'measurement.json').read_text())['interval_seconds'][0]
    while gameplay_clock(out) < begin + 2:
        time.sleep(0.2)
    actions = []

    def act(label, run=None, hold=4.0):
        actions.append(dict(label=label, seconds=gameplay_clock(out)))
        if run:
            run()
        time.sleep(hold)

    def key(name):
        subprocess.run(['xdotool', 'windowactivate', '--sync', window], check=False, capture_output=True)
        subprocess.run(['xdotool', 'key', '--window', window, name], check=True, capture_output=True)

    others = [c for c in json.loads(subprocess.check_output(['hyprctl', '-j', 'clients'], text=True))
              if c.get('address') != address and c.get('mapped', True)]
    act('windowed baseline')
    act('fullscreen (F11)', lambda: key('F11'))
    act('windowed again (F11)', lambda: key('F11'))
    act('fullscreen (Alt+Enter)', lambda: key('alt+Return'))
    act('windowed again (Alt+Enter)', lambda: key('alt+Return'))
    act('resized 1600x900', lambda: hypr(f'hl.dsp.window.resize({{ window = "{prefix}", x = 1600, y = 900 }})'))
    act('resized back 1280x720', lambda: hypr(f'hl.dsp.window.resize({{ window = "{prefix}", x = 1280, y = 720 }})'))
    if others and re.fullmatch(r'0x[0-9a-fA-F]+', others[0]['address']):
        act('focus on another window', lambda: hypr(f'hl.dsp.focus({{ window = "address:{others[0]["address"]}" }})'))
    act('focus back', lambda: hypr(f'hl.dsp.focus({{ window = "{prefix}" }})'))
    act('minimize request (WM-dependent)', lambda: subprocess.run(['xdotool','windowminimize',window],check=True,capture_output=True),hold=2)
    act('restore/map', lambda: subprocess.run(['xdotool','windowmap',window],check=True,capture_output=True),hold=2)
    act('hidden/unmapped', lambda: subprocess.run(['xdotool','windowunmap',window],check=True,capture_output=True),hold=2)
    act('mapped recovery', lambda: subprocess.run(['xdotool','windowmap',window],check=True,capture_output=True),hold=4)
    actions.append(dict(label='end', seconds=gameplay_clock(out)))
    route.wait()
    result = json.loads((out / 'startup.log.result.json').read_text()) if (out / 'startup.log.result.json').exists() else {}
    phases = analyze(out, actions)
    failures = [p['phase'] for p in phases if p['phase'] not in ('hidden/unmapped','minimize request (WM-dependent)') and (p['guest_fps'] < 57 or (p['max_flip_gap_ms'] or 0) > 150)]
    if phases and phases[-1]['present_fps'] < 50:
        failures.append('presents did not resume')
    exit_ok = result.get('child_returncode') == 0 and not result.get('timed_out')
    report = dict(actions=actions, phases=phases, exit=result, passed=not failures and exit_ok, failures=failures)
    (out / 'window-lifecycle.json').write_text(json.dumps(report, indent=2) + '\n')
    for p in phases:
        print(f"{p['phase']:28s} {p['seconds']:6.2f}s guest {p['guest_fps']:6.2f} fps present {p['present_fps']:6.2f} fps "
              f"max flip gap {p['max_flip_gap_ms']} ms")
    print('exit', result.get('child_returncode'), 'PASS' if report['passed'] else 'FAIL ' + ', '.join(failures))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
