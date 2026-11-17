# Performance optimization workflow

Preserve the working private player before changing the renderer or translation build:

```text
python scripts/freeze_performance_baseline.py --build-dir build-player --output analysis/optimization/<host>/baseline
python scripts/freeze_performance_baseline.py --output analysis/optimization/<host>/baseline --verify
```

The output must be a new directory under ignored `analysis/`. It contains the executable, a hash-verified copy of the selected runtime bundle, compile-command/build records, source snapshot, generated-source and object hashes, settings, and a private backup of the original home/save tree. These files can contain game-derived code and entitlement material; keep the whole snapshot local. The freeze does not rebuild, launch or alter the original player or saves.

`baseline.json` records the checkout commit separately from the runtime bundle's source label and the actual executable/archive hashes. A relink using old guest objects is not a clean rebuild from the current commit. Generated-object identities and compile commands remain part of the comparison evidence.

`scripts/host_inventory.py --output analysis/optimization/<host>/inventory` collects read-only hardware and full Vulkan information without requiring NVIDIA tools. Unavailable probes are recorded explicitly. Windows uses selected CIM fields and the active power scheme; no account, network or hardware serial-number inventory is collected. Thermal conditions require separate per-run observation. A Vulkan API version is not a display-driver version.

Use a separate result row for each hardware/OS/driver configuration. The starting observations are Linux Ryzen 9 7950X/RTX 3060 near 60 GAME FPS, Windows Iris Pro 580 around 20, and Windows Ryzen 9 7940HS/Radeon 780M around 29-35. Only the earlier Linux results have a recorded acceptance cohort; the Windows numbers are user observations. Linux iGPU runs and current AMD driver measurements remain pending. Do not pool these observations or infer an OS bottleneck from different machines.

The benchmark contract retains default Battle (Field of War I, default mode, neutral player one and three CPU opponents) and first campaign mission, isolated purchased profiles, real audio and 1280x720 output with unchanged internal surfaces/quality. Allow five seconds after verified gameplay entry before a 60-second interval. Confirm both boundaries and countdown progression. Use at least three clean repeats for baseline comparisons; final acceptance requires five valid Battle minutes and campaign, completed GAME FPS within 0.25% of 60, mean at most 16.71 ms, p95 at most 18 ms and p99 at most 25 ms. Initial and sustained thermal conditions must be identified separately.

Keep correctness captures and profiled runs separate from clean timing. In the current renderer, defining `TB_GPU_TIMING` forces serial submission, including an empty value or `0`; leave it entirely absent when measuring default async performance. The existing `TB_FRAME_BUDGET_TRIGGER` file can arm the timeline on both platforms. The current scripted harness still has Linux desktop assumptions; portability work must retain its scene, visibility, audio and strict-execution rejection rules.

The audio fix in `2584b04` is part of the baseline. Preserve paced guest-ring consumption, bounded whole-block queueing, MMCSS and producer scheduling. User confirmation establishes that audible crackling was fixed on the Intel host; startup/menu silence statistics alone are neither underrun counts nor gameplay/soak acceptance. The outstanding Intel pending-present center-pixel fixture failure remains a separate rendering gate.
