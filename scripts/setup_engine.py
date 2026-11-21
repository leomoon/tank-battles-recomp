"""One setup engine for `port.py setup` and the builder interface.

Runs the existing import, translation and native build pipeline as named
stages and reports them as events: readable lines on a console, or one JSON
object per line with --json for a front end. Every child process belongs to
this engine, so cancelling (SIGTERM/SIGINT, CTRL_BREAK on Windows, or "cancel"
on stdin with --stdin-control) stops exactly those processes and keeps every
completed stage for the next run.

Events (--json): {"event": "stage", "stage": ..., "state": started|reused|done|failed|cancelled}
  {"event": "progress", "stage": ..., "done": n, "total": m}
  {"event": "log", "line": ...}
  {"event": "result", "ok": bool, "player": path?, "log": path, "error": text?, "action": text?}
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime
import errno
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent))
import port  # noqa: E402
from install_layout import Layout  # noqa: E402

STAGES = (("inputs", "Check inputs, space and builder files"),
          ("import", "Import and verify your game"),
          ("translate", "Translate the game code"),
          ("configure", "Prepare the native build"),
          ("compile", "Compile your player"),
          ("assemble", "Create your player folder"))
# Measured on the reference title: the largest lifted translation unit peaks
# near 0.3 GB in clang/Zig and the lifter near the value below per worker.
# Both carry a safety margin; a fixed reserve stays free for the desktop.
COMPILE_JOB_MB = 700
LIFT_JOB_MB = 1500
RESERVE_MB = 1536
# Workspace footprint: translations (also staged while retranslating), build
# outputs, imported game, compiler cache, with margin.
WORKSPACE_MB = dict(translations=1100, build=800, game=450, cache=400)
NINJA_PROGRESS = re.compile(r"^\[(\d+)/(\d+)\]")
LIFT_PROGRESS = re.compile(r"lifted (\d+)/(\d+) functions")


class Cancelled(BaseException):
    """Like KeyboardInterrupt: stage error handlers must not turn it into a failure."""


class StageError(Exception):
    def __init__(self, message, action=""):
        super().__init__(message)
        self.action = action


# ---------------------------------------------------------------------------
# Reporting

class Reporter:
    def __init__(self, log_path: Path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path = log_path
        self.log_file = log_path.open("a", encoding="utf-8", errors="replace")
        if os.name == "posix":
            os.fchmod(self.log_file.fileno(), 0o644)
        self.stage_name = "inputs"
        self.lock = threading.Lock()

    def write_log(self, text):
        with self.lock:
            self.log_file.write(text + "\n")
            self.log_file.flush()

    def stage(self, stage, state):
        if state == "started":
            self.stage_name = stage
        self.write_log(f"[stage] {stage}: {state}")
        self.emit_stage(stage, state)

    def log(self, line):
        self.write_log(line)
        self.emit_log(line)

    def progress(self, done, total):
        self.emit({"event": "progress", "stage": self.stage_name, "done": done, "total": total})

    def result(self, **result):
        result = dict(event="result", log=str(self.log_path), **result)
        self.write_log("[result] " + json.dumps(result))
        self.emit(result)

    def close(self):
        self.log_file.close()


class ConsoleReporter(Reporter):
    def __init__(self, log_path, stream=None):
        super().__init__(log_path)
        self.stream = stream or sys.stdout

    def emit(self, event):
        if event["event"] == "result":
            if event["ok"]:
                return
            print(f"[setup] {event['error']}", file=self.stream, flush=True)
            if event.get("action"):
                print(f"  {event['action']}", file=self.stream, flush=True)
            print(f"[setup] Log: {event['log']}", file=self.stream, flush=True)

    def emit_stage(self, stage, state):
        if state in ("failed", "cancelled"):
            print(f"[setup] {stage}: {state}", file=self.stream, flush=True)

    def emit_log(self, line):
        print(line, file=self.stream, flush=True)


class JsonReporter(Reporter):
    def __init__(self, log_path, stream=None):
        super().__init__(log_path)
        self.stream = stream or sys.stdout

    def emit(self, event):
        with self.lock:
            self.stream.write(json.dumps(event) + "\n")
            self.stream.flush()

    def emit_stage(self, stage, state):
        self.emit({"event": "stage", "stage": stage, "state": state})

    def emit_log(self, line):
        self.emit({"event": "log", "line": line})


class LineWriter(io.TextIOBase):
    """Routes in-process prints (setup_game, port) into log events."""

    def __init__(self, sink):
        self.sink, self.pending = sink, ""

    def writable(self):
        return True

    def write(self, text):
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            self.sink(line)
        return len(text)

    def flush(self):
        if self.pending:
            self.sink(self.pending)
            self.pending = ""


# ---------------------------------------------------------------------------
# Owned child processes and cancellation

class Children:
    def __init__(self):
        self.processes: set[subprocess.Popen] = set()
        self.lock = threading.Lock()
        self.cancelled = False

    def start(self, command, cwd, env):
        options = dict(cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1)
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True   # Its own group: killpg reaches its compilers too.
        with self.lock:
            if self.cancelled:
                raise Cancelled()
            process = subprocess.Popen(command, **options)
            self.processes.add(process)
        return process

    def finished(self, process):
        with self.lock:
            self.processes.discard(process)

    def terminate_all(self):
        with self.lock:
            self.cancelled = True
            processes = list(self.processes)
        for process in processes:
            if process.poll() is not None:
                continue
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    with contextlib.suppress(OSError):
                        os.killpg(process.pid, signal.SIGKILL)
                process.kill()


def make_executor(children: Children, reporter: Reporter):
    def execute(command, *, cwd, env=None):
        process = children.start(command, cwd, env)
        try:
            for line in process.stdout:
                line = line.rstrip("\r\n")
                reporter.log(line)
                match = NINJA_PROGRESS.match(line) or LIFT_PROGRESS.search(line)
                if match:
                    reporter.progress(int(match[1]), int(match[2]))
            process.wait()
        finally:
            children.finished(process)
        if children.cancelled:
            raise Cancelled()
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
    return execute


def install_cancellation(children: Children, stdin_control: bool):
    def cancel(*_):
        children.terminate_all()
        raise Cancelled()
    signals = [signal.SIGINT, signal.SIGTERM] + ([signal.SIGBREAK] if hasattr(signal, "SIGBREAK") else [])
    for number in signals:
        signal.signal(number, cancel)
    if stdin_control:
        def watch():
            for line in sys.stdin:
                if line.strip() == "cancel":
                    break
            # EOF too: a front end that went away must not leave a build running.
            children.terminate_all()
            signal.raise_signal(signal.SIGTERM)
        threading.Thread(target=watch, daemon=True).start()


# ---------------------------------------------------------------------------
# Resources: memory, disk

def available_memory_mb() -> int | None:
    try:
        if os.name == "nt":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = Status()
            status.dwLength = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.ullAvailPhys >> 20
            return None
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) >> 10
    except (OSError, ValueError, AttributeError):
        return None
    return None


def bounded_jobs(requested: int | None, per_job_mb: int, memory_mb: int | None = None, cpus: int | None = None) -> int:
    cpus = cpus or os.cpu_count() or 1
    jobs = min(requested or cpus, cpus)
    memory_mb = available_memory_mb() if memory_mb is None else memory_mb
    if memory_mb is not None:
        jobs = min(jobs, max(1, (memory_mb - RESERVE_MB) // per_job_mb))
    return max(1, jobs)


def directory_mb(path: Path) -> int:
    total = 0
    if path.is_dir():
        for root, _, files in os.walk(path):
            for name in files:
                with contextlib.suppress(OSError):
                    total += (Path(root) / name).stat().st_size
    return total >> 20


def space_needed_mb(workspace: Path, config) -> int:
    """What the remaining work may still write, after crediting existing outputs."""
    have = dict(translations=directory_mb(port.resolve(config["paths"]["recompiled"])),
                build=directory_mb(port.resolve(config["paths"]["build"])),
                game=directory_mb(port.resolve(config["paths"]["gamedata_root"])),
                cache=directory_mb(workspace / "cache"))
    # Retranslation stages a second copy before replacing the first.
    return sum(max(0, need - have[name]) for name, need in WORKSPACE_MB.items()) + 256


def check_space(workspace: Path, config):
    workspace.mkdir(parents=True, exist_ok=True)
    needed = space_needed_mb(workspace, config)
    free = shutil.disk_usage(workspace).free >> 20
    status = "ok" if free >= needed else "error"
    return dict(name="Workspace space", path=str(workspace), status=status,
                action="" if status == "ok" else f"Free {needed - free} MB more on this drive, or choose a workspace with at least {needed} MB free.",
                detail=f"{free} MB free, about {needed} MB needed")


def check_resources():
    """Damaged builder files must fail before an hour of work, with a clear action."""
    try:
        port.LAYOUT.verify()
        bundle = port.LAYOUT.runtime_bundle()
        if bundle:
            from runtime_bundle import verify
            verify(bundle, source=port.LAYOUT.source_commit())
        return dict(name="Builder files", path=str(port.LAYOUT.resources), status="ok", action="")
    except (OSError, ValueError, KeyError) as error:
        return dict(name="Builder files", path=str(port.LAYOUT.resources), status="error",
                    action=f"Download the builder again; its files do not verify ({error}).")


# ---------------------------------------------------------------------------
# The stages

def run_setup(args, config, reporter: Reporter) -> Path | None:
    from setup_game import check_game, install_game
    from setup_inputs import print_checks, require_valid
    from setup_pipeline import player_executable, run_pipeline, setup_lock

    import player_folder
    workspace = port.LAYOUT.workspace
    reporter.stage("inputs", "started")
    output = (Path(args.output).expanduser().resolve() if getattr(args, "output", None)
              else player_folder.default_output() if port.LAYOUT.packaged and not args.check else None)
    if output:
        player_folder.check_output(output)   # Before any long work.
    checks = check_game(args, config, workspace, port.resolve)   # Prints its own results.
    extra = []
    if not args.import_only:
        extra += port.setup_tools(args)
        extra.append(check_resources())
        extra.append(check_space(workspace, config))
        requested = args.jobs
        args.jobs = bounded_jobs(requested, COMPILE_JOB_MB)
        args.lift_jobs = bounded_jobs(requested or config["recompiler"]["jobs"], LIFT_JOB_MB)
        memory = available_memory_mb()
        extra.append(dict(name="Parallel jobs", path=f"compile {args.jobs}, translate {args.lift_jobs}",
                           status="ok", action="" if memory is None else f"{memory} MB memory available"))
    print_checks(extra)
    require_valid(checks + extra)
    reporter.stage("inputs", "done")
    if args.check:
        print("[setup] Check passed. Purchased inputs were not modified.")
        return None

    with setup_lock(port.resolve(config["paths"]["gamedata_root"])):
        reporter.stage("import", "started")
        args.elf = str(install_game(args, config, workspace, port.resolve))
        reporter.stage("import", "done")
        if args.import_only:
            return None
        args.sdk = None
        args.analyze_only = False
        args.recomp_dir = None
        args.cmake_args = []
        run_pipeline(args, config, port.resolve, port.prepare, port.configure, port.build, notify=reporter.stage)
    build_dir = port.resolve(args.build_dir or config["paths"]["build"])
    player = player_executable(build_dir, args)
    if output:
        reporter.stage("assemble", "started")
        sources = player_folder.build_outputs(build_dir, getattr(args, "multi_config", False))
        sources.update(player_folder.content_files(port.resolve(config["paths"]["vfs_root"]),
                                                   port.resolve(config["paths"]["hdd0_root"]) / "home/00000001/exdata",
                                                   config["game"]["title_id"]))
        try:
            source = port.LAYOUT.source_commit()
        except ValueError:
            source = None
        player_folder.assemble(output, sources, dict(source_commit=source, title_id=config["game"]["title_id"]))
        if getattr(args, "migrate_saves_from", None):
            player_folder.migrate(Path(args.migrate_saves_from).expanduser().resolve(), output)
        reporter.stage("assemble", "done")
        launcher = output / player_folder.launcher_name()
        print(f"[setup] Ready. Play with: {launcher}")
        return launcher
    workspace_option = f" --workspace {shlex_quote(str(workspace))}" if workspace != port.ROOT else ""
    build_option = f" --build-dir {shlex_quote(args.build_dir)}" if args.build_dir else ""
    print(f"[setup] Ready. Launch with: python scripts/port.py{workspace_option} run{build_option}")
    return player


def shlex_quote(value):
    import shlex
    return shlex.quote(value)


def classify(error, reporter: Reporter) -> tuple[str, str]:
    stage = reporter.stage_name
    retry = "Press Build again (or rerun setup) to continue; finished stages are kept."
    text = str(error)
    if isinstance(error, OSError) and error.errno == errno.ENOSPC or "No space left on device" in text:
        return "The drive holding the workspace is full.", f"Free space on that drive, then retry. {retry}"
    if isinstance(error, MemoryError):
        return "The computer ran out of memory.", f"Close other programs or choose fewer jobs, then retry. {retry}"
    if isinstance(error, subprocess.CalledProcessError) or "returned non-zero exit status" in text:
        tail = {"translate": "The game code translation failed.", "configure": "Preparing the native build failed.",
                "compile": "The native compiler failed."}.get(stage, "A setup step failed.")
        return tail, f"Details are in the log. {retry}"
    if stage == "inputs":
        return text, "Correct the inputs above, then retry. Your original files were not modified."
    return text, retry


def run(args, config, reporter: Reporter, stdin_control=False, setup=run_setup) -> int:
    children = Children()
    port.EXECUTE = make_executor(children, reporter)
    writer = LineWriter(reporter.log)
    try:
        install_cancellation(children, stdin_control)
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            try:
                player = setup(args, config, reporter)
            finally:
                writer.flush()
        reporter.result(ok=True, player=str(player) if player else None)
        return 0
    except Cancelled:
        children.terminate_all()
        reporter.stage(reporter.stage_name, "cancelled")
        reporter.result(ok=False, cancelled=True, error="Setup was cancelled.",
                        action="Press Build again to continue; finished stages are kept.")
        return 130
    except Exception as error:   # Every failure becomes a stage error with an action.
        children.terminate_all()
        message, action = classify(error, reporter)
        if error.__cause__:
            reporter.write_log(f"[cause] {error.__cause__!r}")
        reporter.write_log(f"[error] {error!r}")
        reporter.stage(reporter.stage_name, "failed")
        reporter.result(ok=False, stage=reporter.stage_name, error=message, action=action)
        return 1
    finally:
        reporter.close()


def log_path(workspace: Path) -> Path:
    return workspace / "logs" / datetime.now().strftime("setup-%Y%m%d-%H%M%S-%f.log")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--workspace")
    parser.add_argument("--json", action="store_true", help="One JSON event per line on stdout")
    parser.add_argument("--stdin-control", action="store_true", help="Cancel on a 'cancel' line or end of stdin")
    port.add_setup_arguments(parser)
    args = parser.parse_args()
    try:
        port.LAYOUT = Layout(port.ROOT, args.workspace)
        config = port.LAYOUT.load_config(args.config)
    except (OSError, ValueError) as error:
        event = dict(event="result", ok=False, error=str(error), action="Choose a writable workspace and an installed builder.")
        print(json.dumps(event) if args.json else f"error: {error}", flush=True)
        return 2
    path = log_path(port.LAYOUT.workspace)
    reporter = JsonReporter(path) if args.json else ConsoleReporter(path)
    return run(args, config, reporter, stdin_control=args.stdin_control)


if __name__ == "__main__":
    sys.exit(main())
