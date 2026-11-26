# Online frame pacing audit

The invited player's lower completed-frame rate is associated with the title's original synchronization loop. The loop adds a positive, frame-indexed network-clock correction to a monotonic pacing deadline. It also changes pacing phases when the peer advances. This audit establishes the arithmetic and observed mechanism, but does not establish a recompilation defect or a safe way to remove required synchronization. The original behavior remains the default. Step 4 adds a developer-only candidate for a demonstrated conflict between an older timing estimate and a newer recovery frame; it is not approved for ordinary play.

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

The next implementation decision must account for frame-phase requests, calibrated clock offsets, the separate requested interval, and legitimate recovery waits. The developer experiment below retains the original path by default. It must prove preserved elapsed simulation time, peer frame progression, session resets and complete-round synchronization before its FPS gain is accepted. Equivalent paired RPCS3 timing and original PS3 timing have not been captured; original console behavior remains unknown. Cross-machine checks remain pending as recorded in the baseline plan.

## Step 4 candidate: invalidate a superseded frame-phase estimate

The first Step 4 investigation did not identify a causal signaling or calibration defect. Its complete, lossless ten-sample exchange had elapsed response times of 24–57 microseconds, all adjustments matched the original arithmetic, and their differences from the latest-response midpoint were at most nine microseconds. The unchanged game still showed approximately 59.2 host FPS and 43.8 invited FPS in a short Field of War II observation. `scripts/online_clock_report.py` checks these wrapped numeric differences. That observation does not justify replacing clock calibration or signaling latency.

The subsequent paired frame trace identified a more specific conflict. Host A completed 59.60 game FPS and invited B completed 49.16 FPS during 25 seconds on Field of War I. Every one of the 1,490 captured host frame headers matched an incoming client frame, with a mean header-construction-to-processing interval of 0.067 ms on this host. There were no duplicate incoming frame indices. The title's identity comparison stayed at local/owner IDs 16/16 on A and 33/16 on B; it did not transiently mistake the invited account for the owner.

B requested 265 recovery phases. The host frame was one ahead of B on 264 requests and two ahead on one request. In all 265 cases, the next pacing wait exited with the recovery flag set. On 249 of those exits it still applied a positive correction, averaging 2.447 ms across all 265 exits. The latest positive timing samples at those requests were between one and seven host frames older than the recovery frame. The host trace was lossless; B dropped one diagnostic record, so its counts describe retained events rather than a complete event history. Separately recorded gameplay FPS is not calculated from diagnostic event counts.

This supports an experimental hypothesis: a positive phase estimate from an older frame should not continue to delay catch-up after a newer host frame establishes that the client is behind. It does not establish that every positive correction is wrong, that the original PS3 behaved differently, or that the candidate is correct under all network conditions. The original path therefore remains the default.

### Exact developer opt-in

```sh
TB_EXPERIMENTAL_ONLINE_PACING=stale-phase ./TankBattlesRecomp ...
```

Only the exact value `stale-phase` enables the candidate. Unset, empty, `0`, `1` and misspelled values all leave the original path active. The startup log names the enabled experiment. This is a developer environment switch, not a launcher setting or a release default.

The title executable's `tb_pacing_experiment` is separate from the read-only recorder. It follows the original positive-sample acceptance, maximum selection and halving through exact hooks. `online_pacing_recovery.h` tracks the source frame and expected value of the pending peak, retained peak and published pacing correction. A matching value alone is insufficient; its frame provenance must also be known. Equal maxima keep conservative provenance, and ambiguous ordering falls back to the title's behavior.

At the existing receive-side recovery request, immediately before the title writes its phase flag, invalidation is permitted only when all of the following hold:

- The original client-role branch has been taken, the queue is active and nominal pacing is enabled.
- The received host frame is newer than the client's current frame under unambiguous 32-bit frame ordering.
- A positive published correction still equals the tracked value and comes from an older host frame.
- Every nonzero pending or retained peak also equals its tracked value and comes from an older host frame. A fresh, unknown or overwritten peak preserves the original control signal.
- This is a newer recovery request than the last request on which the candidate acted.

Only then does the candidate clear those superseded pending/retained estimates and the corresponding pacing correction. It leaves the title's original recovery flag, recovery counter, nominal interval, clock epoch, frame increment, simulation updates, input handling, packet format and packet delivery paths intact. A new valid positive timing sample follows the original path again. This is selective invalidation of a tracked older-frame phase estimate, not a blanket zero or a magnitude cap. Whether the old estimate can safely be superseded in every required scenario remains subject to Step 5 acceptance.

### State, fallback and validation

Provenance resets at network-manager start/teardown, queue construction/reinitialization, transport teardown, clock calibration, the previous-timestamp reset callback and changes in the title's local/owner identifier pair. An unexpected concurrent queue invalidates provenance and disables the experiment until a lifecycle reset. No sample from a previous session may qualify a new session's correction for invalidation. Missing, stale, ambiguous or unsupported state keeps the title's original behavior.

The helper has no timers, background mutation or altered clock source. Both source and consumer configuration attach the same hooks through `scripts/patch_online_pacing.py`; generated chunks are not persistently hand-edited. The title object and packaged header carry the candidate without adding a runtime archive ABI dependency. Read-only diagnostics still observe original values before an experimental action, and kind 14 records each actual invalidation separately.

Deterministic tests cover exact opt-in/default behavior, tracking through the title's halving, preservation of fresh and unknown estimates, overwritten values, repeated recovery requests, negative timing samples, large values, frame-index wrap and its ambiguous half-period, and clearing provenance between sessions. Register-preservation tests also cover the native adapter. The candidate remains disabled for ordinary use until complete-round correctness, changing network conditions, host swaps, map changes, reconnects and three-player acceptance establish that it preserves required synchronization. Short diagnostic FPS checks do not satisfy that acceptance.

### Initial live comparison

A short Field of War I comparison used Native/100%, FXAA off, PulseAudio, two human slots with neutral input and two CPU tanks, infinite lives/time, and visible ungrouped windows. The same-machine test-only port translator ran with zero added delay. Builds finished before measurement. Completed game frames came from independent `TB_GAMEPLAY_TIMING`, rather than pacing calls, vblank or window presentations.

| 20-second run | Host A completed FPS | Invited B completed FPS | B mean correction at wait exit | Retained B invalidations |
| --- | ---: | ---: | ---: | ---: |
| Candidate disabled | 59.65 | 46.20 | 3.041 ms | 0 |
| Candidate enabled only on B | 59.60 | 50.25 | 0.764 ms | 162 |

All 162 retained invalidations followed a host frame one ahead of the client; their source frames were one to four frames older than that host frame. The host recorded no experimental actions. Both recordings completed, but B dropped one diagnostic record in the disabled run and two in the enabled run; retained counts do not establish complete action histories. Gameplay FPS uses a separate recorder. The disabled run preceded the final added role/lifecycle reset hooks; its experiment was disabled, so those hooks return without changing guest state. The final candidate executable and changed-source hashes are recorded in `analysis/online-fps/step4/candidate-verification.json`.

The candidate did not restore 60 completed FPS. A single short comparison does not establish a repeatable performance improvement, particularly given the phase sensitivity in Step 3. It establishes that the opt-in hook activates during gameplay while an unchanged peer remains connected. No full-round outcome, simulation-speed, adverse-network, three-player or separate-machine acceptance is claimed. The detailed records are in `analysis/online-fps/step4/RESULT.md`; Steps 5 and 6 remain required before considering a release default.

## Step 5 investigation status

The isolated packet-condition harness and its validation workflow are documented in [ONLINE_CORRECTNESS_TESTING.md](ONLINE_CORRECTNESS_TESTING.md). Mixed original-host/candidate-invitee rounds in both host assignments reached agreed debriefs, including one round with packet faults and a bounded process stall. These observations do not establish precise movement/hit/pickup equivalence or a sustained FPS gain; the healthy invitee still fluctuated around the high 40s. Full Step 5 acceptance remains pending in `analysis/online-fps/step5/RESULT.md`. The investigation also found the independent public-room capacity bug, fixed separately in commit `3370a80`; that browser fix does not depend on the pacing experiment.

## Remaining delay after the initial candidate

Fresh Step 5 captures now account for the ordinary slowdown with complete, lossless overlapping pacing and frame-budget recordings. In the original control, independent completed FPS is 59.50 host and 48.83 invitee. Their mean FIFO frame intervals are 16.804 and 20.458 ms. The invitee adds 2.590 ms in main-thread semaphore waits, 0.643 ms in pacing waits and 0.420 ms in remaining wall time. Both request exactly 16.667 ms during the capture. Separate unique flip sequences show longer FIFO-execution-to-vblank delay on the invitee, 6.213 versus 3.442 ms; that chain overlaps the semaphore wait and must not be added again.

The invited overlap contains 714 pacer resets for 584 completed frame intervals. Its 130 intervals with two resets average 26.900 ms, compared with 18.613 ms for its one-reset intervals. This targets recovery/alternate-phase timing and flip scheduling for the next causal experiment; it does not prove the extra reset is erroneous or measure simulation updates. Zero-correction frames also remain slower than the host. The stale-phase run still falls below 60 FPS and its lossy invitee trace supports retained observations only. Complete measurements, identity records, overhead comparison and interpretation limits are in `analysis/online-fps/step5/REMAINING_DELAY.md`. The experiment remains disabled by default and the FPS issue remains unresolved.
