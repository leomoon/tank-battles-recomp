# Read-only online pacing diagnostics

The title executable includes `online_pacing.cpp`. `scripts/patch_online_pacing.py`, called by the existing network patcher during source and consumer configuration, attaches exact instruction hooks in `func_001A33CC`, `func_0016EEBC`, `func_0016DD64`, `func_0015B0BC` and the sendto/recvfrom import stubs. The patcher validates every required function and instruction before writing, tolerates game-free fixtures containing none, rejects partial or ambiguous title layouts, and is idempotent. It does not depend on generated chunk numbers or executable addresses. The existing runtime archive ABI stays unchanged; consumer packages carry the diagnostic source and its host clock header.

Set `TB_PACING_TRACE` to an output JSONL path and `TB_PACING_TRIGGER` to a trigger-file path before launching. Both are mandatory to create the writer. `TB_PACING_SECONDS` selects a 1–120-second window, default 20 seconds. `TB_PACING_ROLE=host` or `invited` labels the operator-selected role; otherwise the header says `unspecified`. The label does not infer role from a nonzero correction, since a client may have a zero correction. Start actual gameplay, then create the trigger file. Each process records at most one window. Restart for another window.

```sh
# Set these separately for each test process before launching it.
export TB_PACING_TRACE="$PWD/analysis/online-fps/step2/B-pacing.jsonl"
export TB_PACING_TRIGGER="$PWD/analysis/online-fps/step2/B-pacing.trigger"
export TB_PACING_ROLE=invited
export TB_PACING_SECONDS=20
# After verifying live gameplay:
touch "$TB_PACING_TRIGGER"
python scripts/pacing_report.py "$TB_PACING_TRACE"
```

With diagnostics disabled, each hook returns after checking the cached recorder state. Merely setting the path and trigger arms the recorder without collecting events; inactive-versus-active comparisons use the same binary. With diagnostics armed, a host worker polls the trigger every 10 ms. During recording, producers append numeric records to a preallocated 32,768-event buffer with a nonblocking mutex attempt. Full-buffer and contention drops are counted. The worker closes the window and writes the file after recording, keeping file I/O off the guest threads. Shutdown stops and joins the worker and flushes an interrupted window; storage remains alive during guest-thread teardown. An interrupted window has `complete: false` in its header and must not be used as a complete interval.

The JSONL header gives schema, role, requested host-time boundaries, completion status, capacity, dropped events and retained-event count. Event kinds are 1 (first pacing comparison while waiting), 2 (comparison allowing exit), 3 (completed correction write), 4 (sendto completion), 5 (recvfrom completion), 6 (received frame-snapshot subtraction), 7 (peak selection before decay), 8 (constructed numeric frame header), and 9 (completed pacing-field reset). Intermediate pacing polls are counted but not appended. Register and memory reads preserve the original floating-point deadline and comparison; the hooks do not recompute or replace the decision and do not write guest state.

Pacing records include the pacing-object address, previous guest timestamp, exact observed guest timestamp, current pacing interval float bits, raw unsigned correction, raw state flags, original floating deadline, original floating observed time and poll count. Correction-write records normalize the field address back to the pacing-object base. The field is unsigned in the pacing deadline. Received snapshot differences use signed 32-bit subtraction before positive values enter an unsigned peak calculation, described in [the arithmetic audit](ONLINE_FRAME_PACING.md). Raw flags are diagnostic state, not an automatic role classification.

All records carry local host monotonic nanoseconds from `tb_host_now`, the same clock domain used by the existing frame-budget and gameplay timing tools on that host. Frame-budget text timestamps subtract an origin; its startup log now records `[frame-budget] base_monotonic_ns=...` so the report can restore absolute timestamps exactly. Align events by these timestamps. For pacing events, `previous_guest_us`, `observed_guest_us`, `deadline_guest_us` and `observed_float_us` belong to the title-relative monotonic guest clock and must not be subtracted from host nanoseconds. Kinds 6–8 reuse numeric fields for network-clock operands, as specified below; those are a separate clock domain. Neither domain may be compared directly with another machine's local clock.

Socket records contain only local socket number, caller LR, raw return value, local call-start time and completion time. `local_sequence` is a monotonically increasing local completion number for that direction; `tx`/`rx` snapshot both local counters at append. Failed calls count too. These counters correlate correction writes and pacing decisions with local socket activity; they are not decoded wire sequence IDs, remote sender clocks or a direct RTT measurement. A recvfrom already blocked when recording starts has no captured call-start timestamp. The socket hooks do not read payloads. The separate title hooks read only the three numeric frame-header words described below, without copying payloads, credentials, account identifiers or remote addresses.

Use `scripts/pacing_report.py TRACE --json REPORT` to inspect correction values, guest deadline lateness, polls, local socket durations and time since the latest local receive completion. Temporal proximity does not establish which packet caused a correction. Dropped events invalidate claims that depend on a complete packet/correction timeline. Keep these traces separate from clean Step 1 baselines. The meaning and limits of the title's correction are recorded in [ONLINE_FRAME_PACING.md](ONLINE_FRAME_PACING.md).

To correlate a pacing trace with frame-budget flip intervals from the same process, pass `--frame-budget TRACE --startup-log LOG` to `scripts/pacing_report.py`. It requires exactly one logged frame-budget clock origin, adds that origin to the relative trace timestamps, and reports how many pacing/socket events overlap complete frame intervals. Older traces without the clock mapping are rejected for this correlation mode. The frame trace must actually overlap the pacing window.

## Producer and reset operands

These additional event kinds reuse the schema-1 numeric slots; inspect `kind` before interpreting them. All are read-only. The frame-header hook runs immediately after construction, not on arbitrary incoming bytes. It reads the frame index and two network-clock timestamps, without reading the account or gameplay payload.

| Kind | `object` | `local_sequence` | `previous_guest_us` | `observed_guest_us` | `correction_u32` |
| --- | --- | --- | --- | --- | --- |
| 6 | Synchronization queue | Matched frame index | Stored local frame snapshot | Remote frame snapshot | Raw wrapped remote-minus-local difference |
| 7 | Synchronization queue | Unused | Retained peak | Pending received peak | Unsigned maximum before gating/halving |
| 8 | Constructed header buffer | Outgoing frame index | Main-update network-clock snapshot | Network clock at header construction | Unused |
| 9 | Pacing object | Unused | Updated previous-frame timestamp | Unused | Field value immediately after its reset |

For kind 9, `flags` packs pacing bytes `+0x122`, `+0x12c` and `+0x12d` at shifts 16, 8 and 0. It observes those bytes at the reset instruction, before any later instructions in that return path. Kind 7 precedes the clock-ready, 512 µs threshold and pacing-phase checks, so a peak record does not guarantee a later correction write. Pacing exit records (kind 2) describe the wait-loop exit decision; reset records (kind 9) also cover paths which skip that loop.

Run `python scripts/online_pacing_audit.py TRACE --phases PHASES.json --gameplay TIMING.jsonl --json REPORT.json` to check retained deadline arithmetic, received subtraction, maximum selection, correction halving and zero-valued resets. `PHASES.json` is a list of local host-monotonic `start_ns`/`end_ns` intervals with labels; gameplay timing is optional. The default rejects interrupted or lossy recordings. `--allow-dropped` permits retained-event arithmetic and summaries with `lossless: false`; it never establishes a complete producer/reset timeline. The offline model in `scripts/online_pacing_model.py` is an analysis tool and is not linked into the game.
