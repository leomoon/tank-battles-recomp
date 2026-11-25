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

Keep correctness captures and profiled runs separate from clean timing. In the current renderer, defining `TB_GPU_TIMING` forces serial submission, including an empty value or `0`; leave it entirely absent when measuring default async performance. The existing `TB_FRAME_BUDGET_TRIGGER` file can arm the timeline on both platforms. CPU scope collection no longer implicitly enables GPU timing. Windows instruction sampling and OS CPU counters are available below; scheduler ready-time/context-switch evidence has not been collected.

For Linux resource comparisons, add `--host-resources` to the scripted Battle route. Use an isolated, previously warmed reference profile with `--reference-template` and `--appimage` to collect the RPCS3 side. Alternate run order across pairs. Both routes use 60 Hz pacing, hold the main menu, level select, briefing and debriefing for 12 seconds each, and default to a 25-second Battle interval so an early round ending is less likely to invalidate it. The harness retains boundary screenshots, process and per-thread CPU counters at 8 Hz, and independent memory, GPU, temperature and fan samples at about 1 Hz. `host-resource-report.json` summarizes CPU time by scene and `host-sensors.jsonl` retains timestamps for checking delayed fan response. The round-end transition is bounded by the last observed live frame and the first observed debriefing, with scene search and OCR occurring in that bracket. Use the stable-scene intervals for CPU baseline comparisons and the bracket for investigating transitions. RPCS3 supplies its title-reported FPS and the native player counts completed frames.

To attribute Linux CPU time, run the native resource route separately with `--linux-symbols` and summarize it using `scripts/cpu_scene_report.py`. This samples user-space instructions at 99 Hz on the threads present when sampling starts. Compare the sampled time against the resource route's per-thread counters, which include kernel CPU time. If the executable is stripped, supply `--player-symbols` with a symbol-bearing relink from the same objects and link order. Verify identical executable section bytes and addresses before using the relink for symbolization. For the Tank Battles inbound mailbox call site, `--mailbox-profile` enables a separate diagnostic on a current build. Counts include complete 100 ms buckets inside each scene and distinguish a free slot on the first read, a full-slot response and a free slot after retries. Unflushed trailing buckets and buckets crossing scene boundaries are excluded. Preserve the pre-instrumentation Release measurement for attribution because mailbox instrumentation changes the cost of the measured path.

The native gameplay harness supports Windows Release executables and Ninja/source builds using the same cadence reporting and rejection rules:

```text
python scripts/script_gameplay.py --output analysis/performance/windows-battle --scene battle --build-dir build-player --manual-boundaries
```

Windows uses an isolated profile with a junction to shared title content, separate writable saves/licenses, and an exclusive profile lock. It discovers the game's window by process ID and exact title, requires a foreground, unminimized, uncloaked 1280x720 client, and closes only the owned window. Foreground checks do not detect every possible overlapping window. A failed focus request does not establish window readiness. Do not switch applications during a timed interval.

Windows menu recognition uses local Windows OCR. The stylized countdown can fail OCR: `--manual-boundaries` requires explicit review of each retained boundary image. Review requests give the image SHA-256; publish the corresponding review JSON with that hash, `live_gameplay: true`, the actual integer `countdown`, a named `reviewer`, and the observed boolean `overlay_visible`. Write a temporary file and rename it into place. Never supply a guessed countdown or approve a menu/results screen. Reviews occur outside the timed interval; countdown reports use the capture timestamps, including elapsed review time. A visual review is not an OCR result. No RPCS3 reference has been captured on this Windows host.

Use `--quick --profile-seconds 10` only for harness diagnostics; quick runs are never baseline-eligible. Windows reference-emulator, correctness-video and soak adapters are explicitly unsupported. Linux desktop behavior is retained but still needs a fresh physical Linux run. Both paths preserve separate guest flip, completed-frame and window-present streams, actual audio/surface logs, and strict-execution checks. The launch manifest identifies the executable, runtime manifest/compiler and effective commands separately from the checkout source; it does not repair an old bundle's provenance.

Local Windows validation on 2026-11-17 produced a valid 60-second Battle sample at 20.0 completed GAME FPS (50.00 ms mean, 52.93 ms p95, 65.20 ms p99). Short Battle and first-campaign diagnostics passed at 20.2 and 25.5 FPS respectively. These are baseline observations, not optimization gains. Retained evidence is under `analysis/performance/windows-intel-baseline/run-2`, `windows-reviewed-probe`, and `windows-campaign-probe`. Other attempts were rejected for a review-file race, unavailable foreground, or access violations. The review race and premature window-ready publication were repaired. Two fault records identify Intel `igvk64.dll` 26.20.100.8141 at offset `0x50f18a`; this locates the failure but does not establish its cause. Three valid Battle minutes, a full campaign sample, renewed Linux validation, and investigation of the Intel crash remain open before accepting the baseline cohort.

The audio fix in `2584b04` is part of the baseline. Preserve paced guest-ring consumption, bounded whole-block queueing, MMCSS and producer scheduling. User confirmation establishes that audible crackling was fixed on the Intel host; startup/menu silence statistics alone are neither underrun counts nor gameplay/soak acceptance. The outstanding Intel pending-present center-pixel fixture failure remains a separate rendering gate.

## NUC resolution baseline, 2026-11-24

Step 1 freezes an initial snapshot on the Intel NUC6i7KYB, i7-6770HQ, Iris Pro
580, 32 GiB DDR4-2400, Windows 11 IoT Enterprise build 26300 and graphics driver
27.20.100.8681, using Balanced power. Measurements followed compilation;
temperature, CPU/GPU clocks, power and per-process GPU residency are unavailable.

| Native clean scene | Completed GAME FPS, 60 seconds | Mean / p95 / p99 ms | Overlay |
| --- | ---: | --- | --- |
| Default Battle, neutral player one and three CPUs | 29.22 (1,753 frames) | 34.22 / 49.23 / 50.56 | On throughout |
| First Campaign mission | 47.47 (2,848 frames) | 21.07 / 33.35 / 35.17 | Off |

Both runs passed strict execution, owned-window visibility and real host-audio
checks. These are separate scene snapshots, not a matched comparison or the
three-repeat acceptance cohort. Neither establishes the 60 FPS objective.
The freshly rebuilt clean player has SHA-256
`7bd953a8b5bfcf2626df33d88e039afa21ecbc294f7519d853ec9e426273d1aa`;
local evidence is `analysis/performance/nuc-resolution-step1/`.

Separate diagnostics now record logical draws, emitted Native/scaled engine
draws, fallback presentation, actual scene/present-source extents, issued color
readbacks, completed color/depth guest stores and GPU depth conversions.
Materialization records include location/offset, allowing roles to be joined
to complete resolve and active consumer evidence instead of dimensions alone.
`resolution_baseline_report.py <run-directory>` summarizes counters, GPU times
and Windows read-only per-thread CPU/working-set/private-byte samples. CPU
time is distinct from frame-budget wall time outside instrumented blocking.

The Native Campaign diagnostic emitted 221.18 Native engine draws per 221.18
logical draws, zero scaled draws, and observed 2560x1440 scene backing with a
1280x720 present source. GPU command time was 14.85 ms/frame. Three separate
Battle RSX scopes prove all six resolve tiles per display, and a 2048x2048
depth-only producer consumed through the shadow GPU alias. Native captures
show one 8 MiB Z16 alias conversion/frame and zero completed CPU guest stores;
GPU transfers still occur. Heavy captures never supply accepted FPS.

The 150% diagnostic exposes the existing duplicated work: 220.43 logical draws
emit 220.43 Native plus 157.44 scaled engine draws/frame. Observed scaled
scene/present extents are 3840x2160 and 1920x1080, with a Native fallback also
recorded. This and a separate Battle diagnostic faulted after window-close
requests in Intel `igvk64.dll`; their counter observations are retained, but
their timing reports remain rejected. The existing clean/diagnostic GPU
fixture assertion also remains an open rendering gate.

Windows measurement clocks now use `perf_counter`, matching the runtime's
QueryPerformanceCounter. Python 3.11 `monotonic` uses GetTickCount64 with a
different epoch. End captures follow the recorded producer deadline, and
overlay-setting changes reject a timing interval. Reviewed countdown values
are bound to retained image hashes and identify the actual reviewer.

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

`--cpu-samples` (on `script_gameplay.py`, and `frame_budget.py run`) starts `scripts/sample_windows_cpu.py` 5 seconds after the gameplay marker. It samples the `ppu main`, `tb-rsx-walker` and `tb-present` threads by suspend-and-sample at about 1 kHz for the interval. Use a map verified against the measured executable; a runtime relink can invalidate an older map. An instruction address outside the executable does not distinguish DLL execution from blocking. The render threads carry Windows thread names. Sampling perturbs timing, so keep it out of clean runs.

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

### Title build: shared semantics, ISA level and PGO

`cmake/TitleCompileFlags.cmake` is shared by the full-source build and the Windows CI-runtime consumer. It compiles the lifted title code with:

- `-fwrapv -fno-strict-aliasing -ffp-contract=off`;
- the SSE4.1 baseline, spelled with `/clang:` under clang-cl.

The Windows consumer previously dropped all of these, including the `-fwrapv` that Linux requires for correct wrapping arithmetic.

**ISA level.** `TB_TITLE_ARCH=x86-64-v3` builds, but on the Iris the game then stalls deterministically during boot, after the trophy `RegisterContext` and before the "not signed in" dialog. It stays off until that divergence is explained.

**Restrict-context experiment.** `TB_TITLE_RESTRICT_CONTEXT=ON` compiles byte-identical copies of the lifted sources with a `__restrict` context parameter. It measured neutral and stays off.

**PGO.** Profile-guided optimization is opt-in and local, because the profile is derived from the purchased game:

```text
python scripts/port.py configure --build-dir build-pgo-gen ... -- -DTB_TITLE_PGO=generate
cmake --build build-pgo-gen --config Release --target TankBattlesRecomp
set LLVM_PROFILE_FILE=analysis\pgo\battle-%p.profraw
python scripts/script_gameplay.py --output analysis/performance/<new> --scene battle --build-dir build-pgo-gen --pending-review
llvm-profdata merge -o analysis/pgo/battle.profdata analysis/pgo/battle-*.profraw
python scripts/port.py configure --build-dir build-pgo ... -- -DTB_TITLE_PGO=<absolute path to battle.profdata>
```

The instrumented build runs at roughly two-thirds speed and writes its profile every 5 seconds, so it is never a benchmark. Regenerate the profile whenever the translated sources change.

On the Iris, with the same runtime, PGO (`budget-pgo-1`):

| Metric | Before | After |
| --- | --- | --- |
| Main guest thread (ms/frame) | 21.6 | 19.3 |
| Completed FPS | 29.5 | 29.7 |

The GPU becomes the limiter (19.9 ms/frame). The optimized rebuild took 296 s.

Consumer builds also write `TankBattlesRecomp.map` (`/lldmap`) for the CPU sampler.

### NV3089 gather reads its source image directly

The 2:1 resolve gather copied the whole sampled source rectangle (about 14.7 MB of a 2560x1440 target) into a buffer before its compute pass. For RGBA8 sources, `shaders/nv3089_gather_image.comp` now uses `texelFetch` on the source image. It reads only the kept texels, with integer coordinates and exact UNORM8 packing. Its per-slot descriptor pools are recycled when the slot's batch has completed.

The once-per-device exactness test checks the buffer and image paths separately and enables each only on a full match. Iris Pro 580: both exact.

On the Iris, with the PGO build (`budget-gather-1`, `budget-gather-3`):

| Metric | Before | After |
| --- | --- | --- |
| GPU busy (ms/frame) | 19.9 | 16.1–16.2 |
| Image-to-buffer copies per frame | 8 (6.4 ms) | 2 (3.2 ms) |

The remaining copies are the depth conversion's.

Teardown fix: deferred image destruction now stops once the draw engine is shut down, and anything still queued is destroyed after the device is idled and before `vkDestroyDevice`. Destroying the device with live images faulted inside `igvk64.dll` (read of null+0x150) at exit.

`shaders/glsl_spirv_tool.cpp` builds a minimal `glslangValidator -V` stand-in from the glslang libraries the runtime already links, for hosts without the glslang tools. It reproduces both earlier embedded shaders byte for byte, and `scripts/compile_depth_shader.py --glslang <tool>` uses it.

## Linux PPU load CPU result

The 32-bit guest load helper is forced inline so the compiler can keep the function’s memory snapshot in registers. Six resource runs in reversed parent/candidate order reduced median default Battle process CPU from 1.969 to 1.885 logical cores and main PPU CPU from 0.594 to 0.523. Held menus and debriefing showed little change. The size-tool text total grew by 32.32 MB. The Linux memory fixture checks boundaries, alignment, byte order and guarded fallbacks with sanitizers. Local measurements and executable identities are recorded in [the load experiment](../analysis/performance/cpu-load32/REPORT.md).

## Linux raw SPU idle wait

The identified Tank Battles raw worker now parks after a short spin when both local-store work slots and its mailbox are empty. Mailbox, signal and stop producers wake it through the existing channel condition variable. A one-millisecond timeout covers local-store and DMA writes without notification. Linux enables the wait by default, with `TB_RAW_SPU_IDLE=0` selecting the original polling path. Other hosts require explicit opt-in.

Three comparison pairs reduced median Battle process CPU from 1.871 to 0.929 logical cores, main menu from 1.285 to 0.304 and debriefing from 1.687 to 0.690. A diagnostic identified raw-worker CPU at 0.0213 core in Battle. Fresh full-minute Battle and Campaign passed the existing pacing limits at 59.9667 and 60.00 completed GAME FPS, with host audio and strict execution. Local evidence, thermal readings and test coverage are in [the idle-wait report](../analysis/performance/cpu-spu-idle/REPORT.md).

## Bounded vertex-ID remapping

Draws with one base index and a vertex-ID span inside the existing remap table use direct lookup. Sparse ranges and mixed bases use the hash path. Generations and first-use order are preserved, and current vertex bytes are fetched each draw. Linux comparison pairs reduced median Battle walker CPU from 0.2198 to 0.2005 core and process CPU from 0.950 to 0.898. Menu/debriefing process results remain inconclusive. Full-minute Battle and Campaign passed at 59.9833 and 60.00 completed GAME FPS. [Measurements and temporal verification](../analysis/performance/cpu-vertices/REPORT.md)

Use `--host-resources --vertex-profile` on the native scripted route for preparation costs and held-scene draw/pipeline/upload snapshots. Adding `--correctness-video` records the full resource route at 60 FPS and enables bounded scanout-generation tracing. These modes record diagnostic flags in the resource report. Use matching capture options when comparing their costs.

## Packed VMX mixer arithmetic

On x86-64 with GCC or Clang, `vmx_vmaddfp` selects a target-specific packed FMA function when the CPU supports FMA/SSSE3 and the host uses round-to-nearest with DAZ/FTZ off. The function preserves guest byte order, aliasing, canonical NaNs and both guest VSCR flush modes. Other CPU or host floating-point states use the scalar implementation. The baseline ISA applies to the rest of the player. CTest runs the fused fixture with automatic selection and with `VMX_SCALAR_FMA` to cover the fallback.

Three Linux comparison pairs reduced median Battle channel-strip CPU from 0.0972 to 0.0558 logical core, a 42.6% saving. Whole-process Battle median fell 3.53% with overlapping ranges, while main-menu process CPU fell 12.10% with distinct ranges. Round-end process savings remain inconclusive. Fresh full-minute Battle and Campaign passed at 59.95 and 59.98 completed GAME FPS. The user reported normal audio during comparison sessions. Windows validation remains pending. [Measurements, numerical fixtures and audio evidence](../analysis/performance/cpu-mixer/REPORT.md)

## Combined Linux CPU acceptance

Nine alternating original/combined/RPCS3 runs supplied three accepted intervals per build and scene. Median Battle process CPU was 1.963, 0.873 and 1.378 logical cores respectively. The combined player uses 55.53% less CPU than the frozen original and 36.67% less than RPCS3. Main-menu CPU fell from 1.286 to 0.263 core, a 79.52% reduction. Median Battle CPU temperature readings fell from about 78 to 58 °C and CPU auxiliary fan readings from 1,010 to 826 RPM. Whole-GPU power varied across runs, with total-system power unresolved. RPCS3 frame rates come from its window title.

The final normal Release rebuild matches the executable used for five accepted Battle minutes, first Campaign timing and physical input/save/reload checks. Successful GitHub host-service CI covers 17 Linux and 18 Windows fixtures. Windows FMA dispatch uses a cached CPUID/XCR0 check to avoid the Microsoft runtime’s missing `__cpu_model` symbol. The portable fixture suite also covers generated PPU loads and vertex remapping. The broad bundle attempts are recorded separately from these CPU/host fixture results. [Combined evidence and limitations](../analysis/performance/cpu-combined/REPORT.md)

## NUC reduced-resolution CPU limit

The current Windows Iris Pro 580 player resolves genuine 640x360 and 960x540
internal images with one eligible scene pass and no normal presentation pixel
readback. This has not achieved 60 completed GAME FPS. A 30-second 50% Battle
diagnostic without heavy image captures measured 29.7 FPS, about 22.34 ms/frame
on the main guest thread outside traced blocking, and 9.91 ms/frame of GPU
work. The 60 Hz vblank clock was correct. These figures locate the remaining
deadline problem on the guest CPU path; the main-thread figure includes
untraced waiting/preemption and is not OS CPU accounting.

A separate full title build using AVX2/FMA (`x86-64-v3`) and restrict-qualified
contexts measured 29.83 FPS, about 21.79 ms main-thread time and 10.15 ms GPU
time in a matching short diagnostic. This is not a validated performance win;
the default compiler settings are unchanged. The existing build is frozen,
and both diagnostic runs, function samples and compiler identities remain
local under `analysis/performance/resolution-step5/`. Sustained CPU optimization
and 60 FPS acceptance remain open. [Resolve checks and limitations](RENDER_SCALE.md)

## Step 7 Windows CPU investigation

`--cpu-accounting` records read-only `GetProcessTimes`/`GetThreadTimes`
snapshots for every process thread. It does not suspend threads or enable
renderer tracing. `scripts/windows_cpu_report.py <run>` matches the OS CPU
deltas to completed-frame events within the same QPC snapshot bounds;
`windows-cpu.json` reports execution time, coverage, residual CPU and session
checks. Frame-budget reports include these counters separately from untraced
wall time. CPU-accounting runs remain diagnostics, not clean FPS acceptance.

Six verified 60-second 50% Battle intervals compared the existing raw-SPU
polling and bounded idle-wait paths on one executable, with callback polling,
real host audio, FXAA off and the overlay on. Run order was poll/idle,
idle/poll, poll/idle; the last polling interval ended its round early and was
rejected, then repeated after the third idle interval. All accepted intervals
passed strict execution, visibility, renderer checks and normal exit.
The measured executable was
`353326856e8542e83cd1deafe0480283f7cb498420183de4426cd9d1df43193c`.

| Median of three intervals | Raw-SPU polling | Bounded idle wait |
| --- | ---: | ---: |
| Completed GAME FPS | 29.83 | 29.48 |
| Process CPU, logical cores | 2.212 | 1.315 |
| Raw-SPU CPU, logical cores | 0.994 | 0.042 |
| Main guest CPU, ms/completed frame | 20.65 | 22.75 |

The wait saves about 41% of total process CPU, but main-thread CPU increases
and FPS is slightly lower. It remains opt-in on Windows (`TB_RAW_SPU_IDLE=1`),
with the previously validated Linux default unchanged. These are observations
on this NUC, not a measured power saving or a universal performance claim.
Live clocks, temperatures and scheduler ready time were not collected.

The frame-budget timeline now records request sequence, executed marker,
vblank completion, callback spans, semaphore wait/post and presentation queue
serials. The report matches only unique, ordered flip sequences with matching
buffers and keeps presentation serials separate. In a 30-second 50% Battle
diagnostic, 889 matched chains measured 1.72 ms request-to-execution, 8.91 ms
execution-to-vblank and 0.80 ms vblank-to-callback on average. Presentation jobs
averaged 9.65 ms including their vblank wait, rather than demonstrating an
additional full-vblank delay. The main thread used 21.94 ms of actual OS CPU
per completed frame; GPU work averaged 10.42 ms. These overlapping stages must
not be added together.

An event-driven callback experiment reduced average callback wake latency to
0.013 ms, but a subsequent full-minute run stopped advancing frames and logged
FIFO circling/resync. It was rejected and the experimental implementation was
removed; the established callback polling remains. This identifies an ordering
investigation, not a proven root-cause repair. Benchmarks now reject FIFO
resyncs and detect five seconds without completed-frame progress during the
timed interval, retaining a screenshot and diagnostic snapshot request before
closing the owned test window.

Local evidence is under `analysis/performance/resolution-step7/`, including
rejected runs. The final artifact is rebuilt after restoring Windows defaults.
Its executable SHA-256 is
`0bd9c0e95d0f1ff28d7db5f0b61732b153ba49ef9dbf04c930a7e067c25ad8f2`.
The final 30-second 75% Campaign diagnostic passed strict execution, visibility,
renderer/overlay checks and normal exit at 44.6 completed GAME FPS. An earlier
Campaign attempt failed the visibility gate and remains excluded. Four native
CTest checks passed, and 75 Python tests completed with two platform/tool skips.
At that stage, the main thread still exceeded 16.67 ms even with polling,
and no safe FPS improvement had been established. Further dispatch/translated
code optimization, the matching Native/75%/50% scene matrix and clean sustained
acceptance remain pending. Neither the clock, simulation speed nor acceptance
thresholds were changed.

### Dispatch cold-path experiment

A fresh 30-second Battle profile at 50% used a symbol map whose executable
text bytes matched the measured player. Of main-thread samples, 3.5% landed
in `ps3_indirect_call_impl` and 1.1% in `tb_execution_event`; 33.3% were outside
the executable and remain unattributed. Sampling suspends threads briefly,
so this run is diagnostic evidence rather than clean performance acceptance.

The dispatch miss path's large backtrace buffers reserved 3,192 stack bytes
on every resolved call on Windows. Moving that block verbatim into a
non-inlined helper reduced the reservation to 1,144 bytes. Seven alternating
microbenchmark pairs using the production archives measured median resolved
dispatch cost of 25.72 ns before and 22.90 ns after, about 11% lower. This is
a function-level result, not an 11% game speedup.

One full-minute 50% Battle interval per build, with read-only OS CPU
accounting, measured 29.82 completed GAME FPS for both. Main CPU was
21.13 ms/frame before and 21.20 ms/frame after. Both passed strict execution,
visibility, renderer, overlay and normal-exit checks. This pair demonstrates
no gameplay FPS or main-thread CPU improvement; it is not a repeated
acceptance cohort. The normal player was restored to the baseline runtime,
and the cold-path transformation was removed from the production overlay.
Its source and measurements remain local alongside the rejected experiments.

`runtime_ppu_dispatch` tests the production archive's resolved calls, cache
replacement/invalidation, deliberate cache collisions, explicit targets
without CTR modification, nested calls, trampolines, and null/invalid/depth
strict stops. Diagnostic-mode invalid/depth behavior is also retained. Local
profiles, assembly and results are under
`analysis/performance/resolution-step7-dispatch/`.

The next experiment used profile-guided title optimization. A fresh
instrumented build completed separate 30-second Battle and Campaign training
intervals with verified live boundaries, strict execution and normal exits.
Their startup/menu navigation is included in the training counters. The two
final cumulative snapshots contain 22,455 functions; source/header hashes,
the exact training executable and runtime manifest are recorded locally.
The periodic LLVM writer appends cumulative snapshots, so only the final
complete snapshot of each process is merged, after validation with
`llvm-profdata`. Whole-process entry counts are matched to the sampled
function leaders in `training-function-counts.json`; they are not per-frame
or phase-matched call rates. Independent, uninstrumented comparisons are
recorded below. Keep profiles local; normal builder flags remain unchanged.
The compiler check confirmed that `/w` suppresses even an explicit
`-Werror=profile-instr-out-of-date`. PGO use builds therefore silence ordinary
generated-code warnings with `-Wno-everything` and then enable that specific
error. A changed-function fixture now fails as required. Training builds are
labelled `diagnostics.pgo_training` and cannot qualify as clean baselines.

### PGO validation on the NUC

The uninstrumented PGO candidate uses the same verified runtime bundle,
Clang 22.1.3, SSE4.1 baseline, wrapping arithmetic, aliasing rules and separate
floating-point rounding as the control. No simulation, vblank, rendering or
strict-execution behavior was changed. Its executable SHA-256 is
`c2487d8a8689b9510fc42585e8aac1394c46c96b6cf3111d27670451a1dcdb63`.
The executable code section shrank from 153,095,318 to 115,374,310 bytes
(24.6%). This candidate and its purchased-game profiles stay local; PGO is
not enabled in the ordinary builder defaults.

Three valid 60-second Battle intervals per build used 50% resolution,
overlay on, FXAA off, real host audio and read-only OS CPU accounting. Run
order was PGO/control, control/PGO, PGO/control. The last control reached
debriefing before its ending capture and was rejected, then replaced with a
fresh control. No compilation ran during the gameplay intervals. All eight
accepted Battle/Campaign intervals passed strict execution, live/visible
boundaries, renderer/overlay checks and normal exit. These are CPU-accounting
diagnostics, not clean sustained-FPS acceptance.

| Battle, median of three intervals | Control | PGO |
| --- | ---: | ---: |
| Main-thread CPU, ms/completed frame | 21.30 | 18.40 |
| Completed GAME FPS | 29.72 | 30.05 |
| Process CPU, logical cores | 2.229 | 2.118 |
| Completed frame p99, ms | 49.87 | 35.07 |

PGO reduced measured main-thread CPU cost by 13.6%; all three PGO CPU results
(18.26–18.63 ms/frame) were below all three controls (21.07–21.75). The small
FPS increase leaves Battle in the 30 FPS range, and main CPU still exceeds
the 16.67 ms budget. This is useful CPU headroom, not a 60 FPS fix.

A separate full-minute first-mission Campaign pair at 50% measured 50.28
completed GAME FPS and 12.66 ms main CPU/frame for the control, versus 54.00
FPS and 10.55 ms for PGO. Both were independent of training. This is one
diagnostic pair, not a repeated Campaign cohort; OCR fallback required
image-bound countdown reviews and the warm-up lengths differed. Clocks,
temperatures and scheduler ready time were not collected in that pair. The
follow-up below covers the Windows scene/scale matrix and available clock
telemetry. Repeated cross-platform acceptance remains open.

Local evidence is in `pgo-comparison.json`, `pgo-campaign-comparison.json`,
`pgo-artifacts.json` and `pgo-profile-identity.json` under
`analysis/performance/resolution-step7-dispatch/`. The rejected
`pgo-control-3` is retained and excluded. Production dispatch/boot CTests,
Windows measurement/build-identity checks, strict-stop probes and the
changed-function profile-rejection fixture passed. Purchased-game profiles
remain local and ordinary builder defaults remain unchanged.

### Dispatch follow-up and remaining critical path

A fresh actual CMake link emitted the map for a separate 30-second 50% PGO
Battle profile. Its executable SHA-256 is
`cf8682043ea1c25d23a185ec35a81042047e950a8c4ec6fa99fd1a28ad287e64`,
and its map SHA-256 is
`aebe327b329aa46932a7fdac9e34a3a3a87a5ba3c16e415e3bd5ad3e412d4f14`.
Both outputs are preserved together under `symbol-build/Release/` in the
local evidence directory. Do not use another link's map: earlier relinks
produced different layouts, and later diagnostics replaced an older map.
The tested `c2487d8a` player was restored before profiling began and remains
the main launcher's executable.

Of 18,322 main-thread instruction samples, 3.51% landed in
`ps3_indirect_call_impl`, 2.07% in `func_00192EB8`, 1.77% in
`func_002A74B8`, 1.66% in `func_002B89B0`, 1.59% in `func_0021B138`,
1.47% in `func_00795984`, 1.44% in `func_002EFC30`, and 0.98% in
`tb_execution_event`. Outside-module samples were 38.30%. Walker leaders were
FIFO dispatch (4.70%), vertex remapping (3.90%) and vertex fetch (1.96%);
69.30% were outside the executable. These are exclusive instruction-pointer
samples, not inclusive stack costs or proof that outside-module time was
spent runnable. Training function-entry counts include startup and menus
and cannot supply phase-matched rates for every translated function.
The diagnostic passed strict/live/visible/renderer/normal-exit checks at
29.83 FPS and is excluded from clean baselines. The exact identities and
ranked counts are in `symbol-map-identity.json` and `final-function-ranks.json`.

A separately marked cache-counter build measured a 97.96% main-thread hit
rate: 36,976,999 hits out of 37,748,736 lookups. Counters publish in blocks of
1,048,576 lookups; the reported blocks end inside the gameplay interval, so
the first block can straddle its beginning. This does not justify enlarging
the existing 512-entry per-thread cache. Logs containing `[ppu-icache]` are
explicitly excluded from clean baselines. No counter instrumentation remains
in the production runtime.

A compact resolved-dispatch path reduced a microbenchmark to about 17 ns,
but its valid gameplay pilot measured 30.07 FPS and 18.50 ms main CPU/frame.
An O3 PGO pilot measured 30.10 FPS and 18.31 ms. Both lie within the retained
O2 PGO CPU range; neither establishes a gameplay improvement. Both changes
were removed. Dispatch tests additionally require exactly seven indirect
events for seven fixture calls, including CALLTRACE and RETWATCH modes.

The earlier callback-event stall remains an unresolved synchronization
experiment. Inspection found that the custom-context walker recycler can
rewrite guest current/PUT and host GET while the guest callback also owns
that bookkeeping. The flip code documents a related advance/flush race.
This is a concrete ownership hazard to investigate, not a demonstrated
interleaving or a verified repair. The event-delivery change stays rejected;
established polling and strict FIFO-resync/stall rejection remain enabled.

The 50% Battle trace matched 891 flip chains. Mean request-to-execution was
3.10 ms, execution-to-vblank 10.02 ms, and vblank-to-callback 0.72 ms.
Callback work averaged 0.013 ms and presentation queue delay 0.029 ms.
Presentation jobs averaged 10.63 ms including their vblank wait. These
overlapping stages do not demonstrate another full-vblank presentation delay
and must not be added together. The retained main-thread CPU cost above
16.67 ms remains the measured obstacle to 60 FPS in this Battle scene.

### Windows scene/scale validation

The final matrix uses the retained `c2487d8a` executable for every run, with
the same runtime, 1280x720 window, 60 Hz clock, real host audio, overlay on
and FXAA off. Each scene/scale has a 30-second frame-budget/OS CPU diagnostic
and a separate clean 60-second interval after five seconds of gameplay
warm-up. Diagnostics also collect live OS frequency estimates, performance
limit flags and raw thread context-switch counters. They are not clean
baselines. Their difference from the separate clean interval includes both
collector overhead and scene variation; it cannot isolate profiler overhead.
No compiler or heavy background test runs during these intervals.

| Scene | Scale | Resolved output | Clean completed FPS | Traced main CPU, ms/frame | GPU, ms/frame |
| --- | ---: | --- | ---: | ---: | ---: |
| Battle | Native | 1280x720 | 29.77 | 18.94 | 16.22 |
| Battle | 75% | 960x540 | 29.85 | 20.21 | 12.34 |
| Battle | 50% | 640x360 | 30.08 | 19.86 | 9.84 |
| Campaign | Native | 1280x720 | 51.68 | 12.60 | 12.40 |
| Campaign | 75% | 960x540 | 52.05 | 11.70 | 10.29 |
| Campaign | 50% | 640x360 | 53.42 | 11.51 | 9.55 |

All twelve accepted runs passed strict execution, live/visible boundaries,
overlay consistency, renderer checks and normal shutdown. Each traced run
verified its requested resolved extent with zero scale fallback/recovery;
clean intervals deliberately have no in-interval tracing. Main CPU, walker
CPU and GPU costs overlap and must not be added. These are one diagnostic and
one clean interval per scene/scale, not a repeated cross-scale acceptance
cohort. Diagnostic FPS differed from its clean counterpart by +0.22% to
-1.27% in Battle and -6.21% to -8.42% in Campaign. That includes collector
cost and scene variation. Clean p95 was 32.71-34.32 ms and p99 34.01-49.96 ms;
none meets the sustained 60 FPS acceptance gate. The lower-resolution GPU
saving is real, while the Battle CPU deadline remains missed.

The six diagnostic intervals sampled median OS frequencies of 3.13-3.24 GHz
(sample range 3.045-3.267 GHz), with sampled performance-limit flags zero.
Evidence, rejected attempts and the complete mean/p95/p99/dependency records
are local under `analysis/performance/resolution-step7-final/`, particularly
`matrix-results.json` and `investigation-results.json`.

`script_gameplay.py --manual-boundaries --visual-boundaries` supports explicit
Windows capture review without delaying the measured scene while OCR runs.
The start remains unverified until both retained images have matching SHA-256
reviews asserting live gameplay, countdown and expected overlay state. The
route then writes a receipt binding both review files; the observer requires
that receipt before accepting the scene. Missing, stale, expired or non-live
reviews cannot promote a run. Both captures are outside the timed interval.
The existing OCR/manual-fallback route remains available. Early debriefs,
visibility failures and expired reviews are retained and excluded.
Interval arming also tolerates a concurrent manifest rewrite or Windows
writer lock; an observer exit before arming still fails the route.

The Windows Performance Recorder CPU profile could not start: 0xc5585011,
"Failed to enable the policy to profile system performance." The thermal WMI
query returned access denied. Recorded frequency values are OS estimates;
zero sampled performance-limit flags do not prove that no throttling occurred.
Context-switch counts and sampled thread states do not measure scheduler
ready-time duration. Temperature, package power and precise ready time remain
unavailable. There is no local Linux runner; Linux validation is still open.

The tested local O2 PGO player is installed behind the main display-settings
launcher at `out/Tank Battles Recomp/Play Tank Battles.exe`. Assembly verified
the executable hash and every owned file, preserved all five existing
save/settings/profile files, and retained the previous executable locally.
The launcher passed its seven-screen UI smoke check, including display
settings, and read-only account-status check. Forty Windows gameplay/timing
tests, eight execution-policy tests, and the production dispatch/CPU-boot
CTests passed after the retained-code changes. Ordinary builder defaults
remain unchanged: this validates the local Windows artifact, not Linux PGO
or a downloaded builder. Step 7's cross-platform/default-rollout gate and
the separate sustained 60 FPS goal remain open.
