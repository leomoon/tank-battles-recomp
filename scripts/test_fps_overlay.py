#!/usr/bin/env python3
"""Check distinct guest/present rates, stalled sampling, raster bounds and toggle."""
import os
import subprocess
from port import ROOT
out=ROOT/'build-tests/fps-overlay';out.mkdir(parents=True,exist_ok=True)
subprocess.run(['c++','-std=c++20','-O2','-I'+str(ROOT),str(ROOT/'tests/runtime_bundle/overlay.cpp'),str(ROOT/'fps_overlay.cpp'),str(ROOT/'input_events.cpp'),str(ROOT/'player_settings.cpp'),str(ROOT/'rendering_profile.cpp'),'-pthread','-o',str(out/'test')],check=True)
subprocess.run([str(out/'test')],cwd=out,env=dict(os.environ,TB_FPS_OVERLAY='1',TB_SETTINGS_FILE=''),check=True)
print('PASS: cadence, overlay toggle, picker input, simultaneous toast and expiry')
