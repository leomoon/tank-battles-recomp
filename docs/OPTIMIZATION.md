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

## Per-frame cost breakdown

`python scripts/frame_budget.py run --output analysis/performance/<new> --build-dir build-player --seconds 20` arms the timeline inside live Battle gameplay. The report covers more than walker/guest thread time:

- **Busy time against the 16.67 ms budget.** Reported for the main guest thread, the walker's CPU work (drain minus fence waits, plus presents), the present thread and the GPU. The largest of these is named as the limiter. Fixed 60 Hz vblank flips quantize frame intervals to whole vblanks, so headroom shows how far the limiting resource actually is from 60 FPS. A 20 FPS frame interval says nothing about whether the work takes 34 or 49 ms.
- **GPU execution.** Timestamps around every command buffer and render pass. They are read when the same command buffer is next begun, so no wait is added and asynchronous submission stays enabled, unlike `TB_GPU_TIMING`. Render-pass time is grouped by target extent. Command-buffer time also includes copies, clears, compute gathers and uploads outside render passes. Set `TB_FRAME_GPU_TIMESTAMPS=0` to keep only the CPU counters.
- **Work counters per frame.** Covers submissions, synchronous submit-waits, memory/image/buffer creations, render passes, barriers, descriptor allocations and writes, clears, transfers, draws, pipeline/shader creation, texture hash and upload volume, and guest colour/depth reads. Counters are cumulative and are written at most every 0.5 ms each, so a busy counter cannot fill the trace.
- **Fence-wait labels on Windows as well as Linux.**

These are diagnostics, not clean timing.

### Pending-review diagnostic runs

Windows OCR does not reliably read the stylized Battle countdown on every host. `--pending-review` lets a timing diagnostic continue when the countdown is unreadable. The run keeps the boundary images and records `pending_review`, and its report says `scene_verification: pending human review` and is never baseline-eligible, so `compare_rendering_runs.py` refuses it. Use it only to iterate on implementations. Acceptance and baseline cohorts still need OCR or the reviewed `--manual-boundaries` path.

### First Intel Iris Pro 580 breakdown (driver 27.20.100.8681)

A 20-second pending-review Battle diagnostic of the frozen baseline source relinked with these counters (`analysis/performance/windows-intel-driver8681/budget-3`) ran at 20.15 FPS (median 49.9 ms, i.e. three vblanks).

Busy time per frame:

| Resource | ms per frame | Notes |
| --- | --- | --- |
| Walker CPU work | 22.3 | The limiter: headroom -5.6 ms. Of this, 21.5 ms is FIFO drain, plus 5.5 ms of fence waits. |
| Main guest thread | 20.1 | Blocked 29.2 ms in `sys_semaphore_wait` |
| GPU | 18.2 | 2560x1440 passes 6.6 ms, 2048x2048 passes 0.5 ms; the rest is transfers, clears, gathers and uploads |

Work per frame:

- **Depth readback round trip:** a synchronous `vk_eng_depth_readback` (3.7 ms wait), then about 4.19 M texels of texture upload. The 2048x2048 depth surface is read back and re-uploaded every frame because `TB_DEPTH_GPU` defaults off on Windows.
- **Hashing:** 11.5 MB of sparse texture hashing.
- **Draws:** 361 draws, 361 descriptor-set allocations and 1,443 descriptor writes.
- **Submission:** 11.8 queue submissions and 76 barriers.

Earlier attempts failed because the window lost focus, the overlay was toggled manually, or countdown OCR failed. They are retained under `rejected-*`.

### GPU-resident depth on every OS

`TB_DEPTH_GPU` now defaults on everywhere; `TB_DEPTH_GPU=0` or the harness flag `--cpu-depth` selects the reference CPU conversion. The harness no longer forces the CPU path when no flag is given.

Fixture results on Iris Pro 580 with driver 27.20.100.8681:

- the exact `vulkan_depth16` fixture passes, with the default on;
- `vulkan_nv3089` passes;
- `vulkan_draw_groups` still fails its existing presented-centre assertion.

The present path now copies only the centre pixel unless `PS3RECOMP_VK_DUMP` or `TB_FRAME_HISTORY` asks for whole frames. Measurement manifests record whether the NV3089 GPU-resolve self-test matched.

On the Iris, 20-second pending-review Battle diagnostics:

| Change | Completed FPS | Walker busy (ms/frame) |
| --- | --- | --- |
| Before | 20.15 | 22.3 |
| After (`budget-step6-1`) | 28.2 | 11.2 |

Other per-frame changes:

- uploaded texels dropped from 4.19 M to about 150;
- sparse hashing dropped from 11.5 MB to 3.1 MB;
- no guest colour reads remain;
- one synchronous submit-wait remains, the GPU depth conversion's.

The limiter is now the main guest thread (21.1 ms), with the GPU at 19.7 ms. The overlay was toggled manually during the diagnostic runs, so these are not clean timings.

### Windows CPU sampling and fast runtime relinks

`--cpu-samples` (on `script_gameplay.py`, and `frame_budget.py run`) starts `scripts/sample_windows_cpu.py` 5 seconds after the gameplay marker. It samples the `ppu main`, `tb-rsx-walker` and `tb-present` threads by suspend-and-sample at about 1 kHz for the interval. Samples are symbolized with the `TankBattlesRecomp.map` that consumer builds now write through `/lldmap` (no runtime cost). Samples outside the executable mean kernel/driver time, mostly blocking. The render threads now carry Windows thread names. Sampling perturbs timing, so keep it out of clean runs.

`scripts/relink_player.py --build-dir <consumer build> --bundle <bundle>` replays only the final link against another runtime bundle with the same ABI, reusing every title object. It records the bundle in `runtime-relink.json` so measurement manifests name the runtime that was actually linked.

The first sample of the semantic-flags build on the Iris attributed 2.9% of main-thread samples to `ps3_indirect_call_impl` and 1.8% to `tb_execution_event`. The PPU/SPU indirect-call execution events now count thread-locally and publish to the shared report every 1024 events with relaxed atomics. Strict-stop kinds keep the exact global path.

### Host waits on Windows

- **Walker idle sleep.** The walker's 100/250 µs idle sleeps rounded up to the ~1 ms Windows timer period. They now use a reused high-resolution waitable timer (Windows 10 1803+), and the walker also waits on the guest's FIFO kick event, waking immediately when a guest fence poll finds the queue dry. Measured on the Iris together with the batched depth conversion and deferred destruction: mean put-to-drain latency fell from 4.1 to 2.2 ms.
- **Power throttling.** The process opts out of EcoQoS power throttling and timer-resolution throttling.
- **`usleep` left as is.** The frame-budget profile shows no guest thread polling with `sys_timer_usleep` during Battle gameplay. The sub-millisecond `usleep` busy-wait therefore affects loading only, and it is unchanged because it keeps guest polling latency.

### Fewer completion waits on the walker

- **Batched depth conversion.** The exact Z16 GPU conversion has no CPU reader. It now records into the open asynchronous batch instead of a waited submit, and its descriptor set is rewritten only when its buffers change.
- **Deferred image destruction.** Destroying an image no longer waits for every batch in flight. The handles are queued with the generation of the next submission and destroyed once that generation has completed and no present copy is running. A full queue falls back to the waited path, and release/shutdown completes and retires everything.

Iris pending-review Battle diagnostic of the semantic-flags build with this runtime (`budget-s457-2`):

| Metric | Before | After |
| --- | --- | --- |
| Completed FPS | 28.24 | 29.5 |
| Walker fence-wait time (ms/frame) | 6.96 | 1.33 |
| Synchronous submit-waits per frame | 1 | 0 |

The main guest thread (21.6 ms running) is the limiter.
