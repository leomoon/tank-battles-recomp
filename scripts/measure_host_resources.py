"""Low-rate Linux CPU, memory, NVIDIA GPU and thermal sampling for owned test runs."""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET


def number(value):
    found = re.search(r"-?\d+(?:\.\d+)?", value or "")
    return float(found[0]) if found else None


class HostSampler:
    def __init__(self):
        self.previous = {}
        self.hz = os.sysconf("SC_CLK_TCK")
        self.sensors = {}
        for directory in Path("/sys/class/hwmon").glob("hwmon*"):
            name = (directory / "name").read_text().strip()
            for path in [*directory.glob("temp*_input"), *directory.glob("fan*_input")]:
                label = path.with_name(path.name.replace("_input", "_label"))
                key = name + ":" + (label.read_text().strip() if label.exists() else path.stem)
                if name in ("k10temp", "asusec"):
                    self.sensors[key] = path

    def sample(self, pid=None, *, fast=False, slow_only=False):
        now = time.monotonic_ns()
        row = dict(monotonic_ns=now, wall_time_ns=time.time_ns(), pid=pid)
        directory = Path("/proc") / str(pid) if pid is not None else None
        if not slow_only:
            row["threads"] = []
            cpu = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
            total, idle = sum(cpu), cpu[3] + cpu[4]
            old = self.previous.get("system")
            if old and total > old[0]:
                row["system_cpu_percent"] = 100 * (1 - (idle - old[1]) / (total - old[0]))
            self.previous["system"] = (total, idle)
            if directory:
                try:
                    self._cpu(row, pid, (directory / "stat").read_text(), now, "process_cpu_percent")
                    for task in (directory / "task").iterdir():
                        try:
                            stat = (task / "stat").read_text()
                            thread = dict(tid=int(task.name), name=stat.split("(", 1)[1].rsplit(")", 1)[0])
                            self._cpu(thread, int(task.name), stat, now, "cpu_percent")
                            row["threads"].append(thread)
                        except (ProcessLookupError, FileNotFoundError):
                            pass
                except (ProcessLookupError, FileNotFoundError):
                    row["process_exited"] = True
        if fast:
            return row
        if directory:
            try:
                status = (directory / "status").read_text()
                resident = re.search(r"VmRSS:\s+(\d+)", status)
                if resident is None:
                    raise ProcessLookupError("Process has exited")
                row["rss_mib"] = int(resident[1]) / 1024
                row["thread_count"] = int(re.search(r"Threads:\s+(\d+)", status)[1])
                rollup = (directory / "smaps_rollup").read_text()
                pss = re.search(r"(?m)^Pss:\s+(\d+)", rollup)
                row["pss_mib"] = int(pss[1]) / 1024 if pss else None
            except PermissionError:
                row["pss_mib"] = None
            except (ProcessLookupError, FileNotFoundError):
                row["process_exited"] = True
        row["sensors"] = {}
        for key, path in self.sensors.items():
            try:
                value = int(path.read_text())
                row["sensors"][key] = value / 1000 if path.name.startswith("temp") else value
            except (OSError, ValueError):
                pass
        try:
            result = subprocess.run(["nvidia-smi", "-q", "-x"], capture_output=True, text=True, timeout=4)
            gpu = ET.fromstring(result.stdout).find("gpu") if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, ET.ParseError) as error:
            row["gpu_error"] = str(error)
            return row
        if gpu is None:
            row["gpu_error"] = result.stderr.strip() or "NVIDIA GPU telemetry unavailable"
        else:
            row["gpu"] = dict(name=gpu.findtext("product_name"),
                utilization_percent=number(gpu.findtext("utilization/gpu_util")),
                power_w=number(gpu.findtext("gpu_power_readings/average_power_draw") or gpu.findtext("power_readings/power_draw")),
                temperature_c=number(gpu.findtext("temperature/gpu_temp")),
                fan_percent=number(gpu.findtext("fan_speed")),
                total_memory_mib=number(gpu.findtext("fb_memory_usage/used")),
                process_memory_mib=sum(number(p.findtext("used_memory")) or 0
                    for p in gpu.findall("processes/process_info") if p.findtext("pid") == str(pid)))
        return row

    def _cpu(self, row, pid, stat, now, label):
        fields = stat.rsplit(")", 1)[1].split()
        ticks = int(fields[11]) + int(fields[12])
        if fields[0] in ("Z", "X"):
            raise ProcessLookupError("Process has exited")
        row.update(cpu_seconds=ticks / self.hz, start_ticks=int(fields[19]))
        key = (label, pid, row["start_ticks"])
        old = self.previous.get(key)
        if old and now > old[1]:
            row[label] = 100 * (ticks - old[0]) / self.hz / ((now - old[1]) / 1e9)
        self.previous[key] = ticks, now


def save_sample(stream, sampler, pid=None, **labels):
    row = sampler.sample(pid)
    row.update(labels)
    stream.write(json.dumps(row) + "\n")
    stream.flush()
    return row


class ResourceRecorder:
    """Read CPU counters at 8 Hz, independently of potentially slow sensors."""

    def __init__(self, directory, pid):
        self.stop = threading.Event()
        self.errors = []
        self.workers = [
            threading.Thread(target=self.record, args=(directory / name, pid, interval, fast))
            for name, interval, fast in (
                ("host-cpu.jsonl", 0.125, True), ("host-sensors.jsonl", 1.0, False)
            )
        ]
        for worker in self.workers:
            worker.start()

    def record(self, path, pid, interval, fast):
        from diagnostic_logs import open_log

        try:
            sampler = HostSampler()
            with open_log(path, "w") as stream:
                while not self.stop.is_set():
                    started = time.monotonic()
                    row = sampler.sample(pid, fast=fast, slow_only=not fast)
                    stream.write(json.dumps(row) + "\n")
                    stream.flush()
                    if row.get("process_exited"):
                        break
                    self.stop.wait(max(0, interval - (time.monotonic() - started)))
        except Exception as error:
            self.errors.append(f"{path.name}: {error}")

    def close(self):
        self.stop.set()
        for worker in self.workers:
            worker.join()
        if self.errors:
            raise RuntimeError("; ".join(self.errors))
