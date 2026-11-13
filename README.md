# Tank Battles Recomp

An experimental PC port scaffold for Tank Battles on PlayStation 3 (NPUB30154), built with [ps3recomp](https://github.com/sp00nznet/ps3recomp).

**THIS REPOSITORY DOES NOT CONTAIN THE GAME.**

To recompile or run the game, you must provide your own decrypted dump of a legitimately purchased copy of Tank Battles for PlayStation 3. Cloning this repository does not supply the game or a license to it. No game executables, assets, decrypted dumps, firmware, entitlement files, private keys, or generated translations of the game code are included. Translated code is generated locally from your own dump.

## Status

The repository has a pinned ps3recomp dependency, a native runner, configuration, and scripts for analysis, translation, build, and launch. It is not a playable port yet. Missing system interfaces and game-specific behavior still need to be implemented and tested. The synthetic smoke test checks the runner independently of Tank Battles. The native Vulkan build now renders the actual title screen and its press-X prompt using translated guest shaders; basic gamepad input and audible sound are user-reported working. Visual glitches and slow rendering are reported; mission correctness, audio quality/timing, and saves remain unverified.

Initial static analysis of NPUB30154 found approximately 17,300 candidate PPU functions, 274 imports across 19 libraries, and one embedded SPU ELF of 3,136 bytes. Remaining validation includes graphics beyond the title screen, audio, repeated startup with fresh asset caches, media requirements, saves, and visible full-game and offline menu behavior. Scanning the main executable does not rule out additional code loaded from assets or system libraries.

## Structure

```text
tank-battles-recomp/
├── CMakeLists.txt          # Native executable and runtime integration
├── config.toml             # Paths and translation settings used by scripts/port.py
├── main.cpp                # PPU runner, graphics backend, and frame clock
├── stubs.cpp               # Tank Battles-specific runtime hooks
├── content_paths.cpp       # Title metadata, mount roots, and EDAT mapping
├── asset_probe.cpp         # Opt-in native filesystem verification
├── external/
│   └── ps3recomp/           # Pinned Git submodule
├── scripts/
│   ├── port.py             # Prepare, configure, build, and run
│   └── smoke_test.py       # Synthetic boot test requiring no game files
└── game/
    └── .gitkeep            # Only this placeholder is tracked
```

The `analysis/`, `recompiled/`, `spu/`, and `build*/` directories hold local generated output and are ignored by Git. Everything inside `game/` except `.gitkeep` is ignored, including files whose names are not covered by extension rules.

## Dependency

Clone the port with its ps3recomp dependency:

```bash
git clone --recurse-submodules https://github.com/leomoon/tank-battles-recomp.git
cd tank-battles-recomp
```

For an existing clone:

```bash
git submodule update --init --recursive
```

The submodule is pinned to the revision recorded in Git, initially `de4f090328ea08bef6b2f15caec0b85e343a7ece`. Do not use `git submodule update --remote` unless deliberately testing an SDK upgrade. Developers can use a separate SDK checkout with `--sdk ../ps3recomp` for `prepare` and `configure`, or `-DPS3RECOMP_DIR=/path/to/ps3recomp` with CMake directly. Shared runtime fixes belong upstream; game-specific hooks belong here.

## Requirements

- Python 3.11 or newer for the port scripts and TOML configuration.
- Git, CMake 3.20 or newer, Ninja, and a C17/C++20 toolchain. Clang is recommended, particularly for the SPU dispatch path.
- The Python dependencies listed in `external/ps3recomp/requirements.txt`.
- SDL2 development files on Linux and macOS.
- Vulkan development headers, the Vulkan loader, a working Vulkan driver, and glslang development libraries/CMake targets for Linux graphics. This port requires glslang guest-shader translation when configuring Vulkan. On Arch, the relevant development packages are `vulkan-headers`, `vulkan-icd-loader`, `glslang`, and `sdl2`, plus your GPU driver. SPIRV-Cross is not required for the Vulkan path. The null backend is available for headless diagnostics.

You can install the Python tools and portable build tools into a local environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r external/ps3recomp/requirements.txt cmake ninja
```

Native compiler, SDL2, Vulkan, and shader-tool dependencies must be available separately. Windows uses D3D12; macOS uses Metal. Platform build support does not imply that Tank Battles is playable.

## Local game dump

Put your purchased game's dump under `game/` using this layout:

```text
game/
├── .gitkeep
├── dev_hdd0/
│   └── game/
│       └── NPUB30154/
│           ├── PARAM.SFO
│           ├── USRDIR/
│           │   ├── EBOOT.elf
│           │   ├── assets_common.edat
│           │   ├── assets_images.edat
│           │   ├── assets_ps3.edat
│           │   └── assets_sound.edat
│           └── ...
└── dev_hdd1/                # Writable cache, created on launch
```

Copy the entire installed `NPUB30154` directory from your own dump, including its other files. RPCS3 can install your PKG and export `EBOOT.elf` through Tools → Decrypt PS3 Binaries. A decrypted executable alone is not the complete game. Protected EDAT assets must also be readable by the port through runtime support or a separately prepared decrypted asset dump. Full-game entitlement files from your purchased copy remain local; the Linux entitlement bridge verifies the purchased C00 unlock as described below.

The scripts read `config.toml` and look for `game/dev_hdd0/game/NPUB30154/USRDIR/EBOOT.elf` by default. Paths are relative to the repository root. To analyze a decrypted executable elsewhere, pass `--elf /path/to/EBOOT.elf`; this does not copy the executable or its assets. Set matching filesystem paths in the configuration before trying to run the game. `PS3_VFS_ROOT`, `PS3_HDD0_ROOT`, `PS3_HDD1_ROOT`, `PS3_GAMEDATA_ROOT`, and `PS3_DEV_FLASH` can override the launch mounts. The default VFS root is the installed title directory, HDD0 is `game/dev_hdd0`, and the user-data root is `game`, so saves and writable caches stay in the ignored dump tree. The runner reads `PARAM.SFO`, checks the configured title ID, and supplies the installed HDD boot path. Override metadata with `--paramsfo /path/to/PARAM.SFO` or `PS3_PARAM_SFO`.

## Prepare, build, and run

From the repository root with the local dump in place:

```bash
# Analyze the executable and generate PPU C/C++ locally.
python scripts/port.py prepare

# Configure the native build and discover/lift embedded SPU programs.
python scripts/port.py configure

# Compile the experimental port.
python scripts/port.py build --jobs 2

# Launch using the local game dump and writable mounts.
python scripts/port.py run
```

For analysis without translation, use `python scripts/port.py prepare --analyze-only`. Linux configuration enables Vulkan by default; use `python scripts/port.py configure --backend null` for headless diagnostics. Extra CMake options can follow `--`, for example `python scripts/port.py configure -- -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++` when using a fresh build directory. Run each subcommand with `--help` for path and tool overrides.

The build compiles the local generated code and links ps3recomp from source. It does not download a game, decrypt an executable, obtain keys, or implement the missing game behavior automatically. A successful build is an intermediate porting milestone.

## Vulkan rendering verification

Keep the graphics build separate from the null build used for CPU and filesystem diagnosis:

```bash
python scripts/port.py configure --backend vulkan --build-dir build-vulkan
python scripts/port.py build --build-dir build-vulkan --jobs 4
python scripts/port.py run --build-dir build-vulkan
```

Normal Vulkan launches enable `PS3RECOMP_VK_WINDOW=1` and `PS3RECOMP_VK_GUEST_PROGRAMS=1` unless explicitly overridden. At the pinned SDK revision these otherwise default to an offscreen target and fixed-function rendering. The port rejects unavailable requested window/guest-program support with exit status 87, and CMake rejects a Vulkan configuration without glslang translation. Explicitly setting either variable to `0` remains available for diagnostics but cannot pass the rendering verifier.

```bash
# Game-free glslang translation and shader-cache regression checks.
python scripts/test_vulkan_shaders.py

# Actual purchased game, real display and Vulkan device; bounded session.
python scripts/verify_vulkan_rendering.py --timeout 15

# Optional X11/XWayland window automation, requiring xdotool and libX11.
python scripts/verify_vulkan_rendering.py --check-window --timeout 20
```

The verifier requires the register-file draw engine, guest shader modules and pipelines, recorded draws, texture uploads, flipped surfaces, successful window presentation, non-clear pixels, and no observed strict CPU execution fallback. It saves `startup.log`, exit metadata, `verification.json`, `frame.ppm`, and `frame.png` under ignored `analysis/vulkan/verification/`. Pixel variation is an automated rendering check, not recognition of a title screen; inspect the PNG visually. The window variant requests a resize, records actual geometry and swapchain sizes, then sends a standard X11 close request and requires a normal exit. A tiling compositor may keep its chosen geometry; swapchain resize evidence includes compositor sizing at startup.

The local default Wayland bounded run and X11 window-control run pass on the RTX 3060. The captured frame shows a recognizable 1280×720 Tank Battles title screen with the press-X prompt. The renderer translated both shader stages, uploaded the title textures, and recreated the swapchain after compositor resizing. The X11 close test ended normally. No menu button was pressed during acceptance. Existing asset caches were used, audio used SDL's dummy driver, and no cold-cache startup or RPCS3 visual comparison was established by this check. The shader suite passes 28 assertions, including malformed input and damaged cache handling.

A subsequent user session reports working gamepad input and audible sound, substantial visual glitches, and slow rendering. Enabling the SDK's experimental `VK_DRAW_BATCH=1` seemed slower in that session; this is an observation without measured frame timing, and batching remains disabled by default. Rendering correctness and performance beyond the title screen are unresolved.

Vulkan diagnostics join the timeout snapshot as bounded counters and metadata. `window-present` counts successful/suboptimal `vkQueuePresentKHR` results, while `guest-draw` counts recorded draw calls; the framebuffer readback supplies separate pixel evidence. Generated backend instrumentation and requested-feature guards use the same reviewable source-copy workflow as the strict execution hooks, without editing the pinned SDK checkout or generated game code. Backend captures and shader dumps contain game-derived data and must remain in ignored local directories.

## Bounded boot diagnostics

Run a bounded session and keep combined stdout/stderr plus machine-readable exit metadata in the ignored analysis directory:

```bash
python scripts/port.py run --timeout 30 --log analysis/boot/session.log
```

The sidecar is `analysis/boot/session.log.result.json`. A timeout returns launcher status 124 while preserving the actual child return code and termination signal in the sidecar. Ordinary exits retain their exit status; signal exits use the shell convention of 128 plus the signal number. Without `--timeout`, the session is unbounded. Without `--log`, output goes to the terminal. POSIX timeout cleanup terminates the session's process group, including descendants.

On Linux, a timeout first requests a diagnostic snapshot with SIGUSR1. The runner reports startup milestones and each recorded guest thread's last observed HLE NID, CIA, and LR. These are snapshots at HLE boundaries, not continuously sampled guest PCs, and may be stale inside long guest loops. Fatal-signal and ELF-load failures also produce diagnostics. Snapshot handling uses lock-free atomic records and signal-safe writes. Linux-only port dispatch checks Tank Battles overrides before the shared handler and routes overrides through the SDK's ABI guard using private NID registrations. Register overrides in `stubs.cpp` before guest dispatch; the hook runs after shared runtime initialization.

The smoke test also checks an intentionally stalled runner, startup-failure reporting, and an override that must preserve the caller TOC. These fixtures are compiled only with `TANK_BATTLES_DIAGNOSTIC_TESTS=ON` in the synthetic build; the normal game build defaults to OFF. Run `python scripts/test_boot_session.py` for output, exit-status, and timeout process-group cleanup checks.

## Synthetic boot check

```bash
python scripts/smoke_test.py
```

This builds the port's actual runner against ps3recomp's synthetic PPU program in `build-smoke/`, using the headless renderer on Linux. It checks memory loading, HLE calls, guest threads, callbacks, frame presentation, and exit-status propagation. It requires no game files and does not establish Tank Battles compatibility. The synthetic guest passes status 39 to the runtime; its verifier checks that status and returns zero with `[smoke] PASS` on success.

## Import audit and startup triage

After building the normal executable, generate an offline-profile import report:

```bash
python scripts/audit_imports.py
python scripts/triage_boot.py --timeout 3
```

The audit loads the local ELF and initializes HLE registrations without dispatching guest code. On Linux it captures generic, context, fast, and port-override registrations, and probes the runtime's actual NID lookup. Results go to `analysis/step4/import-audit.json`. Optional host-network entries are distinguished from missing registrations. Handler presence, unresolved names, and known module limitations are separate fields; registration is not proof of compatibility. Capture is opt-in and stops before guest dispatch.

The triage tool retains the original Step 4 profiles: repeated configured-root runs, a launch-only corrected-root profile, and the documented unresolved-import failure mode. It saves bounded logs, exit metadata, and observations under `analysis/step4/`. The current configuration already corrects the asset root, so those root profiles may now be equivalent. At the pinned SDK revision, the shared missing-table dispatcher returns success directly despite `PS3_HLE_UNRESOLVED=fail`. The native strict policy now rejects reached missing registrations, and the port overrides purchase verification with the local entitlement bridge below; its earlier unresolved-success result remains historical diagnostic evidence.

Set `TB_TRACE_NID=0x2ECD48ED` when launching to log that import's entry and return values, capped at 32 calls. The native audit uses startup-only `TB_HLE_INVENTORY`, `TB_IMPORT_PROBE`, and `TB_HLE_AUDIT_ONLY` hooks; the Python tool manages them. Run `python scripts/smoke_test.py` followed by `python scripts/test_import_audit.py` to verify the inventory against synthetic generic/context/fast/override, inactive optional-network, and unknown-NID cases.

## Asset IO verification

On Linux, the port retains the SDK's `sceNpDrmIsAvailable` and `sceNpDrmIsAvailable2` handlers so the running game can register its own asset klicensee. After availability succeeds, the port maps the resulting plaintext `.edat.dec` cache into the generic C filesystem layer, whose default mappings and reads otherwise bypass the configured mounts and EDAT resolver. The PPU HLE and raw syscall layers already use that resolver. No private key is embedded in the port. RPCS3's `.unedat` suffix is not automatically discovered or used as a runtime filename.

For local verification, first independently decrypt your four asset containers with your own RPCS3 installation and place its `assets_*.edat.unedat` outputs under `game/reference-rpcs3/assets/`. RPCS3's [command-line parser](https://github.com/RPCS3/rpcs3/blob/master/rpcs3/rpcs3.cpp) supports `--headless --decrypt FILE`, and its [decrypt implementation](https://github.com/RPCS3/rpcs3/blob/master/rpcs3/Crypto/decrypt_binaries.cpp) handles EDAT output. Use your own asset klicensee when RPCS3 requests it. Keep reference files and raw RPCS3 logs local.

```bash
python scripts/verify_assets.py --key-log /path/to/your/local/RPCS3.log
```

This opt-in probe reads the asset key transiently from that local log and sends it to the native runner through stdin. It does not dispatch game code. It checks the game's availability handlers and open, size, seek, read, EOF, and close behavior in all three filesystem layers; compares full runtime plaintext hashes with the independent references; verifies cache reuse; and creates then removes unique test files under the configured HDD/cache roots. Reports are saved under ignored `analysis/step5/`. The SDK currently decides cache freshness by modification time, so a newer manually damaged cache must be removed before retrying; cache reuse alone is not an integrity check.

Game-free regression checks cover missing or mismatched metadata, wrong-key rejection, initial decryption, stale-cache regeneration, cache reuse, and writable mount consistency:

```bash
python scripts/smoke_test.py
python scripts/test_content_paths.py
python external/ps3recomp/tools/test_edat_guest_klicensee.py
```

The local NPUB30154 check matches all four asset containers against independent RPCS3 plaintext. Normal startup now selects the correct HDD title and opens those containers, and now passes the original raw SPU mailbox wait after the main-stack correction; the Vulkan build now reaches the title screen with existing asset caches. Asset IO verification does not establish completed resource initialization or playable boot.

## Purchased full-game unlock

The base NPUB30154 executable identifies itself as a trial. The Linux port verifies the purchased C00 unlock through `sceNpDrmVerifyUpgradeLicense` instead of accepting the SDK's unresolved-import success fallback. Put your own files in this ignored directory:

```text
game/dev_hdd0/home/00000001/exdata/
├── UP2006-NPUB30154_00-TANKBATTLES00KEY.edat
└── UP2006-NPUB30154_00-TANKBATTLES00KEY.rap
```

Prepare the SDK's local `.edat.rifkey` sidecar from your purchased RAP:

```bash
python scripts/prepare_entitlement.py --rpcs3-source ../rpcs3
```

Preparation uses PyCryptodome from the existing Python environment and public format tables from an optional local [RPCS3 source checkout](https://github.com/RPCS3/rpcs3). The checkout is needed only for preparation; the native runner does not invoke RPCS3 or Python for license checks. The script writes the derived key locally with mode 0600, prints no key bytes, and refuses to replace a different existing sidecar. No title-specific or purchaser key is compiled into the port. Public platform format constants already exist in the SDK and the EDAT verifier; these do not grant ownership.

At runtime, the bridge looks only in the configured HDD0 root's `home/00000001/exdata` profile. It validates this title's observed C00 format, the outer content ID, NPD title and developer hashes, the encrypted data block, and the decrypted `GOMA` payload's content ID. It uses a private instance of the pinned SDK codec with a unique transient plaintext file that is removed after verification. Existing `.dec`/`.unedat` caches and ambient `PS3_RIF_KEY` values cannot satisfy this check. The implementation supports the observed 352-byte version-1/type-1/license-2 unlock, rather than every PS3 entitlement format.

Missing, invalid, or unrelated entitlements return `SCE_NP_DRM_ERROR_LICENSE_NOT_FOUND` (`0x80029521`); a null or unterminated argument returns `SCE_NP_DRM_ERROR_INVALID_PARAM` (`0x80029502`). Offline `sceNpDrmExecuteGamePurchase` returns `GAME_ERR_NOT_XMBBUY_CONTENT` (`0x80028F81`) and performs no purchase or guest restart. The missing-unlock worker retains the game's own sign-in poll with a 10 ms sleep; the offline profile does not fabricate signed-in state to terminate that worker.

```bash
python scripts/smoke_test.py
python scripts/test_entitlement.py
python scripts/verify_entitlement.py --timeout 3
```

The 13 game-free entitlement tests cover valid synthetic input, missing files, wrong keys, corrupt hashes/data, unrelated content IDs, bad payloads, invalid guest arguments, and offline purchase results. The Linux verification script needs an unstripped diagnostic runner, `nm`, and parent access to its child's `/proc` files. It checks the known local executable's instruction layout, reads only a guest pointer chain and three entitlement-state bytes, and compares bounded purchased/missing-unlock sessions using a separate ignored HDD fixture. It does not dump guest memory or remove your purchased files. Results go to `analysis/step6/entitlement-verification.json`.

The actual game's trial flag is clear with the purchased unlock and set without it, matching the traced return-value gate and RPCS3's purchased reference. The purchased verification thread completes; the missing-unlock path remains in its offline-status loop. The main-stack correction resolves the original SPU mailbox wait; visible full-game content and mission access have not yet been revalidated in the native port.

## Offline PSN profile

On Linux, `offline_network.cpp` implements the local/offline behavior for this title's imported PSN interfaces. Startup clears `PS3_NET_ONLINE` and `PSNR_SERVER` before initializing HLE, so those environment settings cannot enable host matchmaking in this port. Online multiplayer is outside the initial playable milestone. The pinned shared SDK is unchanged.

| Interface | Offline behavior |
| --- | --- |
| NP manager | Reports OFFLINE; callback registration does not invent a sign-in event. |
| NetCtl | An initialized state query succeeds with Disconnected. Network dialogs deliver LOADED, FINISHED, and UNLOADED through sysutil; the result is cancelled. |
| Score and TUS | Local title contexts have valid releasable handles. Transaction creation fails without login; no request IDs or successful empty leaderboard results are fabricated. |
| Matching2 | Local contexts and registrations succeed. An accepted context start delivers StartOver with NOT_NP_SIGN_IN; room requests fail immediately. |
| Avc2 | Initializes the 132-byte parameter structure. Accepted voice loading delivers LOAD_FAILED with NOT_SUPPORTED; streaming does not report success. |
| Invitations and signaling | Requests return explicit offline/unreachable errors without creating pending online operations. |

The bridge holds deferred completions until `cellSysutilCheckCallback`, then uses the SDK's existing guest callback queue. Abort, context destruction, module termination, and NP termination clear the corresponding pending work. Failed requests leave output buffers and request-ID fields untouched. The imported callbacks remain guest callbacks with their original argument layout. Core initialization and local DRM checks remain usable without signing in.

Run the native interface tests after building the synthetic runner:

```bash
python scripts/smoke_test.py
python scripts/test_offline_network.py
```

The tests run the actual HLE dispatcher and callback queue with a child-only Linux seccomp filter that denies new sockets. Both normal and online-configured environment profiles pass the same checks, covering deferred delivery, callback arguments, cancellation, context cleanup, output boundaries, and rejection of unavailable services. The runner's opt-in `TB_OFFLINE_PROBE=1` performs these checks before guest dispatch and requires no private keys or game assets when used with the synthetic ELF. The synthetic build has no SPU program; actual game startup still requires separate validation.

To reproduce the import and startup trace with your local game:

```bash
PS3_HLE_TRACE=1000 python scripts/port.py run --timeout 3 --log analysis/step7/game-boot.log
python scripts/audit_imports.py --output analysis/step7/import-audit.json
python scripts/trace_offline_imports.py
```

The bounded launch returns 124 when its diagnostic cutoff is reached; the original raw-SPU wait has been resolved by the main-stack correction. The historical Step 7 trace records 94 related imports and 239 static call sites for the local NPUB30154 dump, translating private port aliases through captured HLE registrations. That pre-fix trace reached NP, NP2, and TUS initialization plus local DRM checks; later NetCtl, matchmaking, and voice calls have been exercised by the native probe but have not yet been reached in normal startup. Static call sites do not establish that a path executed. The Vulkan title screen is now verified. Menu navigation and offline mission selection remain unverified.

## Main-stack placement and SPU startup

The runner places its 1 MiB main-thread stack in the PS3 stack region, with top `0xD0100000`, as defined in `guest_layout.h`. Tank Battles classifies certain transfers using the stack address's high nibble. The previous segment-zero stack selected a CPU-copy path for a null synchronization request, leaving the PPU waiting for an SPU command that had never been sent. The corrected layout sends the real synchronization command and receives its SPU acknowledgement. This changes the host runner's guest-memory layout without changing game instructions or synthesizing mailbox replies.

Run `python scripts/test_guest_layout.py` for the original routing regression. Use `python scripts/compare_asset_cache_boots.py --prefix asset-cache-fresh` to compare isolated cold/warm asset-cache boots with fresh fixture names. The isolated cold/warm startup evidence is under `analysis/step8/`; three corrected boots pass the old mailbox wait, and the full cold-cache run reproduces all four independent asset hashes. Step 9 implements the subsequently reached text-conversion import and verifies bounded startup under strict execution; the Vulkan title-screen check below now verifies visible startup with existing asset caches. This is not yet a playable port. The pinned SDK's modification-time cache policy can reuse partial caches after interrupted decryption; incomplete generated caches must be removed or quarantined before repeating that diagnostic, without changing purchased originals.

## Strict static execution diagnostics

The Linux runner defaults to strict guest execution. A reached missing HLE import, unresolved/null PPU target, unverified PPU recovery, SPU interpreter entry, or untranslated SPU target stops the process with status 86 and a bounded diagnostic report. This also intercepts SDK branches that otherwise return success or silently skip invalid calls. `TB_EXECUTION_MODE=diagnostic` explicitly allows those SDK fallbacks for investigation while recording them; it does not establish static compatibility.

`scripts/instrument_sdk.py` generates instrumented copies of seven SDK source files under the selected build directory. CMake builds those copies with the normal SDK include paths; the pinned submodule remains unchanged. The generator requires unique matching source anchors and records original/output hashes in `instrumented_sdk/manifest.json`. Original source changes trigger reconfiguration. No generated game translation is edited. Logs contain bounded target observations, counters, last image/address pairs, and a bounded image/overlay inventory. Counter and last-target writes use lock-free atomics; timeout reports use signal-safe output. A full image-inventory buffer terminates explicitly rather than silently dropping coverage.

```bash
python scripts/smoke_test.py
python scripts/test_execution_policy.py
python scripts/verify_static_startup.py --timeout 6
```

The eight policy/conversion tests exercise actual native missing-import, missing-target, full-interpreter, and untranslated-SPU paths, plus diagnostic mode, text conversion, and reproducible source generation. The startup verifier records its limited coverage in `analysis/static-execution/verification.json`: the run must reach its diagnostic timeout, report no observed execution failures/fallbacks, and match the observed image IDs against the generated static inventory. Passing this check proves only the recorded startup interval; other languages, later images, and campaign-wide coverage still require their planned checks. The separate Vulkan check below verifies the observed graphical startup interval.

The newly reached NID `0x931FF25A` is `L10nConvertStr`. `text_conversion.cpp` implements the observed ISO-8859-1 to UTF-8 path and common UTF-8/UTF-16BE/UTF-32BE/UCS encodings through the host converter. Lengths are guest big-endian byte counts; length queries, partial output, invalid sequences, and unsupported converters return explicit results. Unsupported code pages remain unsupported rather than returning success with unchanged output. The port logs conversion metadata without string contents.

For SPU pipeline checks, `python scripts/test_spu_pipeline.py` runs the pinned SDK suite with a fixture-only adapter supplying the two missing disabled local-store-watch symbols. The untouched upstream test currently fails three link cases for those symbols; the adapter leaves the SDK runtime blocks and test assertions unchanged, and all 15 tests pass. Other targeted dispatch tests run directly from the untouched SDK.

## Credits and contributions

This port uses [ps3recomp by sp00nznet / Ned Heller](https://github.com/sp00nznet/ps3recomp). The CMake scaffold and runner are adapted from its project template; upstream attribution is preserved in [LICENSE](LICENSE). ps3recomp and its bundled components have their own licensing notices.

Contribute tooling, configuration, documentation, and original runtime fixes. Do not submit game files, firmware, licenses, keys, memory dumps, or generated game-code translations in commits, issues, or attachments. Review staged files before committing.

This is an unofficial project and is not affiliated with or endorsed by Gameloft or Sony. Tank Battles and its original content belong to their respective rights holders.
