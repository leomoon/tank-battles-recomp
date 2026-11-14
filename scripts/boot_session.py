"""Bounded child sessions with a combined log and explicit exit metadata."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from diagnostic_logs import open_log, normalize_log_permissions

def run_session(command, *, cwd, env, timeout=None, log_path=None, request_snapshot=False, on_start=None):
    if log_path:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = open_log(log_path) if log_path else None
    started = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    timed_out = interrupted = False
    process = None

    def stop():
        if request_snapshot and sys.platform.startswith("linux") and process.poll() is None:
            try:
                os.kill(process.pid, signal.SIGUSR1)
                time.sleep(0.2)
            except ProcessLookupError:
                pass
        if os.name == "posix":
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
        elif process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            else:
                process.kill()
            process.wait()
        finally:
            # A child can exit while one of its descendants ignores SIGTERM.
            if os.name == "posix":
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass

    try:
        process = subprocess.Popen(command, cwd=cwd, env=env,
            stdout=stream, stderr=subprocess.STDOUT if stream else None,
            start_new_session=(os.name == "posix"))
        try:
            if on_start:
                on_start(process)
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop()
        except KeyboardInterrupt:
            interrupted = True
            stop()
        except BaseException:
            stop()
            raise
    finally:
        if stream:
            stream.close()
            normalize_log_permissions(log_path.parent)

    result = {
        "started_utc": started, "command": list(map(str, command)),
        "timeout_seconds": timeout, "timed_out": timed_out, "interrupted": interrupted,
        "child_returncode": process.returncode,
        "termination_signal": -process.returncode if process.returncode < 0 else None,
        "duration_seconds": round(time.monotonic() - start, 3),
        "log": str(log_path) if log_path else None,
    }
    status = 124 if timed_out else 130 if interrupted else (
        128 - process.returncode if process.returncode < 0 else process.returncode)
    result["launcher_exit_status"] = status
    if log_path:
        Path(str(log_path) + ".result.json").write_text(json.dumps(result, indent=2) + "\n")
    if timed_out:
        print(f"[session] TIMEOUT after {timeout}s; child returncode={process.returncode}", file=sys.stderr)
    else:
        print(f"[session] child returncode={process.returncode}", file=sys.stderr)
    return status, result
