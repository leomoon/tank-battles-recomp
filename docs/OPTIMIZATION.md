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

Keep correctness captures and profiled runs separate from clean timing. In the current renderer, defining `TB_GPU_TIMING` forces serial submission, including an empty value or `0`; leave it entirely absent when measuring default async performance. The existing `TB_FRAME_BUDGET_TRIGGER` file can arm the timeline on both platforms. CPU scope collection no longer implicitly enables GPU timing. Windows CPU stack/context-switch sampling remains unavailable.

The native gameplay harness supports Windows Release executables and Ninja/source builds using the same cadence reporting and rejection rules:

```text
python scripts/script_gameplay.py --output analysis/performance/windows-battle --scene battle --build-dir build-player --manual-boundaries
```

Windows uses an isolated profile with a junction to shared title content, separate writable saves/licenses, and an exclusive profile lock. It discovers the game's window by process ID and exact title, requires a foreground, unminimized, uncloaked 1280x720 client, and closes only the owned window. Foreground checks do not detect every possible overlapping window. A failed focus request does not establish window readiness. Do not switch applications during a timed interval.

Windows menu recognition uses local Windows OCR. The stylized countdown can fail OCR: `--manual-boundaries` requires explicit review of each retained boundary image. Review requests give the image SHA-256; publish the corresponding review JSON with that hash, `live_gameplay: true`, the actual integer `countdown`, a named `reviewer`, and the observed boolean `overlay_visible`. Write a temporary file and rename it into place. Never supply a guessed countdown or approve a menu/results screen. Reviews occur outside the timed interval; countdown reports use the capture timestamps, including elapsed review time. A visual review is not an OCR result. No RPCS3 reference has been captured on this Windows host.

Use `--quick --profile-seconds 10` only for harness diagnostics; quick runs are never baseline-eligible. Windows reference-emulator, correctness-video and soak adapters are explicitly unsupported. Linux desktop behavior is retained but still needs a fresh physical Linux run. Both paths preserve separate guest flip, completed-frame and window-present streams, actual audio/surface logs, and strict-execution checks. The launch manifest identifies the executable, runtime manifest/compiler and effective commands separately from the checkout source; it does not repair an old bundle's provenance.

Local Windows validation on 2026-11-17 produced a valid 60-second Battle sample at 20.0 completed GAME FPS (50.00 ms mean, 52.93 ms p95, 65.20 ms p99). Short Battle and first-campaign diagnostics passed at 20.2 and 25.5 FPS respectively. These are baseline observations, not optimization gains. Retained evidence is under `analysis/performance/windows-intel-baseline/run-2`, `windows-reviewed-probe`, and `windows-campaign-probe`. Other attempts were rejected for a review-file race, unavailable foreground, or access violations. The review race and premature window-ready publication were repaired. Two fault records identify Intel `igvk64.dll` 26.20.100.8141 at offset `0x50f18a`; this locates the failure but does not establish its cause. Three valid Battle minutes, a full campaign sample, renewed Linux validation, and investigation of the Intel crash remain open before accepting the baseline cohort.

The audio fix in `2584b04` is part of the baseline. Preserve paced guest-ring consumption, bounded whole-block queueing, MMCSS and producer scheduling. User confirmation establishes that audible crackling was fixed on the Intel host; startup/menu silence statistics alone are neither underrun counts nor gameplay/soak acceptance. The outstanding Intel pending-present center-pixel fixture failure remains a separate rendering gate.
