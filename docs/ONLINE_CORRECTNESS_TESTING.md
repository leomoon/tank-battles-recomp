# Online pacing correctness tests

The `stale-phase` experiment remains disabled by default. Completed FPS alone cannot establish synchronization correctness: the title can advance its network frame counter near 60 Hz while rendering fewer frames. Keep full-round correctness observations separate from short FPS comparisons. Record original/candidate assignment, map, lives/time, peer topology and test conditions, then compare timer progression, movement and fire responses, hits, pickups, scores, winner and debrief on every peer. A missing observation is pending, not a pass.

## Isolated packet conditions

`scripts/online_network_lab.py` is a standalone developer UDP relay. It is not imported by the game, included in builder resources or enabled by any player setting. It affects only packets explicitly routed to its configured sockets. It never changes firewall rules, routing tables, network interfaces or desktop configuration. Its report contains aggregate counts rather than packet payloads, account credentials or remote account state.

Each link has a listening socket, target endpoint and independent policy. Forwarded gameplay datagrams retain their bytes. Delay and deterministic jitter schedule delivery; every-N loss omits a datagram; duplication adds an identical copy two milliseconds later; every-N reordering adds an extra delay so later packets can pass it. These are repeatable fault schedules, not a statistical model of the Internet. A bounded queue reports overflow separately from intentional loss, and real send failures are counted. The relay cannot preserve UDP send-error semantics across the separate forwarding hop; use its failure counters and do not treat it as a production transport.

Example configuration for two disposable local UDP fixtures:

```json
{
  "links": [
    {"name": "A-B", "listen": ["127.0.0.1", 39001], "target": ["127.0.0.1", 3659]},
    {"name": "B-A", "listen": ["127.0.0.1", 39002], "target": ["127.0.0.1", 3658]}
  ]
}
```

Run the relay separately, then route only the disposable peers through those listeners:

```sh
python scripts/online_network_lab.py --config analysis/lab/relay.json \
  --control analysis/lab/conditions.json --stats analysis/lab/stats.json
```

The optional control file must define every link. Replace it atomically to change conditions during a round. For example:

```json
{
  "A-B": {"delay_ms": 35, "jitter_ms": 10, "drop_every": 17, "duplicate_every": 13},
  "B-A": {"delay_ms": 5, "reorder_every": 11, "reorder_delay_ms": 35}
}
```

Empty policies restore immediate delivery. Policies are validated before replacement; malformed or incomplete controls keep the last valid policy and increment an error count. Already queued packets keep their original deadlines. Sequence counters do not reset on a policy transition. Policies allow integer delays/jitter up to 1,000 ms and bounded unsigned counters/seeds. Stop the relay with SIGINT or SIGTERM to write final counts, including packets still queued at stop.

## Linux same-host adapter

RPCN's same-LAN endpoint selection returns the advertised local address and console port 3658. Two local games therefore need explicit test address/port translation. `scripts/online_test_ports.c` supplies an optional Linux `LD_PRELOAD` adapter for these disposable tests. Build it separately; it is not part of the player or builder:

```sh
cc -shared -fPIC -Wall -Wextra -Werror scripts/online_test_ports.c -ldl \
  -o analysis/lab/ports.so
```

For a two-peer relay, set A's `TB_LAB_LOCAL_PORT=3658`, `TB_LAB_SEND_PORT=39001` and `TB_LAB_RECV_PORT=39002`; set B's corresponding values to 3659, 39002 and 39001. Both use `LD_PRELOAD` pointing to the adapter. Configure listeners and targets with the actual local route address when signaling expects that address, rather than substituting loopback. The adapter redirects destinations at port 3658 and restores the expected source port on receipt. Other traffic passes through. The adapter itself has no packet queue or pacing hooks.

For three local peers, the adapter can advertise distinct virtual LAN addresses `127.0.0.101`, `.102` and `.103` while binding real local ports 3658, 3659 and 3660. Set `TB_LAB_VIRTUAL_IP` per peer and `TB_LAB_HOST_IP` to the real local route address. The adapter translates only the four local-address bytes in the 13-byte RPCN UDP discovery registration, then translates socket endpoint metadata between virtual peer addresses and the real ports. It does not edit gameplay packet bytes. `TB_LAB_VIRTUAL_SEND_PORTS` and `TB_LAB_VIRTUAL_RECV_PORTS` optionally provide comma-separated A/B/C relay-port mappings; zero entries use the corresponding direct local port. Use one relay link for each directed peer pair to impose unequal conditions. This is a same-host test fixture, not a change to RPCN, the guest protocol or operating-system networking.

The three-peer arrangement must not be presented as separate-machine acceptance. Capture each owned game window separately, keep the windows ungrouped and keyboard control enabled, and save per-peer outcomes. Do not capture unrelated desktop content. Stop only processes created by the test, and restore every injected key or temporary process stall in a `finally` block.

## Verification and acceptance

```sh
python -m unittest discover -s scripts -p 'test_online_network_lab.py'
```

The tests use game-free byte fixtures and real loopback sockets. They verify deterministic delay/jitter/loss/duplication/reordering, link independence, queue bounds, policy transitions, forwarded byte identity, source metadata, native adapter routing and virtual discovery-address translation. A compiled probe exercises the actual `online_pacing_recovery.h` helper under the scheduled stale, reordered and duplicate frame deliveries and confirms that fresh estimates retain original behavior. That probe checks provenance rules, not game simulation or full protocol correctness.

For live acceptance, include unchanged peers in both host assignments, full finite rounds, three players with unequal link conditions, invitation and public-room joins, map changes, rematches, disconnect/rejoin and temporary stalls. Verify that positive correction waits and recovery phases still occur under poor conditions. A matching winner alone does not establish identical movement speed, input handling, hit/pickup delivery or every player's score history. Trace drops limit conclusions about complete event histories. Do not enable the candidate as a release default until those checks and sustained performance acceptance pass.

## Recording and capture limits

For lifecycle tests longer than the default ten-minute telemetry window, set `TB_GAMEPLAY_TIMING_SECONDS` explicitly (1–3,600 seconds). New recordings include `duration_limit_ns` and a terminal `recording_ended` row. `FrameProgress.observe_record` treats that expiry as an ended diagnostic, not a game stall; it still detects stopped completed frames inside the active recording window. Expired telemetry cannot establish whether the game continues running.

On Xwayland with Vulkan, direct X11 backing-pixel captures can be stale even when the compositor shows advancing gameplay. Use compositor captures clipped to the owned windows after geometry has settled, and cross-check independent completed-frame telemetry within its valid window. A sampled stack inside SDL polling does not establish an SDL lock; repeated stacks and continuing timing/state evidence are needed. The Step 5 investigation withdrew an earlier freeze interpretation after discovering both test-method limits.

Record whether a human is controlling any peer during a timed input probe. Concurrent manual play can verify delivery of shots, pickups and score changes, but invalidates a controlled movement-speed comparison. Preserve those observations with that limitation rather than treating the injected key duration as the only input.

Distinguish a rendering stall from a pacing wait. In one three-player run, an invitee stopped completing frames inside an active recording window; repeated stacks showed the title waiting on its flip semaphore, and FIFO counters showed one queued flip never reached. The unchanged host and remaining invitee also slowed. The SDK overlay now prevents the walker's overflow fallback from rewinding title-owned command contexts, whose segment transitions the title itself manages. The game-free flip-callback fixture reproduces the erroneous rewind and verifies both preservation of title-owned contexts and continued runtime-owned recycling. That regression test does not by itself establish that every live stall or slowdown has the same cause. Continue full-round and lifecycle validation after rebuilding.
