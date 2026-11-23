# Online Battle and Friends

In **Play Tank Battles**, choose **Sign in** or **Create account**. For the public server, enter `play.lmx.to:31313` in **Server**. Each player needs a distinct account on the same server. Choose **Play online as** your username to start the game, or **Play offline** for local play. [Account and server settings](RUNTIME.md#private-rpcn-sign-in)

Friendships and invitations belong to the server you signed into. The Friends panel shows your current account and server.

Type directly into a focused field in the launcher. To use the on-screen keyboard, click **KB** or focus the field with the D-pad and press **PlayStation X** or **Xbox A**. Select characters with the D-pad, then choose **Done** or press **Start** to keep your edits. **Cancel** discards the edit.

## Open Friends

Press **F2** in the game window. On the first controller, hold **Select** and press **Triangle** on a PlayStation-style pad, or hold **View** and press **Y** on an Xbox-style pad. From a private lobby, you can also open **Invite Friends → Manage Friends**.

Use Left and Right to switch between **Friends**, **Received Requests**, **Sent Requests**, and **Add Friend**. Use Up and Down to select a username or action. Confirm with **PlayStation X**, **Xbox A**, or **keyboard Z**. Go back with **PlayStation O**, **Xbox B**, or **keyboard X**. The match keeps running while Friends is open.

## Add a friend

1. Open **Add Friend** and confirm **Edit Username**. Enter the other player's RPCN username exactly as it appears on that server.
2. With a keyboard, press **Enter** to finish editing, **Backspace** to delete, or **Esc** to cancel. While editing, Z and X type letters. With the on-screen keyboard, use the D-pad to select characters and **PlayStation X** or **Xbox A** to type. **Triangle / Y** changes case, **Square / Xbox X** deletes, and **Start / Menu** or **Done** finishes.
3. Select **Send Request** and confirm. The request appears under **Sent Requests**. Select it there to **Cancel Request**.
4. The other player opens **Received Requests**, picks your name, and chooses **Accept Request** or **Decline Request**. The shortcut opens **Received Requests** directly while the incoming request toast is visible. Requests sent to an offline player appear after they sign in.

After acceptance, the **Friends** list updates for both players during the same session. To remove a friend, select their name, choose **Remove Friend**, and confirm. If the connection drops, reopen **Online Battle** to reconnect and refresh the list.

## Invite a friend to a private Battle

1. In **Online Battle**, create a game and choose the level and mode. Leave the room private and select **Invite Friends**.
2. Choose an online friend and confirm. If you still need to add them, choose **Manage Friends** from the invite list. Once they accept, go back to the same picker. Your chosen level stays selected and the friend appears in the invite list while they are online.
3. The recipient sees a short invitation toast. In **Online Battle → Accept Invitation**, select the sender and confirm to join their room. If the inbox says **No invitations**, press Back once to return to Online Battle. New invites appear while the inbox is open.
4. Start the match from the host's lobby.

## Join a public Battle

The host chooses **Go Public** after creating a room. Other players open **Online Battle → Join Game** to see the host and player count for each room. Select a room and confirm to join. Refresh an open list with **PlayStation Square**, **Xbox X**, or **keyboard A**, including when the list is empty.

## Peer transport

The port implements Matching2 discovery, room creation, search, joining and leaving, internal room updates, and room messages against RPCN protocol 34. It uses the title's communication ID from guest memory. RPCS3's NP interfaces and RPCN's `np2_structs.proto` are the reference for the wire and guest ABI. The native player runs independently of RPCS3.

Each computer needs its own RPCN account and UDP port 3658 available for the native player. RPCN uses TLS on the configured TCP endpoint and UDP 3657 for endpoint discovery. Gameplay travels directly between players. Forwarding only RPCN's ports through a web reverse proxy does not carry that traffic. Stock RPCN reports local peers on port 3658, so two players on the same computer cannot currently share that port.

Room search requests the owner's ping record before joining. Stock RPCN supplies a fixed 20 ms value. The adapter checks the real peer's reachability through endpoint discovery and signaling.

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

Local checks cover malformed discovery responses, loopback challenge and acknowledgment exchanges, nonce rejection, and guest socket handoff and filtering through the production SDK.

## Friends and invitation diagnostics

Friends actions use the running game's RPCN connection. The panel shows success after the server acknowledges a change. If the server times out, reconnect and check the list to confirm what happened. Leaving the lobby closes Friends and cancels a pending invite picker. If a guest leaves and the host keeps the room, the host can continue using the same picker.

The game-free `friends_ui` fixture drives the production Friends controller, Basic callbacks, RPCN transport, and Matching2 against two authorized test accounts. It checks request and acceptance, live invite choices, delivery of the 24-byte room attachment, joining the same private room, and panel cleanup when either player leaves.

```bash
python scripts/test_rpcn_friends_ui.py \
  --runner build-rpcn-tests/friends_ui \
  --host-profile /private/player-a.ini \
  --peer-profile /private/player-b.ini
```

Enable `TB_OFFLINE_TRACE=1` for guest menu and network traces. The `[rpcn-basic]` lines record invitation fields, callback results, and attachment reads.

The maintainer tested Friends with two accounts. Earlier LAN playtests reached private Battle with invitations in both directions between Linux and Windows, and with a Linux host and two Windows guests.
