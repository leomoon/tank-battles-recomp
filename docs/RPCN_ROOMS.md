# Native RPCN rooms

The optional RPCN profile selects the server. The runtime contains no fixed LAN address. [Player settings](RUNTIME.md#private-rpcn-sign-in) describes the profile, certificate and account commands. The default player stays offline.

The port implements Matching2 context creation, discovery, room creation/search/join/leave, internal room updates and room messages against stock RPCN protocol 34. It uses the title's communication ID from guest memory. RPCS3's NP interfaces and RPCN's `np2_structs.proto` are the reference for the wire and guest ABI. RPCS3 is not required to run the native player.

Room search also requests RPCN's owner-ping record before joining. Stock RPCN supplies a fixed 20 ms value; this is not a measurement of the connection between the players. The current native route reaches a successful join and matching membership callbacks, then waits for the peer signaling implementation. The host's visible player slots do not confirm the peer until that connection is ready.

Select **Online Battle**, create a room, confirm the map and mode, then choose **Go Public** in the lobby. A second account can search through **Join Game**. A newly created room is private, so searching before publication returns no matching room. Live peer signaling and synchronized gameplay remain a separate implementation stage.

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
