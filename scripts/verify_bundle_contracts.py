"""Rerun production contracts from downloaded executables, never rebuild them."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--bundle',required=True)
args=parser.parse_args();binary=Path(args.bundle).resolve()/'bin'
ext='.exe' if os.name=='nt' else ''
env=dict(os.environ,SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',SDL_JOYSTICK_HIDAPI='0',SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT='0x0000/0x0000')
def run(*command,extra=None):
    subprocess.run([str(value) for value in command],env=dict(env,**(extra or {})),check=True,timeout=120)
run(binary/('runtime_shader_check'+ext))
run(binary/('peer_udp_fixture'+ext))
run(binary/('sysnet_fixture'+ext))
run(binary/('matching_rooms'+ext),'--wire')
for name in ('hle','positions','hotplug','keyboard','audio'):
    run(binary/(name+'_fixture'+ext))
run(binary/('audio_fixture'+ext),'no-device',extra={'SDL_AUDIODRIVER':'nonexistent-test-driver'})
if os.name=='nt':
    run(binary/('audio_burst_fixture'+ext),'burst')
run(sys.executable,root/'tests/host_services/verify.py',binary/('hle_fixture'+ext))
run(sys.executable,root/'tests/host_services/verify_timeline.py',binary/('timeline_fixture'+ext))
for suite in ('test_edat_cache.py','test_entitlement.py','test_prepare_entitlement.py','test_frame_budget.py'):
    run(sys.executable,'-m','unittest','discover','-s',root/'scripts','-p',suite,extra={
        'TB_CACHE_TEST_RUNNER':str(binary/('cache_fixture'+ext)),
        'TB_ENTITLEMENT_TEST_RUNNER':str(binary/('entitlement_fixture'+ext))})
print('PASS: downloaded production contracts, no runtime rebuild')
