"""Setup engine: bounded jobs, actionable errors, owned-process cancellation,
and (with a runtime bundle and staged toolchain) the packaged pipeline on
synthetic input through JSON events: fresh build, no-op repeat, injected
failure, cancel and resume.

    TB_TEST_RUNTIME_BUNDLE=... TB_TEST_TOOLCHAIN=... python -m unittest test_setup_engine
"""
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import setup_engine

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = os.environ.get("TB_TEST_RUNTIME_BUNDLE")
TOOLCHAIN = os.environ.get("TB_TEST_TOOLCHAIN")

# Runs the engine with a game-free setup function: the real pipeline stages on a
# synthetic executable, or a long-running owned child for cancellation.
DRIVER = r'''
import argparse, sys, subprocess
resources, workspace, mode, elf = sys.argv[1:5]
sys.path.insert(0, resources + "/scripts")
import port, setup_engine
from install_layout import Layout
port.LAYOUT = Layout(port.ROOT, workspace)
config = port.LAYOUT.load_config("config.toml")
args = argparse.Namespace(elf=elf, restart=False, build_dir=None, jobs=None, build_type="Release", backend="auto",
                          cmake="cmake", ninja="ninja", cmake_args=[], runtime_bundle=None, runtime_source=None,
                          toolchain=None, sdk=None, recomp_dir=None, analyze_only=False, import_only=False, check=False)

def synthetic(args, config, reporter):
    from setup_inputs import print_checks, require_valid
    from setup_pipeline import player_executable, run_pipeline
    reporter.stage("inputs", "started")
    checks = port.setup_tools(args) + [setup_engine.check_resources(), setup_engine.check_space(port.LAYOUT.workspace, config)]
    args.jobs = setup_engine.bounded_jobs(None, setup_engine.COMPILE_JOB_MB)
    print_checks(checks); require_valid(checks)
    reporter.stage("inputs", "done")
    run_pipeline(args, config, port.resolve, port.prepare, port.configure, port.build, notify=reporter.stage)
    return player_executable(port.resolve(config["paths"]["build"]), args)

def sleeper(args, config, reporter):
    reporter.stage("compile", "started")
    # A child that starts its own grandchild, like a compiler driver.
    port.run([sys.executable, "-c", "import subprocess,sys,time;"
              "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)']);"
              "print('grandchild',p.pid,flush=True);print('[1/4] working',flush=True);time.sleep(300)"])

reporter = setup_engine.JsonReporter(setup_engine.log_path(port.LAYOUT.workspace))
sys.exit(setup_engine.run(args, config, reporter, stdin_control=True,
                          setup=synthetic if mode == "synthetic" else sleeper))
'''


class Engine:
    """A running engine with its JSON events."""

    def __init__(self, python, resources, workspace, mode, elf, env=None, network=True):
        command = [str(python), "-E", "-s", "-c", DRIVER, str(resources), str(workspace), mode, str(elf)]
        unshare = shutil.which("unshare")
        if not network and unshare and subprocess.run([unshare, "-Un", "--map-current-user", "true"],
                                                      capture_output=True).returncode == 0:
            command = [unshare, "-Un", "--map-current-user", *command]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, env=env)
        self.events = []

    def next_event(self):
        line = self.process.stdout.readline()
        if not line:
            return None
        event = json.loads(line)
        self.events.append(event)
        return event

    def until(self, predicate):
        while (event := self.next_event()) is not None:
            if predicate(event):
                return event
        return None

    def finish(self, timeout=1800):
        while self.next_event() is not None:
            pass
        self.process.wait(timeout=timeout)
        return self.process.returncode

    def cancel(self):
        self.process.stdin.write("cancel\n")
        self.process.stdin.flush()

    def states(self, stage):
        return [e["state"] for e in self.events if e["event"] == "stage" and e["stage"] == stage]

    def result(self):
        return next(e for e in reversed(self.events) if e["event"] == "result")


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:   # A zombie awaiting its parent is gone for our purposes.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


class BoundsAndErrorsTests(unittest.TestCase):
    def test_jobs_are_bounded_by_memory_and_processors(self):
        self.assertEqual(setup_engine.bounded_jobs(None, 700, memory_mb=64000, cpus=8), 8)
        self.assertEqual(setup_engine.bounded_jobs(32, 700, memory_mb=64000, cpus=8), 8)
        self.assertEqual(setup_engine.bounded_jobs(None, 700, memory_mb=1536 + 2100, cpus=16), 3)
        self.assertEqual(setup_engine.bounded_jobs(None, 1500, memory_mb=1000, cpus=16), 1)
        self.assertEqual(setup_engine.bounded_jobs(2, 700, memory_mb=None, cpus=16), 2)

    def test_errors_carry_an_action(self):
        reporter = mock.Mock(stage_name="compile")
        message, action = setup_engine.classify(OSError(errno.ENOSPC, "No space left on device"), reporter)
        self.assertIn("full", message)
        self.assertIn("finished stages are kept", action)
        message, action = setup_engine.classify(subprocess.CalledProcessError(1, ["c++"]), reporter)
        self.assertEqual(message, "The native compiler failed.")
        self.assertIn("log", action)
        reporter.stage_name = "inputs"
        message, action = setup_engine.classify(ValueError("Missing package"), reporter)
        self.assertEqual(message, "Missing package")
        self.assertIn("not modified", action)

    def test_insufficient_space_is_reported_before_work(self):
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(setup_engine.shutil, "disk_usage", return_value=mock.Mock(free=100 << 20)), \
                mock.patch.object(setup_engine.port, "resolve", side_effect=lambda value: Path(temporary) / value):
            config = {"paths": {"recompiled": "recompiled", "build": "build", "gamedata_root": "game"}}
            check = setup_engine.check_space(Path(temporary), config)
        self.assertEqual(check["status"], "error")
        self.assertIn("MB more", check["action"])

    def test_output_lines_become_log_events(self):
        lines = []
        writer = setup_engine.LineWriter(lines.append)
        writer.write("one\ntw")
        writer.write("o\n")
        writer.write("tail")
        writer.flush()
        self.assertEqual(lines, ["one", "two", "tail"])


@unittest.skipIf(os.name == "nt", "POSIX process checks")
class CancellationTests(unittest.TestCase):
    def test_cancel_stops_owned_processes_only(self):
        with tempfile.TemporaryDirectory(prefix="tb engine ") as temporary:
            bystander = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(120)"])
            try:
                engine = Engine(sys.executable, ROOT, Path(temporary) / "workspace", "sleeper", "unused")
                started = engine.until(lambda e: e["event"] == "log" and e["line"].startswith("grandchild"))
                if started is None:
                    self.fail(engine.process.stderr.read())
                grandchild = int(started["line"].split()[1])
                progress = engine.until(lambda e: e["event"] == "progress")
                self.assertEqual((progress["done"], progress["total"]), (1, 4))
                engine.cancel()
                self.assertEqual(engine.finish(timeout=60), 130)
                self.assertEqual(engine.states("compile"), ["started", "cancelled"])
                self.assertTrue(engine.result()["cancelled"])
                deadline = time.time() + 10
                while alive(grandchild) and time.time() < deadline:
                    time.sleep(0.1)
                self.assertFalse(alive(grandchild), "the engine left its compiler running")
                self.assertIsNone(bystander.poll(), "the engine stopped a process it does not own")
                log = Path(engine.result()["log"]).read_text()
                self.assertIn("[stage] compile: cancelled", log)
            finally:
                bystander.kill()
                bystander.wait()

    def test_a_closed_front_end_cancels_the_build(self):
        with tempfile.TemporaryDirectory(prefix="tb engine ") as temporary:
            engine = Engine(sys.executable, ROOT, Path(temporary) / "workspace", "sleeper", "unused")
            engine.until(lambda e: e["event"] == "progress")
            engine.process.stdin.close()
            self.assertEqual(engine.finish(timeout=60), 130)


@unittest.skipUnless(BUNDLE and TOOLCHAIN, "set TB_TEST_RUNTIME_BUNDLE and TB_TEST_TOOLCHAIN")
class PackagedPipelineTests(unittest.TestCase):
    def test_fresh_repeat_failure_cancel_and_resume(self):
        with tempfile.TemporaryDirectory(prefix="tb engine route ") as temporary:
            temporary = Path(temporary)
            resources = temporary / "Builder ü" / "resources"
            source = json.loads((Path(BUNDLE) / "manifest.json").read_text())["source_commit"]
            subprocess.run([sys.executable, str(ROOT / "scripts/package_resources.py"), "assemble", "--output", str(resources),
                            "--runtime-bundle", BUNDLE, "--toolchain", TOOLCHAIN, "--source", source, "--link-large"],
                           check=True, capture_output=True)
            fixture = temporary / "fixture"
            subprocess.run([sys.executable, str(ROOT / "scripts/prepare_runtime_fixture.py"), "--sdk",
                            str(ROOT / "external/ps3recomp"), "--output", str(fixture)], check=True, capture_output=True)
            python = Path(TOOLCHAIN) / "python/bin/python3"
            workspace = temporary / "wörk space"
            env = {"HOME": str(temporary), "PATH": "/nonexistent", "LANG": "C.UTF-8"}

            def engine(elf=fixture / "smoke.elf"):
                return Engine(python, resources, workspace, "synthetic", elf, env=env, network=False)

            broken = temporary / "broken.elf"
            broken.write_bytes((fixture / "smoke.elf").read_bytes()[:64])
            failed = engine(broken)
            self.assertEqual(failed.finish(), 1)
            self.assertEqual(failed.result()["stage"], "translate", json.dumps(failed.events[-12:]))
            self.assertIn("translation failed", failed.result()["error"])
            self.assertIn("finished stages are kept", failed.result()["action"])

            fresh = engine()
            # Cancel while the native build is being configured; the translation stays.
            configuring = fresh.until(lambda e: e["event"] == "stage" and e["stage"] == "configure")
            if configuring is None:
                self.fail(fresh.process.stderr.read())
            fresh.cancel()
            self.assertEqual(fresh.finish(), 130)
            self.assertEqual(fresh.states("translate"), ["started", "done"])

            resumed = engine()
            self.assertEqual(resumed.finish(), 0, json.dumps(resumed.events[-5:]))
            self.assertEqual(resumed.states("translate"), ["reused"])
            self.assertEqual(resumed.states("compile"), ["started", "done"])
            self.assertTrue(any(e["event"] == "progress" and e["stage"] == "compile" for e in resumed.events))
            self.assertTrue(Path(resumed.result()["player"]).is_file())

            repeat = engine()
            self.assertEqual(repeat.finish(), 0)
            self.assertEqual(repeat.states("translate"), ["reused"])
            self.assertEqual(repeat.states("configure"), ["reused"])
            log = Path(repeat.result()["log"]).read_text()
            self.assertIn("no work to do", log)


if __name__ == "__main__":
    unittest.main()
