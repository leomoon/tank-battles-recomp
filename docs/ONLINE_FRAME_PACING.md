# Online frame pacing audit

The invited player's lower completed-frame rate is associated with the title's original synchronization loop. The loop adds a positive, frame-indexed network-clock correction to a monotonic pacing deadline. It also changes pacing phases when the peer advances. This audit establishes the arithmetic and observed mechanism, but does not establish a recompilation defect or a safe way to remove required synchronization. No gameplay correction is implemented by this work.

The repeatable baselines and diagnostic method are described in [ONLINE_FPS_BASELINES.md](ONLINE_FPS_BASELINES.md) and [ONLINE_PACING_DIAGNOSTICS.md](ONLINE_PACING_DIAGNOSTICS.md). Local evidence is recorded in `analysis/online-fps/step1/RESULT.md`, `step2/RESULT.md` and `step3/RESULT.md`. Generated chunk numbers can change; guest function and instruction addresses below identify this title's NPUB30154 executable.

## From outgoing frame header to correction

`func_0015B0BC` constructs three unsigned 32-bit header words. The network manager is obtained through `func_001694C8`.

| Body offset | Wire offset after the transport prefix | Value |
| --- | --- | --- |
| `+0x00` | `+0x14` | Network-manager frame index, manager `+0x20` |
| `+0x04` | `+0x18` | RTC at header construction minus manager epoch `+0x18`, truncated to 32 bits |
| `+0x08` | `+0x1c` | Network time sampled by the main update, manager `+0x10` |

The fields are stored and decoded with big-endian guest word access. There is no extra host-endian conversion for this triple. `func_001692E4` samples the network clock once per enabled main update. `func_00169238` advances the frame index while the network manager is running and its blocking flag permits advancement. Header construction follows the title's send schedule, not an independent fixed-rate ping timer. In the controlled sweeps, outgoing headers were close to the sender's completed-frame cadence; frame snapshots were typically tens of microseconds old at construction.

The receive loop in `func_001D164C` dispatches to `func_001D511C`. Its type-6 branch at `0x001D5464` decodes the triple and passes it to `func_0015ABE0`. That decoder supplies the remote frame index and the third word to `func_0016DD64`, which looks up the corresponding frame bookkeeping entry. At `0x0016E148`, it subtracts that entry's local timestamp at node `+0x10` from the remote snapshot. The subtraction wraps to 32 bits and is compared as a signed 32-bit value. Only positive differences can raise the queue's unsigned pending peak at `+0x20`.

```text
delta = signed32(remote_frame_snapshot - stored_local_frame_timestamp)
if delta > 0:
    pending_peak = max_u32(pending_peak, delta)
```

These are relative network-clock snapshots associated with frame bookkeeping. The local entry may be refreshed during queue maintenance in `func_0016EEBC`; it is not necessarily an immutable original send timestamp. Calling the result a packet RTT, one-way latency or RPCN ping would misdescribe the data flow. Frame-number comparisons in `func_0016DD64` can also set pacing `+0x12d` through its TOC alias and seed a six-update queue counter. Timing and phase control therefore need to be considered together.

`func_0016EEBC` runs only when its queue is active and network-manager `+0x08` permits the path. Each invocation takes the unsigned maximum of pending peak `+0x20` and retained peak `+0x24`, stores it in `+0x24`, and clears the pending peak. `func_001B078C` reads transport `+0x08`, a flag set after the title's clock-calibration exchange. The original code clears the retained peak without writing the pacing field if that flag is zero, the peak is at most 512 microseconds, or pacing `+0x122` is nonzero. Otherwise it logically shifts the peak right by one, retains the half and writes that half to pacing `+0x124` at `0x0016F304`.

```text
peak = max_u32(retained_peak, pending_peak)
pending_peak = 0
if not clock_ready or peak <= 512 or pacing_skip_phase:
    retained_peak = 0
    # No write to pacing +0x124 here.
else:
    retained_peak = peak >> 1
    pacing_correction = retained_peak
```

Halving happens per queue update, including updates which retain a previous peak without receiving a larger new one. A clear of queue `+0x24` does not itself clear pacing `+0x124`. The branch just before `0x0016F304` can emit the title's synchronization diagnostic message; both branch paths reach the same pacing-field store. It is not a separate host-only correction implementation. The captured corrections followed the invited role when the host was swapped, rather than a particular account or process.

## Pacing, consumption and phase changes

The pacing object is `0x00B6D5E0` in this executable, with the correction at `0x00B6D704`. The producer's TOC slot `-0x2534` points directly to the field. The object getter and pacer use slot `-0x1b84`. Slots `-0x4abc` and `-0x4870` also point to the field; their observed readers pass the value to the title's formatted diagnostic output. Treat offsets in other objects as unrelated even when their numeric displacement is also `+0x124`.

`func_001A33CC` keeps a previous monotonic timestamp at `+0x110`. On its ordinary wait path, the original floating-point arithmetic at `0x001A3C40` is:

```text
deadline_us = f32(f32(interval_seconds * 1_000_000 + f32(previous_us))
                  + f32(unsigned_correction_us))
while f32(now_us) < deadline_us:
    sys_timer_usleep(1)
    # The title rereads the clock, interval and correction on each poll.
```

The inner multiply/add has the title's single-rounded fused operation, followed by a separate single-rounded correction addition. `scripts/online_pacing_model.py` reproduces the captured positive, finite arithmetic offline. The correction lengthens the requested interval; it is not subtracted from it. The nominal interval comes from a separate configuration object's `+0x04`, usually the float representation of `1/60` second. Networking also adjusts that interval, including in `func_0016D038`, so analysis must use captured interval bits rather than assuming a constant 16,667 microseconds. Configuration `+0x00` is a measured interval average and is not the requested interval.

The wait is skipped when pacing `+0x121` is zero or `+0x122` selects the alternate phase. After the wait or bypass, the function updates previous timestamp `+0x110`, publishes elapsed seconds at `+0x128`, and takes one of three normal return paths. All three clear correction `+0x124`: the path around `0x001A3668`, `0x001A3B94`, and `0x001A3BDC`. The last selects `+0x122 = 1`, `+0x12c = 0`, `+0x12d = 0`; the ordinary paths restore the other phase. The receive-side frame comparison can request a phase change through `+0x12d` independently of the correction's magnitude.

The field is therefore consumed/reset at each pacing return. A model which carries the same pacing-field value indefinitely across frames is incorrect. Repeated positive writes can still extend successive waits, and the retained network peak can decay over successive updates. The diagnostics confirmed zero immediately after every recorded reset.

Pacing returns are not completed game frames. In the B-host sweep, invited A had 2,678 pacing resets during 45 seconds, but only 2,265 wait-loop exit decisions. There were 413 returns selecting the skip phase. The independently recorded completed-frame rate was 43–59 FPS across the short phases. Host B had 2,674 returns and 2,674 wait-loop exits. A change must preserve the title's update/phase behavior; counting pacing calls or 60 Hz vblank notifications as 60 game FPS would conceal this distinction.

## Lifecycle and clock domains

| Path | Relevant state |
| --- | --- |
| Process initialization | The pacing object begins in zeroed guest BSS. The pacer lazily initializes its interval history through `+0x118`; the correction's ordinary clear is per pacing return. |
| Network start | `func_00169438` starts a previously stopped manager, resets its control counters and records a fresh RTC epoch at `+0x18`. Queue construction/reinitialization in `func_00170A60` zeros pending and retained peaks. |
| Clock calibration | The exchange handled around `0x001D5544` accumulates title timestamp differences, adjusts manager epoch `+0x18`, and eventually sets transport `+0x08` around `0x001D55e4`. These timestamps are separate from RPCN's signaling latency. |
| Timestamp reset callback | `func_001A33BC` calls `func_001A3368(1, 65535)`, which writes a new monotonic `+0x110`. That callback does not itself clear `+0x124`. |
| Map/session lifecycle | Start wrapper `func_000E04B4` reaches manager start; teardown path around `0x000E04A0` reaches `func_001696DC`. Network teardown disables the running flag and destroys the manager. These functions are not dedicated pacing-field resets; the global pacing object's ordinary return paths still own that clear. |
| Transport teardown | `func_001D3A2C` clears transport clock-ready `+0x08` and control state. The producer's readiness guard then suppresses correction writes. |

This is a source audit of the lifecycle paths, supplemented by fresh match starts in both host assignments. It is not live acceptance of every map-change, disconnect and reconnect ordering. Those cases remain necessary before enabling any future change. In particular, a timestamp reset is not evidence that all queue state was reset, and a network stop is not evidence of a dedicated `+0x124` store.

The game pacing clock comes from `func_007A0674`, which calls `sys_time_get_system_time` and subtracts a game clock origin. Linux uses monotonic host microseconds; Windows uses elapsed performance-counter time. The network clock instead comes from `func_001B07E4` and `cellRtcGetCurrentTick`, followed by subtraction of the network epoch. Linux's RTC implementation uses wall-clock microseconds; Windows' current RTC implementation has millisecond granularity. The epoch calibration is intended to relate the peers' network clocks. Neither peer's RTC-relative values should be subtracted directly from host-monotonic diagnostic nanoseconds.

Linux's one-microsecond timer call uses `nanosleep`, so the scheduler can wake it later. Windows uses its performance-counter/yield path for submillisecond sleeps. The audit found no simple conversion of one microsecond into an unconditional frame-length sleep. Positive deadline extensions are present before host scheduling lateness is considered. The separate flip-semaphore/vblank wait can also delay a frame; its wall time overlaps the frame's schedule and must not simply be added to every pacing correction as independent CPU cost. Neither platform's clock precision alone establishes the shared FPS cause.

## Signaling latency audit

`rpcn/peer_udp.cpp` records the first authenticated acknowledgment as `now - peer.begin`. That includes time before the first successful hello/ack exchange, possible retries and pumping delays. It is cached rather than refreshed as a steady-state RTT. `rpcn/signaling.cpp` exposes it as connection-info option 1. The title's import `func_0093CA44` reads it into a transport connection's `+0x38`; option 2 fills its bandwidth field.

In `func_001D493C`, the title uses the connection latency when scheduling its clock-negotiation request/deadline. The synchronization correction at `0x0016F304` is instead produced by the frame snapshots described above. No direct copy or conversion from RPCN's latency field to the correction was found. Negotiation scheduling may indirectly affect calibration, so this audit does not prove complete independence. It also does not justify changing the signaling measurement as an FPS fix. A transport-latency improvement would need its own relevant evidence and tests.

## Controlled-delay evidence and next decision

Two local visible, ungrouped instances ran Field of War I with neutral human input, two CPU tanks, infinite lives/time, Native/100%, FXAA off and PulseAudio. A test-only port translator and asynchronous sender queue applied 0, 3, 6, then 0 ms of additional delay in each direction. Each phase settled for two seconds and measured seven seconds. The unchanged title pacing code ran throughout.

| Host | Added delay each direction | Host completed FPS | Invited completed FPS | Invited mean correction at wait-loop exit |
| --- | ---: | ---: | ---: | ---: |
| A | 0 ms | 59.57 | 47.57 | 2.20 ms |
| A | 3 ms | 59.43 | 46.71 | 1.92 ms |
| A | 6 ms | 59.71 | 49.71 | 1.65 ms |
| A | 0 ms again | 59.57 | 47.00 | 2.09 ms |
| B | 0 ms | 59.57 | 46.00 | 3.34 ms |
| B | 3 ms | 59.57 | 58.57 | 0.17 ms |
| B | 6 ms | 59.57 | 59.43 | 0.04 ms |
| B | 0 ms again | 58.71 | 43.43 | 3.30 ms |

All retained subtraction, maximum-selection, halving and deadline observations matched the audited arithmetic. Both invited traces and the B-host trace were lossless. The A-host trace dropped one diagnostic record and supports only retained-event arithmetic, not a complete host timeline; its completed FPS comes from separate gameplay timing. Every recorded reset was zero. The delay helper reported no send failures; delayed deliveries averaged about 0.05 ms beyond the requested delay. It copies bytes unchanged, but acknowledges queue acceptance before the actual OS send and can reorder packets at phase transitions. It is confined to ignored analysis files and is not part of the game or builder.

The reversible B-host result supports a causal connection between network/frame phase, positive correction and the lower completed-frame cadence. It contradicts a simple model in which larger peer delay always adds a fixed ping penalty to each frame. The unequal response between host assignments also shows why a fixed cap, blanket zero, or permanent artificial packet delay is not an established solution. Short phases can include scene and scheduling changes; this sweep is a mechanism experiment, not a performance acceptance result.

The next implementation decision must account for frame-phase requests, calibrated clock offsets, the separate requested interval, and legitimate recovery waits. No candidate is enabled by this audit. A future experiment must retain the original path by default, preserve elapsed simulation time and peer frame progression, handle session resets, and prove complete-round synchronization before its FPS gain is accepted. Equivalent paired RPCS3 timing and original PS3 timing have not been captured; original console behavior remains unknown. Cross-machine checks remain pending as recorded in the baseline plan.
