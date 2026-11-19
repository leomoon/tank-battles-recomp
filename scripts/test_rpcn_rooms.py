"""Check native Matching2 against a private RPCN instance using two existing accounts."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile
import time


def check(runner, host, peer):
    with tempfile.TemporaryDirectory(prefix="tb-rpcn-rooms-") as directory:
        for index, (first, second, scenario) in enumerate([
                (host, peer, "normal"), (peer, host, "normal"), (host, peer, "owner"),
                (host, peer, "full")]):
            path = Path(directory) / f"host-{index}.log"
            with path.open("w") as log, subprocess.Popen(
                    [str(runner), str(first), "host", scenario], stdout=log,
                    stderr=subprocess.STDOUT) as process:
                try:
                    deadline = time.monotonic() + 8
                    room = None
                    while time.monotonic() < deadline and process.poll() is None:
                        room = re.search(r"created room (\d+)", path.read_text())
                        if room:
                            break
                        time.sleep(.05)
                    if not room:
                        raise RuntimeError(path.read_text())
                    join_mode = "full" + room[1] if scenario == "full" else scenario
                    other = subprocess.run([str(runner), str(second), "join", join_mode],
                                           capture_output=True, text=True, timeout=15)
                    status = process.wait(timeout=10)
                    if status or other.returncode:
                        raise RuntimeError(path.read_text() + other.stdout + other.stderr)
                    print(f"PASS {scenario}, host profile {int(first != host)}", flush=True)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            time.sleep(.15)
        for profile, mode in [(host, "cancel"), (peer, "empty")]:
            result = subprocess.run([str(runner), str(profile), mode, "normal"],
                                    capture_output=True, text=True, timeout=12)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            print(f"PASS {mode}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--host-profile", type=Path, required=True)
    parser.add_argument("--peer-profile", type=Path, required=True)
    args = parser.parse_args()
    check(args.runner.resolve(), args.host_profile.resolve(), args.peer_profile.resolve())
