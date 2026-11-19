"""Align Linux CPU counters and sensor samples with verified Battle scenes."""
import json
from bisect import bisect_left
import re
import statistics
import subprocess
from pathlib import Path


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def overlay_visible(image):
    """Recognize the player's top-left monochrome GAME / PRESENT / FRAME panel."""
    cropped = subprocess.check_output([
        "magick", str(image), "-crop", "460x108+0+0", "+repage",
        "-colorspace", "gray", "-negate", "-resize", "200%", "png:-",
    ])
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "11"],
        input=cropped, capture_output=True, check=True,
    ).stdout.decode().upper()
    return "FPS" in result and any(label in result for label in ("PRESENT", "FRAME", "GAME"))


def interval_usage(samples, begin, end):
    # Use only complete sampling spans inside the scene. The first sample's
    # delta begins before this interval and could belong to another scene.
    active = [
        row for row in samples
        if begin <= row["monotonic_ns"] <= end and "cpu_seconds" in row
    ]
    if len(active) < 2:
        raise ValueError("Incomplete scene CPU coverage")
    first, last = active[0], active[-1]
    elapsed = (last["monotonic_ns"] - first["monotonic_ns"]) / 1e9
    if elapsed <= 0 or elapsed < (end - begin) / 1e9 - 0.75:
        raise ValueError("Incomplete scene CPU coverage")
    if len({(row["pid"], row["start_ticks"]) for row in active}) != 1:
        raise ValueError("Process identity changed during scene")
    cpu_seconds = last["cpu_seconds"] - first["cpu_seconds"]
    stamps = [row["monotonic_ns"] for row in active]
    one_second_cores = []
    for start in active:
        index = bisect_left(stamps, start["monotonic_ns"] + 1_000_000_000)
        if index < len(active):
            finish = active[index]
            duration = (finish["monotonic_ns"] - start["monotonic_ns"]) / 1e9
            one_second_cores.append((finish["cpu_seconds"] - start["cpu_seconds"]) / duration)
    threads = {}
    for prev, current in zip(active, active[1:]):
        earlier = {(t["tid"], t["start_ticks"]): t for t in prev["threads"]}
        for thread in current["threads"]:
            identity = thread["tid"], thread["start_ticks"]
            old = earlier.get(identity)
            if old:
                record = threads.setdefault(identity, dict(tid=thread["tid"], name=thread["name"], cpu_seconds=0))
                record["cpu_seconds"] += thread["cpu_seconds"] - old["cpu_seconds"]
    for thread in threads.values():
        thread["cores"] = thread["cpu_seconds"] / elapsed
    return dict(samples=len(active), cpu_seconds=cpu_seconds, wall_seconds=elapsed,
                cores=cpu_seconds / elapsed, peak_one_second_cores=max(one_second_cores, default=None),
                threads=sorted(threads.values(), key=lambda t: -t["cores"]))


def summarize_run(directory, *, reference):
    manifest = json.loads((directory / "measurement.json").read_text())
    route = json.loads((directory / "route.json").read_text())
    cpu = rows(directory / "host-cpu.jsonl")
    sensors = rows(directory / "host-sensors.jsonl")
    observations = manifest["rows"] if reference else rows(directory / "observations.jsonl")
    timeline = None if reference else rows(directory / "gameplay.jsonl")
    origin = manifest["clock_origin_monotonic_ns"] if reference else timeline[0]["monotonic_ns"] - timeline[0]["elapsed_ns"]
    phases = {}
    for event in route:
        if event["label"] == "resource-start":
            phases[event["scene"]] = [event["monotonic_ns"]]
        elif event["label"] == "resource-end":
            phases[event["scene"]].append(event.get("boundary_capture_ns", event["monotonic_ns"]))
    round_end = next((event for event in route if event["label"] == "round-end-observed"), None)
    if round_end and round_end["last_live_ns"] and round_end["first_debrief_ns"] > round_end["last_live_ns"]:
        # The transition is observed between two captures. Include the bracket
        # so an early round ending never gets silently averaged into Battle.
        phases["round-end-bracket"] = [round_end["last_live_ns"], round_end["first_debrief_ns"]]
    if not (directory / "invalid-scene.json").exists():
        phases["battle"] = [origin + round(x * 1e9) for x in manifest["interval_seconds"]]
    results = {}
    for scene, (begin, end) in phases.items():
        observations_in_scene = [r for r in observations if r["seconds"] is not None
                                 and begin <= origin + r["seconds"] * 1e9 <= end]
        if not observations_in_scene or any(
            not r["visible"] or r["window_size"] != [1280, 720] for r in observations_in_scene
        ):
            raise ValueError(f"{scene}: hidden or differently sized window")
        visibility_times = [begin, *(origin + r["seconds"] * 1e9 for r in observations_in_scene), end]
        if max(b - a for a, b in zip(visibility_times, visibility_times[1:])) > 2.5e9:
            raise ValueError(f"{scene}: incomplete window visibility coverage")
        stats = interval_usage(cpu, begin, end)
        stats.update(start_ns=begin, end_ns=end)
        if scene == "title-to-menu":
            stats["notices_dismissed"] = [
                event["label"] for event in route
                if begin <= event["monotonic_ns"] < end and event["label"].endswith("notice")
            ]
        slow = [r for r in sensors if begin <= r["monotonic_ns"] <= end]
        for group in ("gpu", "sensors", "memory"):
            metrics = {}
            for row in slow:
                values = {key: row.get(key) for key in ("rss_mib", "pss_mib", "thread_count")} if group == "memory" else row.get(group, {})
                for key, value in values.items():
                    if isinstance(value, (int, float)):
                        metrics.setdefault(key, []).append(value)
            stats[group] = {key: statistics.mean(values) for key, values in metrics.items()}
        if reference:
            fps = [r["fps"] for r in observations_in_scene if r["fps"] is not None]
            if fps:
                stats["reported_fps"] = statistics.mean(fps)
                if scene == "battle":
                    stats["cpu_ms_per_frame_estimate"] = 1000 * stats["cores"] / stats["reported_fps"]
        else:
            frames = sum(begin <= origin + stamp < end
                         for row in timeline for stamp in row["events"][1])
            stats["completed_fps"] = frames * 1e9 / (end - begin)
            if scene == "battle" and frames:
                stats["cpu_ms_per_frame"] = 1000 * stats["cores"] / stats["completed_fps"]
        results[scene] = stats
    if reference:
        config = Path(manifest["config"]).read_text()
        log = directory / "cache/rpcs3/RPCS3.log"
        text = log.read_text(errors="replace") if log.exists() else ""
        audio = {
            "configured": re.search(r"(?m)^Audio:\n  Renderer: (.+)$", config)[1],
            "initialized": re.findall(r"cellAudio initializing\. Backend: (.+)", text),
            "cubeb_backend": re.findall(r"Cubeb: Using backend (.+)", text),
        }
    else:
        audio = manifest["actual_audio"]
    screenshot = directory / "gameplay-before.png"
    window_pixels = subprocess.check_output(
        ["magick", "identify", "-format", "%w %h", str(screenshot)], text=True).split()
    return dict(reference=reference, executable_sha256=manifest.get("appimage_sha256" if reference else "executable_sha256"),
                runtime=manifest.get("runtime_build"), sdk_sha256=manifest.get("sdk_manifest_sha256"),
                reference_config_sha256=manifest.get("config_sha256"), audio=audio,
                window_logical_px=[1280, 720], captured_px=list(map(int, window_pixels)), guest_px=[1280, 720],
                overlay_checks=[event for event in route if event["label"] == "overlay-check"],
                round_end=round_end,
                scenes=results)
