# Portable host services

The port uses build-local SDK source copies from `scripts/instrument_sdk.py`. The vendored SDK stays unchanged. HLE entry-point definitions in the copied `ppu_hle.cpp` are renamed to `tb_sdk_ps3_hle_*`; the port supplies the public call and registration entry points. This works without GNU linker wrapping. Overrides dispatch through private NID aliases inside the original SDK adapter, preserving its argument handling, TOC register/stack restoration, callback pumping and in-flight tracking. Missing imports and reached CPU fallback paths still stop strict execution with exit code 86.

Windows input and audio source copies explicitly select SDL2. They share Linux's positional controller mapping, buffered keyboard presses, serialized hotplug handling and bounded four-block audio queue. On Windows, guest ring consumption and producer notifications also follow monotonic 256-frame/48 kHz deadlines, so a host device draining 480 frames at once cannot trigger consecutive guest reads before the producer runs. Startup pads one host block without advancing the guest cursor, and the mixer registers for MMCSS Pro Audio scheduling when available, with highest thread priority as fallback. A missing audio device uses monotonic time. Pad polling and Windows timers use monotonic QPC timing, and normal player startup requests a 1 ms timer period with matching cleanup at exit. Physical-controller and audible host-audio acceptance still require real machines.

The Windows `audio_burst_pacing` fixture runs the production mixer against a synthetic 10 ms device queue. It checks producer notification spacing, the actual audio rate and the four-block queue limit. The unpaced refill produced 226 sub-millisecond intervals out of 477 notifications; paced consumption removes those bursts. `AUDIO_RATE=1` logs consumption rate, silent mixed blocks and bursts; silent blocks are measured before host playback and are not themselves a device-underrun counter.

The Windows guest-thread source copy also maps priorities below 512 to above-normal host priority. This includes the game's SurMixer producers at priorities 400–404; previously only priorities below 256 received the boost, leaving these producers at the same host priority as the main game thread. This affects scheduling priority, not guest clock speed or generated guest instructions.

On the Iris Pro 580 Windows host, the corrected player holds 187.5006 mixed blocks/s against the 187.5 target in a 60-second diagnostic. Excluding the first startup interval, silent mixed blocks average 0.298%, down from 46.777% in the baseline; no sub-millisecond consumption bursts remain. All 17 native Windows CPU test groups pass. The captures are startup/menu diagnostics ending by timeout. The user subsequently confirmed that audio is fixed on this host; long-duration quality and other Windows hosts still require verification. Local evidence is `analysis/windows-audio-verification.json`.

EDAT cache completion records and validation are shared. Windows adapters use UTF-8 paths converted to wide filesystem operations, `LockFileEx` process locks and same-volume `MoveFileExW` publication. Writers flush the completed data and marker before publishing, with the marker last. A killed writer releases its lock; partial temporary files are never cache candidates. Unlock sidecars and their stricter permissions are documented in [Setup](SETUP.md).

## Diagnostic requests

Linux keeps `SIGUSR1` snapshots and `SIGUSR2` timeline arming. Both platforms also accept control files, which the diagnostic worker consumes after a request. These workers are enabled only by their respective environment settings and are joined during normal exit.

| Environment | Purpose |
| --- | --- |
| `TB_DIAGNOSTIC_TRIGGER=/path/to/snapshot.request` | Create that file to request milestone, HLE-boundary and execution/Vulkan counter snapshots. |
| `TB_FRAME_BUDGET=/path/to/frame.trace` | Enable the bounded frame-budget recorder. |
| `TB_FRAME_BUDGET_TRIGGER=/path/to/frame.request` | Create that file to arm the recorder. |
| `TB_FRAME_BUDGET_DELAY=5` | Seconds between an arm request and capture start. |
| `TB_FRAME_BUDGET_SECONDS=20` | Capture duration, clamped by the existing accepted range. |

Windows unhandled exceptions emit the same snapshot before continuing the operating system's exception handling. The verifier does not claim a snapshot is a live guest-PC sample. Frame-budget output retains the SDK timeline format parsed by `scripts/frame_budget.py`. Page census remains Linux-only and opt-in.

## Game-free verification

```bash
python -m pip install -r external/ps3recomp/requirements.txt
cmake -S tests/host_services -B build-tests/host-services -DCMAKE_BUILD_TYPE=Release
cmake --build build-tests/host-services --config Release
ctest --test-dir build-tests/host-services -C Release --output-on-failure
```

Requires SDL2 development files. Windows CI uses clang-cl and vcpkg's `sdl2:x64-windows`; Linux uses its native compiler and SDL2 package. Fixtures compile the production HLE adapter, strict policy, diagnostics, cache resolver, input mapper, audio loop and timeline. Only fixture VM, host callbacks and artificial inputs are supplied. The ABI header is emitted by an empty lifter instance, without a game executable or functions. The suite also runs the standalone entitlement contracts. No game data, purchased license, derived sidecar or key is uploaded to CI.

This step covers host-service contracts. Building and testing the complete Windows Vulkan player, physical input/audio devices and real gameplay remain separate work.

The full game-free runtime split, Windows Vulkan build and downloadable artifact contract are described in [RUNTIME_BUNDLE.md](RUNTIME_BUNDLE.md). Physical Windows GPU acceptance remains separate from hosted CI.
