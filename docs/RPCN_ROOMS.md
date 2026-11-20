# Native RPCN rooms

The optional RPCN profile selects the server. The runtime contains no fixed LAN address. [Player settings](RUNTIME.md#private-rpcn-sign-in) describes the profile, certificate and account commands. The default player stays offline.

The port implements Matching2 context creation, discovery, room creation/search/join/leave, internal room updates and room messages against stock RPCN protocol 34. It uses the title's communication ID from guest memory. RPCS3's NP interfaces and RPCN's `np2_structs.proto` are the reference for the wire and guest ABI. RPCS3 is not required to run the native player.

Room search also requests RPCN's owner-ping record before joining. Stock RPCN supplies a fixed 20 ms value; this is not a measurement of the connection between the players. The adapter now implements peer endpoint discovery and signaling activation. Live two-computer gameplay acceptance is still pending.

Select **Online Battle**, create a room, confirm the map and mode, then choose **Go Public** in the lobby. A second account can search through **Join Game**. A newly created room is private, so searching before publication returns no matching room.

## Peer transport

Each computer needs its own RPCN account and UDP port 3658 available for the native player. RPCN uses TLS on the configured TCP endpoint and UDP 3657 for endpoint discovery. Gameplay travels directly between players. Forwarding only RPCN's ports through a web reverse proxy does not carry that traffic. Stock RPCN reports local peers on port 3658, so two players on the same computer cannot currently share that port.

The signaling adapter retains the server-issued login user ID and registers its bound UDP socket with RPCN. It requests the other account's endpoint, then exchanges native-only `TBP1` challenge/ack packets. It promptly retransmits its challenge on the first incoming challenge to handle either peer starting first. A connection becomes active once the client has answered the peer's challenge and received a matching acknowledgment for its own. The adapter delivers established and extended activation events through the guest callback queue. Activation fails after eight seconds and an active peer that stops acknowledging fails after five seconds.

Tank Battles reads the signaling bandwidth field when a peer activates. The adapter reports the same nominal 100 Mbps value used by RPCS3 for an established connection. The UDP handshake verifies reachability and leaves throughput unmeasured.

An explicit RPCN profile enables the SDK host socket handlers. When the guest binds its first P2P datagram socket, `sysNet` adopts the discovery socket. The receive path consumes signaling control packets before copying game data into guest memory. Peek reads preserve game packets, and close/finalize notify the signaling adapter. `sys_net_abort_socket` interrupts a blocked native UDP receive so the title can join its thread. The SDK supports one P2P datagram virtual port.

For a bounded socket trace, launch each player with its private profile and enable `TB_OFFLINE_TRACE=1` and `PS3_NET_TRACE=1`. Retain both session logs. Signaling entries report context, connection, event and error; `sys_net` entries report socket, direction, endpoint and byte count. These are diagnostic logs and should stay private. Normal offline launch does not register the RPCN transport.

The transport serializes requests on the authenticated TLS connection and queues room notifications. Request IDs retain the guest's application ID in their upper 16 bits. Result images contain big-endian fields and pointer offsets that `sceNpMatching2GetEventData` relocates into guest memory. Callback payloads remain available until context cleanup or the bounded result cache evicts them. Context stop/destroy suppresses stale callbacks; a successful late create/join reply causes a real leave request.

RPCN elects a successor when an owner leaves. The adapter fetches the updated room and reports that owner to the guest. Stock protocol 34 has no kick command, so an explicit kick request returns `NOT_ALLOWED`; it does not pretend to remove a member. Implementing a host kick requires a server extension if the observed game route needs it.

Fast LAN discovery exposed a title event-lifetime race. The title expires unconsumed network status nodes after two update passes, which can occur while its Create/Join menu is still animating. `patch_network_events.py` adds a hook to the local lift that retains exactly the first discovery-success node until the game consumes it. It adds no response delay and does not change the result. The hook is inactive offline, and duplicate status nodes keep their original lifetime. CMake applies it for both source builds and runtime-bundle consumers; preparation applies it after a fresh lift.

## Game-free checks

```bash
cmake -S tests/rpcn -B build-rpcn-tests -DCMAKE_BUILD_TYPE=Release
cmake --build build-rpcn-tests --config Release
ctest --test-dir build-rpcn-tests -C Release --output-on-failure
python scripts/test_patch_network_events.py
```

Requires Python 3.11+, the vendored lifter's Python dependencies, a C++20 compiler and libcurl development files. The fixtures generate an empty common ABI header and provide their own small VM. They contain no game data. The existing Windows runtime CI also builds and runs the wire checks.

For a private server with two existing test accounts, close any game using those accounts and run the integration suite. It temporarily creates rooms, exchanges fixture messages and attributes, then leaves them. It never creates accounts or changes server configuration.

```bash
python scripts/test_rpcn_rooms.py \
  --runner build-rpcn-tests/matching_rooms \
  --host-profile /private/player-a.ini \
  --peer-profile /private/player-b.ini
```

On Windows, use `build-rpcn-tests/Release/matching_rooms.exe`. The suite swaps host roles, checks messages and attributes, verifies owner succession and full-room rejection, cancels an in-flight create, and confirms that no stale room remains. Run it on a dedicated test instance without unrelated public rooms for this title.

To isolate signaling from the game's menus, run this on each of two computers, using different accounts and the other player's NP ID:

```bash
matching_rooms /private/this-player.ini peer OTHER_PLAYER_NPID
```

The fixture waits up to two minutes for the other account's UDP registration, then checks real peer activation, the primary/extended callback arguments, the reported peer address and shared-context cleanup. Close any game using that account first. The Windows runtime bundle includes `bin/matching_rooms.exe`, `peer_udp_fixture.exe` and `sysnet_fixture.exe`, so this check needs no game or separate fixture build. Run `matching_rooms PROFILE signal UNUSED_NPID` to verify bounded failure for an unreachable peer.

Local checks cover malformed discovery responses, real loopback challenge/ack exchanges, nonce rejection and guest socket handoff/filtering through the production SDK. The LAN service has confirmed the Linux player's registration. Lobby agreement, actual game traffic and synchronized movement/hits on two computers remain unverified.
