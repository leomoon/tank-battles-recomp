"""Capture synchronized intervals from two already-running, operator-verified peers.

This mode never reads RPCN profiles. A specification contains labels, timing paths
and public measurement metadata only; it cannot launch or authenticate accounts.
"""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from gameplay_report import paired_cadence

PEER_FIELDS = ('label', 'role', 'executable', 'source_commit', 'timing', 'audio', 'window_size')
RUN_FIELDS = ('diagnostics_active', 'sdk_verbose', 'input_source', 'clock_domain', 'map', 'render_scale', 'fxaa', 'network_topology', 'peer_path', 'port_translator', 'display_backend')


def capture(argv):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seconds', type=int, default=60)
    p.add_argument('--verified-live-visible-ungrouped', action='store_true', required=True)
    a = p.parse_args(argv)
    if a.seconds < 60:
        p.error('Paired baselines require at least 60 seconds')
    spec = json.loads(a.spec.read_text())
    if spec.get('clock_domain') != 'same-host-monotonic':
        p.error('This capture mode requires same-host-monotonic; collect separate-host intervals locally')
    if spec.get('diagnostics_active') != [] or spec.get('sdk_verbose') is not False:
        p.error('Declare diagnostics_active: [] and sdk_verbose: false for a clean paired interval')
    peers = spec['peers']
    if len(peers) != 2 or sorted(x['role'] for x in peers) != ['host', 'invited']:
        p.error('Specify exactly one host and one invited peer')
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', x['label']) for x in peers):
        p.error('Use short alphanumeric peer labels')
    if len({x['label'] for x in peers}) != 2:
        p.error('Peer labels must be distinct')
    # Copy only allowed metadata: never serialize an arbitrary environment/profile.
    manifest = {key: spec[key] for key in RUN_FIELDS}
    manifest['peers'] = []
    for peer in peers:
        row = {key: peer[key] for key in PEER_FIELDS}
        row['executable_sha256'] = hashlib.sha256(Path(row['executable']).read_bytes()).hexdigest()
        manifest['peers'].append(row)
    a.output.mkdir(parents=True, exist_ok=False)
    begin = time.monotonic_ns()
    manifest.update(start_ns=begin, end_ns=begin + a.seconds * 1_000_000_000,
                    scene_start_verified=True, scene_end_verified=False,
                    visibility_attestation='operator verified at start; check observations.jsonl throughout')
    (a.output / 'measurement.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Interval started; keep both games visible and in gameplay.', flush=True)
    while time.monotonic_ns() < manifest['end_ns']:
        time.sleep(max(0, min(1, (manifest['end_ns'] - time.monotonic_ns()) / 1e9)))
    # Let the telemetry writer publish the final interval boundary.
    time.sleep(2)
    reports = {}
    for peer in peers:
        text = Path(peer['timing']).read_text()
        lines = text.splitlines() if text.endswith('\n') else text.splitlines()[:-1]
        rows = [json.loads(line) for line in lines if line.strip()]
        reports[peer['label']] = paired_cadence(rows, begin, manifest['end_ns'])
        # Retain the rows needed to reproduce this interval after peers exit.
        retained=[r for r in rows if r['monotonic_ns']>=begin-2_000_000_000 and r['monotonic_ns']<=manifest['end_ns']+2_000_000_000]
        (a.output / (peer['label']+'-timing.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in retained))
    result = dict(manifest=manifest, players=reports, baseline_eligible=False,
                  pending='Verify live gameplay at end and uninterrupted visibility before accepting this interval.')
    (a.output / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v['completed_frame'] for k, v in reports.items()}, indent=2))


def review(argv):
    p=argparse.ArgumentParser(description='Accept a paired interval after reviewing its gameplay boundaries.')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--observations',type=Path,required=True)
    p.add_argument('--verified-live-at-end',action='store_true',required=True)
    a=p.parse_args(argv)
    result=json.loads((a.output/'report.json').read_text())
    m=result['manifest'];begin,end=m['start_ns'],m['end_ns']
    observations=[json.loads(l) for l in a.observations.read_text().splitlines() if l.strip()]
    selected=[r for r in observations if begin-2_000_000_000<=r['ns']<=end+2_000_000_000]
    if not selected or selected[0]['ns']>begin or selected[-1]['ns']<end:
        raise ValueError('Incomplete visibility observation coverage')
    if any(b['ns']-a['ns']>3_000_000_000 for a,b in zip(selected,selected[1:])):
        raise ValueError('Visibility observation gap')
    labels={x['label'] for x in m['peers']}
    first_pids={x['title']:x.get('pid') for x in selected[0]['peers']}
    for r in selected:
        peers=r['peers']
        if {x['title']:x.get('pid') for x in peers}!=first_pids:
            raise ValueError('Peer process changed during interval')
        if len(peers)!=2 or {x['title'].rsplit(' ',1)[-1] for x in peers}!=labels:
            raise ValueError('Missing visible peer')
        expected={x['label']:x.get('window_size') for x in m['peers']}
        if any(expected[x['title'].rsplit(' ',1)[-1]] is not None and x.get('size')!=expected[x['title'].rsplit(' ',1)[-1]] for x in peers):
            raise ValueError('Peer window size changed')
        if any(not x['visible'] or x['hidden'] or x['grouped'] for x in peers):
            raise ValueError('Hidden or grouped peer')
    m['scene_end_verified']=True
    result['baseline_eligible']=False
    result['paired_same_host_eligible']=True
    result['scope']='Same-host paired baseline only; see network_topology and port_translator. Separate-machine validation pending.'
    result.pop('pending',None)
    (a.output/'observations.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in selected))
    (a.output/'measurement.json').write_text(json.dumps(m,indent=2)+'\n')
    (a.output/'report.json').write_text(json.dumps(result,indent=2)+'\n')
