# Player settings and diagnostic switches

Ordinary Linux setup uses the `player` profile: Release, effective `-O2`, no debug information and a stripped executable in `build-player/`. `cmake --preset developer && cmake --build --preset developer --parallel 4` selects RelWithDebInfo in `build-developer/`. Both profiles require the locally generated translations from setup. Build measurements are collected with `scripts/profile_build.py`; its per-job RSS values are individual compiler peaks, not total simultaneous memory use.

The FPS overlay starts hidden unless a saved preference or `TB_FPS_OVERLAY=1` enables it. F3 toggles and saves it. Windowed size and fullscreen changes are saved beside the user data in `game/settings.ini`. `TB_SETTINGS_FILE` selects a different file; an empty value disables persistence for diagnostics. Explicit environment values override saved preferences. The settings affect only the host window and overlay.

Normal launches capture output in `${XDG_STATE_HOME:-$HOME/.local/state}/tank-battles-recomp/logs/`, with a unique session filename printed to the terminal. `TB_LOG_DIR` changes the directory. `python scripts/port.py run --console` leaves output on the terminal; `--log analysis/example.log` captures a diagnostic session at a chosen path. Direct executable launches use the same log directory; `TB_LOG_CONSOLE=1` keeps their console output. `PS3_VERBOSE` defaults to `0` and reduces high-volume SDK logging; setting it to `1` explicitly enables that logging.

## Reference and comparison paths

These switches remain available in both build profiles. Unset them for the accepted production behavior. They change host implementation choices, not guest settings.

| Switch | Default | Comparison setting |
| --- | --- | --- |
| `TB_VBLANK_HZ` | `60` | `62.5` selects the old 16 ms beat; accepted range is 30 to 240 Hz |
| `TB_PRESENT_THREAD` | on | `0` presents on the walker thread |
| `TB_PRESENT_ON_VBLANK` | on | `0` presents when the renderer reaches the flip |
| `TB_VK_PRESENT_MODE` | MAILBOX if available, otherwise FIFO | `fifo` forces FIFO |
| `TB_LABEL_SYNC` | off | `1` waits for GPU completion before label/reference publication |
| `TB_VK_ASYNC` | on | `0` restores serial draw-batch submission |
| `TB_VK_SUBMIT_MODE` | `grouped` | `serial` restores per-operation command recording |
| `TB_GPU_RESOLVE` | on | `0` uses the guest-memory CPU blit path |
| `TB_DEPTH_GPU` | on | `0` uses the reference CPU depth conversion |
| `TB_TEXTURE_DECODE_MODE` | `specialized` | `reference` selects the original decoder |
| `TB_VK_FENCE_POLL` | off | `1` enables bounded fence polling before the blocking wait |
| `TB_COLOR_READBACK_CACHE` | off | `1` enables the experimental readback cache |
| `TB_DRAW_REMAP_CACHE` | off | `1` enables the experimental index-remap cache |
| `TB_DRAW_REMAP_VERIFY` | off | `1` checks reused remaps against fresh computation |
| `TB_FPS_OVERLAY` | off, or saved preference | `1` shows it, `0` hides it |
| `PS3RECOMP_FULLSCREEN` | off, or saved preference | `1` starts in fullscreen, `0` in a window |

For example, `TB_VK_ASYNC=0 python scripts/port.py run --console` runs the serial reference path. A reference path may be much slower; it is not a player performance setting.

## Opt-in diagnostics

`TB_FRAME_BUDGET=<file>` records the per-frame timeline; `scripts/frame_budget.py` summarizes it. `TB_PAGE_CENSUS=1` enables Linux memory-touch tracing. `TB_RENDER_PROFILE`, `TB_GPU_TIMING`, `TB_TEXTURE_DECODE_PROFILE` and `TB_GAMEPLAY_TIMING` select their respective output paths. Video, frame/texture captures, verification and all profiling are off unless explicitly selected. They add measurement work and do not redefine production defaults; compare runs only with matching declared options. These traces and captured game data belong under ignored `analysis/`.

## Recorded build sizes and resource use

Observed on the development Linux host (Ryzen 9 7950X, GCC 16.2.1, eight parallel jobs), using fresh build directories and identical generated game sources. These are local observations, not hardware-independent build requirements. Wall times include other system load. RSS is the maximum individual compiler job, not the peak of all eight jobs together.

| Profile | Configure + build | Peak compiler job RSS | Executable |
| --- | --- | --- | --- |
| Player Release | 5m 03s | 1.11 GiB | 94.03 MiB |
| Developer RelWithDebInfo | 6m 48s | 1.34 GiB | 590.87 MiB |

All 74 generated PPU compiler invocations in each profile use effective `-O2`. Release uses `-g0` and has neither `.debug_info` nor `.symtab` in the final stripped ELF; the developer executable retains both. Detailed local records are under `analysis/player-builds/`.

The player Release passed a clean 60-second default Battle check on this host with the overlay off and PipeWire audio: 59.9667 completed GAME FPS, mean 16.67595 ms, p95 16.76072 ms and p99 16.78321 ms. Strict fallback counters were zero and the session exited normally. Hardware draw-group fixtures passed async, fence-poll and serial modes. These results cover the selected route, not every mission or Windows; local evidence is under `analysis/performance/player-release-acceptance/`.
## Private RPCN sign-in

The native player defaults to offline. To test sign-in, create a private profile outside the game directory and give it these settings. Keep the file readable only by your account.

```ini
endpoint=lmsvc.lan:31313
ca=rpcn-cert.pem
npid=YourPlayer
password=your-rpcn-password
email=player@example.invalid
```

Use the certificate your server operator supplied for a private server. The `ca` path is relative to the profile. For a server with a publicly trusted certificate, omit `ca`. Account creation needs `email`. A server that validates email also requires the issued `token` for sign-in.

```bash
python scripts/port.py rpcn create /path/to/profile.ini
python scripts/port.py rpcn check /path/to/profile.ini
python scripts/port.py run --rpcn-profile /path/to/profile.ini --console
```

Pass `--build-dir` when using a build outside the project's configured default. For a standalone player, the equivalent executable arguments are `--rpcn-create-account FILE`, `--rpcn-check FILE`, or `<PPU ELF> --rpcn-profile FILE`. Sign-in opens the Online Battle submenu. [Native RPCN rooms](RPCN_ROOMS.md) covers room support and its tests. Live peer signaling and synchronized gameplay are still in development.
