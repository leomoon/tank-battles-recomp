# Tank Battles Recomp

An experimental PC port scaffold for Tank Battles on PlayStation 3 (NPUB30154), built with [ps3recomp](https://github.com/sp00nznet/ps3recomp).

**THIS REPOSITORY DOES NOT CONTAIN THE GAME.**

To recompile or run the game, you must provide your own decrypted dump of a legitimately purchased copy of Tank Battles for PlayStation 3. Cloning this repository does not supply the game or a license to it. No game executables, assets, decrypted dumps, firmware, entitlement files, keys, or generated translations of the game code are included. Translated code is generated locally from your own dump.

## Status

The repository has a pinned ps3recomp dependency, a native runner, configuration, and scripts for analysis, translation, build, and launch. It is not a playable port yet. Missing system interfaces and game-specific behavior still need to be implemented and tested. The synthetic smoke test checks the runner independently of Tank Battles.

Initial static analysis of NPUB30154 found approximately 17,300 candidate PPU functions, 274 imports across 19 libraries, and one embedded SPU ELF of 3,136 bytes. Outstanding areas include graphics, audio, media-player interfaces, full-game entitlement handling, save data, and offline handling of PSN features. Scanning the main executable does not rule out additional code loaded from assets or system libraries.

## Structure

```text
tank-battles-recomp/
├── CMakeLists.txt          # Native executable and runtime integration
├── config.toml             # Paths and translation settings used by scripts/port.py
├── main.cpp                # PPU runner, graphics backend, and frame clock
├── stubs.cpp               # Tank Battles-specific runtime hooks
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
- Vulkan headers and a working Vulkan driver for Linux graphics. Guest shader translation also needs the shader tools described in the upstream [build guide](https://github.com/sp00nznet/ps3recomp/blob/master/docs/BUILDING.md). The null backend is available for headless diagnostics.

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

Copy the entire installed `NPUB30154` directory from your own dump, including its other files. RPCS3 can install your PKG and export `EBOOT.elf` through Tools → Decrypt PS3 Binaries. A decrypted executable alone is not the complete game. Protected EDAT assets must also be readable by the port through runtime support or a separately prepared decrypted asset dump. Full-game entitlement files from your purchased copy remain local; entitlement support is still unfinished.

The scripts read `config.toml` and look for `game/dev_hdd0/game/NPUB30154/USRDIR/EBOOT.elf` by default. Paths are relative to the repository root. To analyze a decrypted executable elsewhere, pass `--elf /path/to/EBOOT.elf`; this does not copy the executable or its assets. Set matching filesystem paths in the configuration before trying to run the game. `PS3_VFS_ROOT`, `PS3_HDD0_ROOT`, and `PS3_HDD1_ROOT` can override the launch mounts.

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

## Credits and contributions

This port uses [ps3recomp by sp00nznet / Ned Heller](https://github.com/sp00nznet/ps3recomp). The CMake scaffold and runner are adapted from its project template; upstream attribution is preserved in [LICENSE](LICENSE). ps3recomp and its bundled components have their own licensing notices.

Contribute tooling, configuration, documentation, and original runtime fixes. Do not submit game files, firmware, licenses, keys, memory dumps, or generated game-code translations in commits, issues, or attachments. Review staged files before committing.

This is an unofficial project and is not affiliated with or endorsed by Gameloft or Sony. Tank Battles and its original content belong to their respective rights holders.
