"""Exercise username validation, the controller keyboard, and SDL input ownership."""
import os
import shlex
import subprocess
from port import ROOT

out = ROOT / "build-tests/friends-input"
out.mkdir(parents=True, exist_ok=True)
flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "--libs", "sdl2"], text=True))
subprocess.run(["c++", "-std=c++20", "-O2", "-I" + str(ROOT), str(ROOT / "tests/friends_input.cpp"),
                str(ROOT / "input_events.cpp"), "-pthread", *flags, "-o", str(out / "fixture")], check=True)
env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
subprocess.run([str(out / "fixture")], env=env, check=True, timeout=20)
print("PASS: username entry, controller keys, SDL text ownership, focus and held-key release")
