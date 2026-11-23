# Native RPCN rooms

The optional RPCN profile selects the server. The runtime contains no fixed LAN address. [Player settings](RUNTIME.md#private-rpcn-sign-in) describes the profile, certificate and account commands. The default player stays offline.

The port implements Matching2 context creation, discovery, room creation/search/join/leave, internal room updates and room messages against stock RPCN protocol 34. It uses the title's communication ID from guest memory. RPCS3's NP interfaces and RPCN's `np2_structs.proto` are the reference for the wire and guest ABI. RPCS3 is not required to run the native player.

Room search also requests RPCN's owner-ping record before joining. Stock RPCN supplies a fixed 20 ms value; this is not a measurement of the connection between the players. The adapter now implements peer endpoint discovery and signaling activation. Live two-computer gameplay acceptance is still pending.

Select **Online Battle**, create a room, confirm the map and mode, then choose **Go Public** in the lobby. **Join Game** opens a public-room list showing each host and player count. Use Up/Down to select, PlayStation X or keyboard Z to join, Square or keyboard A to refresh, and PlayStation O or keyboard X to return. Even a single result requires confirmation. An empty list stays open for refresh. A newly created room is private and appears after its host chooses Go Public.

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

## Friend invitations

With an online RPCN profile selected, **F2** opens the centered Friends panel while the game runs. On the first controller, hold **Select/View + Triangle/Y**. A short toast shows these shortcuts after sign-in. **Invite Friends → Manage Friends** opens the same panel and returns to the preserved invitation picker and selected level when closed. Left/Right changes between Friends, Received Requests, Sent Requests, and Add Friend; Up/Down selects a row. PlayStation X / keyboard Z opens its details, and PlayStation O / keyboard X goes back. The panel shows the active account and server, because relationships belong to that server. Public room search, invitation acceptance, and system dialogs retain their own input. The roster shows which friends are offline. Received and Sent Requests stay separate from mutual friends, and the selected username stays selected when another player changes the list.

In **Add Friend**, press PlayStation X, keyboard Z, or Enter to edit the username. Type its exact capitalization with a physical keyboard, even if a controller is connected. Enter finishes editing, Backspace deletes, and Escape cancels the edit and restores the previous value. While editing, Z and X are letters. Controller users navigate the on-screen keyboard with the D-pad, press X to type a character, Triangle to switch letter case, Square to delete, Start or the on-screen Done key to finish, and O to cancel. Usernames require 3–16 letters, digits, hyphens or underscores. After finishing the username, select **Send Request** and confirm separately. The sender sees that the other player must accept it; the request appears in Sent Requests. Leaving the window cancels an unfinished edit; holding a closing key cannot activate a game control when the panel returns.

With both accounts signed in, the host can leave the lobby private and choose **Invite Friends**. The centered invite dialog lists online mutual friends and highlights the selected account. Its controls are labeled separately for PlayStation controllers and keyboard. Use Up/Down to choose, PlayStation X or keyboard Z to send, and PlayStation O or keyboard X to cancel. The picker consumes the closing button until release so it cannot also activate a lobby control. An incoming invitation shows the sender and a four-second countdown bar. The recipient can then choose **Online Battle → Accept Invitation**, select the sender and confirm. An empty inbox shows **No invitations**. Press PlayStation O or keyboard X once to return to the Online Battle menu. New invitations appear in the dialog if it stays open. The original game attachment supplies the room ID to Matching2.

Accounts must be mutual friends. An incoming friend request displays a four-second toast at the top left. Press F2 or the controller shortcut while it is visible to open **Received Requests**, select the sender, and choose **Accept Request** or **Decline Request**. In **Sent Requests**, select a request to cancel it. In **Friends**, select a player to remove them; a separate confirmation defaults to keeping the friend. Requests and invitations queue in arrival order rather than replacing an unread toast. These actions use the running game's connection, and success is shown only after the server acknowledges the change.

Accepting a request updates both signed-in players without a restart. The invitation picker refreshes when friends come online, go offline, are accepted, or are removed. A disappearing selected invite target returns selection to **Manage Friends**. Opening management keeps the pending invitation and its room attachment; return to the picker to invite a newly accepted online friend. Results from an abandoned management panel cannot reopen it.

Friends leaves the match running and captures player one's keyboard and controller input, including stick movement. Back closes a pending action's panel immediately. If the server never acknowledges a change, Friends reports that it could not confirm the result. Reopen **Online Battle** after losing the connection to retry the existing sign-in. The server supplies a fresh relationship list on reconnect.

Leaving the lobby closes Friends and cancels a pending invitation picker. If a guest leaves and the host keeps the room, the host can continue using the same picker. A closed room dismisses the panel for its remaining players.

The game-free `friends_ui` fixture drives the production Friends UI logic, basic callbacks, RPCN transport, and Matching2 against two unrelated LAN test accounts. It verified request/accept, live invite choices, preservation of the 24-byte room attachment, and joining the exact hidden room with two members without restarting either process. It also verified that guest departure preserves the host’s picker and host departure closes both Friends panels. Run `python scripts/test_rpcn_friends_ui.py --runner build-tests/rpcn/friends_ui --host-profile HOST.ini --peer-profile PEER.ini` against profiles authorized for test-server relationship and room changes. This fixture replaces the renderer and physical input; acceptance through the rendered game on Linux and Windows remains a separate check.

The Linux sender has delivered its original type-3, subtype-0, flags-1 invitation and 24-byte attachment through stock RPCN. The Linux recipient has read a test invitation through the game's real receive/event/attachment calls and displayed “The game room is unavailable” for a deliberately nonexistent room. A Linux host invited Windows to private room 60, and a Windows host invited Linux to private room 61. Both rooms reached Battle over the LAN. A Linux host also invited two Windows players to private room 64 and played Battle with both guests and one CPU tank. Separate validation of the updated picker on Windows is pending. Enable `TB_OFFLINE_TRACE=1` for guest menu/network traces. `[rpcn-basic]` lines record invitation fields, callback results and attachment reads, without passwords or tokens.
