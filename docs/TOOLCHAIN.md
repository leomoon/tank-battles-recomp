# Redistributable build toolchain for the standalone builders

The standalone builders compile each player's private executable on the player's own machine, from their purchased inputs, without installed development tools. That needs a compiler, linker, C/C++ runtime and build tools that may legally ship inside `Builder.exe` and `Builder.AppImage`. The Microsoft Build Tools and Windows SDK used by the ClangCL route may not be redistributed, so the builders use a different, freely redistributable stack. The ClangCL/vcpkg route in [RUNTIME_BUNDLE.md](RUNTIME_BUNDLE.md) remains the developer and CI path for MSVC-ABI Windows builds.

## The stack

| Component | Pinned | Role | Licence |
| --- | --- | --- | --- |
| Zig 0.17.1 | linux and windows host archives | The only compiler and linker: clang/lld 22.1.8, libc++, glibc 2.31 stubs, mingw-w64 | MIT; bundled LLVM Apache-2.0 with LLVM exception; mingw-w64 ZPL/public domain; glibc headers LGPL |
| CMake 4.5.1 | official binaries | Build configuration | BSD-3-Clause |
| Ninja 1.13.2 | official binaries | Build execution | Apache-2.0 |
| CPython 3.13.18 (python-build-standalone 20261110) | install_only_stripped | Runs setup, the PPU/SPU lifter and title patches | PSF-2.0 plus bundled notices |
| capstone, pycryptodome, construct, tabulate, tqdm, colorama | wheels per host | Lifter dependencies from `external/ps3recomp/requirements.txt` | BSD-3, BSD/Public Domain, MIT, MIT, MPL-2.0/MIT, BSD-3 |

Every URL and SHA-256 is in [`ci/toolchain/manifest.json`](../ci/toolchain/manifest.json). Most digests are the publisher's own (Zig's index, GitHub release asset digests, PyPI). The Khronos tag archives and libiconv publish no digest; theirs were recorded when pinned and are marked so in the manifest.

Targets: x86_64 Linux with a glibc 2.31 baseline (Ubuntu 20.04, Debian 11, Fedora 32 and newer), and x86_64 Windows 10 or newer with the mingw-w64 GNU ABI and the Universal CRT. Windows players import only Windows system DLLs (Universal CRT, kernel/user/GDI, Winsock, D3D12, DXGI, D3DCompiler_47). No libc++ or mingw runtime DLL is shipped or needed. Linux players link `libc`, `libm`, `libpthread` and `libdl`, and load X11, PulseAudio, ALSA, udev and the Vulkan driver at run time.

## Runtime dependencies built with the same compiler

`scripts/build_runtime_deps.py` builds these from the pinned sources as static libraries, with the same Zig, for each target. The runtime bundle carries the resulting libraries, headers and relocatable CMake packages, so players link against nothing from the machine.

| Library | Version | Licence | Notes |
| --- | --- | --- | --- |
| SDL2 | 2.32.10 | Zlib | Linux: X11 (XInput2, XRandR), PulseAudio, ALSA, udev, loaded at run time. Native Wayland is not built; Wayland desktops use XWayland. |
| Vulkan-Headers, SPIRV-Headers, SPIRV-Tools, glslang | vulkan-sdk-1.4.363.0 | Apache-2.0, MIT, BSD-3 | glslang with HLSL legalization through SPIRV-Tools-opt. SPIRV-Tools' shared library is removed after install. |
| OpenSSL | 3.6.5 | Apache-2.0 | Linux trust store at `/etc/ssl` |
| curl | 8.23.0 | curl (MIT-style) | OpenSSL backend; TLS sockets only |
| GNU libiconv | 1.18 | LGPL-2.1-or-later | Windows only |

Building the dependencies needs Perl and make on the Linux CI machine for OpenSSL and libiconv, and the X11, PulseAudio, ALSA and udev development headers for SDL2. These are build-machine requirements only; nothing from them ships.

## Redistribution obligations

- Ship each component's licence text and notices with the builder (the toolchain's own `LICENSE` files, CPython's notices, and the `licences/` directory of the runtime bundle).
- **libiconv is LGPL and is linked statically into Windows players.** The builder must ship the exact libiconv 1.18 source archive (its SHA-256 is in the manifest) with its licence. Players are built locally from objects and the static library, so users can always relink against a modified libiconv, which satisfies the LGPL's relinking requirement.
- Zig's glibc headers and stubs are LGPL; using them to compile does not place the player under the LGPL. The Linux player links the system glibc dynamically.
- No purchased content, keys, generated PPU/SPU code, saves or RPCN credentials are part of any toolchain or bundle payload.

## Size

Unpacked: the Linux host toolchain is about 750 MB (Zig 330 MB, CMake 150 MB, Python 160 MB, wheels and Ninja the rest). The Linux runtime bundle is about 216 MB, mostly SPIRV-Tools, glslang and OpenSSL static libraries plus the game-free test tools. Both compress roughly four to one. A full player build additionally needs several gigabytes of workspace for the generated translation units and object files.

## Reproduce

```sh
python scripts/toolchain.py fetch --host linux --cache ~/.cache/tb-toolchain --sources
python scripts/toolchain.py stage --host linux --cache ~/.cache/tb-toolchain --dest /opt/tb-toolchain
python scripts/build_runtime_deps.py --toolchain /opt/tb-toolchain --cache ~/.cache/tb-toolchain --target linux --prefix /opt/tb-deps/linux
cmake -S . -B build-zig/linux -G Ninja -DCMAKE_MAKE_PROGRAM=/opt/tb-toolchain/ninja \
  -DCMAKE_TOOLCHAIN_FILE=/opt/tb-toolchain/cmake/zig-linux.cmake \
  -DCMAKE_PREFIX_PATH=/opt/tb-deps/linux -DCMAKE_FIND_ROOT_PATH=/opt/tb-deps/linux \
  -DCMAKE_BUILD_TYPE=Release -DTB_BUILD_RUNTIME_BUNDLE=ON -DPS3RECOMP_RSX_VULKAN=ON \
  -DPython3_EXECUTABLE=/opt/tb-toolchain/python/bin/python3
cmake --build build-zig/linux && ctest --test-dir build-zig/linux
cmake --build build-zig/linux --target runtime_bundle
python scripts/verify_runtime_consumer.py --bundle build-zig/linux/runtime-bundle --output consumer-check \
  --source "$(git rev-parse HEAD)" --toolchain /opt/tb-toolchain
```

A real player links the same way from a relocated bundle and the user's local translations:

```sh
cmake -S cmake/player-consumer -B build-zig/player-linux -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_MAKE_PROGRAM=/opt/tb-toolchain/ninja -DCMAKE_TOOLCHAIN_FILE=/opt/tb-toolchain/cmake/zig-linux.cmake \
  -DPython3_EXECUTABLE=/opt/tb-toolchain/python/bin/python3 -DTB_RUNTIME_ROOT=/path/to/runtime-bundle \
  -DTB_RUNTIME_EXPECTED_SOURCE="$(git rev-parse HEAD)" -DRECOMP_DIR="$PWD/recompiled" -DTB_PORT_ROOT="$PWD"
cmake --build build-zig/player-linux
```

On the Linux reference host the Zig-built player reached audio initialization in each of 5 boots, as did the GCC player, and measured 50.6 FPS against GCC's 50.6 FPS in the same profiled 20-second Battle run.

Use `--target windows` and `zig-windows.cmake` for the Windows bundle; both are cross-built on Linux. `verify_runtime_consumer.py --toolchain` builds the synthetic title with only the staged CMake, Ninja, Python and Zig, with `PATH` limited to them and pkg-config disabled, and runs it when the bundle's target matches the host (otherwise it confirms the cross-link).

## Installed builder resources and the workspace

`scripts/package_resources.py assemble` lays out what a builder installs: the setup scripts that `port.py` needs (followed from its imports), the lifter and SDK headers from `external/ps3recomp`, the player CMake project, `config.toml`, the verified runtime bundle in `runtime/` and the staged toolchain in `toolchain/`. `package.json` records the source commit, runtime ABI and toolchain identity, plus the SHA-256 of every resource file. The audit refuses purchased formats, keys, saves, generated title code and object files.

When `package.json` is present, `port.py` runs in installed mode:

- The resources are read-only and every file setup writes goes to the workspace (`--workspace`, `TB_WORKSPACE`, or a per-user default: `~/.local/share/tank-battles-recomp/workspace`, `%LOCALAPPDATA%\TankBattlesRecomp\workspace`). Relative `config.toml` paths resolve in the workspace; `paths.sdk` resolves in the resources.
- The source identity comes from `package.json`, not Git, and modified resources are rejected before any work starts.
- `configure` and `build` use the packaged runtime and toolchain on both OSes, with `PATH` limited to the staged tools and Zig's cache inside the workspace.

A checkout keeps the existing behaviour and can select the same route with `--runtime-bundle DIR --toolchain DIR`, now on Linux as well as Windows. Translation signatures no longer include absolute paths, so moving the resources or workspace reuses translations; a build directory configured from moved resources is reconfigured automatically. `scripts/test_packaged_route.py` checks a read-only, relocated package with a workspace path containing spaces and non-ASCII characters, no Git, no network and only staged tools: first build, no-op reuse, interrupted-link resume, relocation, tampering and source-mismatch rejection.

## Setup engine

`scripts/setup_engine.py` is the single implementation behind `port.py setup` and the builder interface. It runs the existing pipeline as stages: `inputs` (purchased files, builder files, workspace space, job limits), `import`, `translate`, `configure` and `compile`. With `--json` it writes one event per line (`stage`, `progress` from the lifter and Ninja, `log`, and a final `result` with the player path or an error plus the action to take). Every run also writes `logs/setup-*.log` in the workspace.

- Damaged builder files and too little disk space are reported in `inputs`, before an hour of work. The space estimate credits existing translations, builds and caches.
- Parallelism is bounded by available memory: about 0.7 GB per compile job and 1.5 GB per translation worker, with 1.5 GB kept free. The reference title's largest translation unit peaks near 0.3 GB in Zig/clang, and two lifter workers near 1.9 GB in total. `--jobs` can only lower the result.
- Cancelling (SIGINT/SIGTERM, CTRL_BREAK on Windows, or `cancel` or end of input with `--stdin-control`) stops the engine's own child process trees and nothing else. Completed stages are kept, so the next run resumes; a failed or cancelled stage never records completion.
- Failures become one sentence and an action: a full drive, out of memory, failed translation, configuration or compilation (details in the log), or the input problem found by the checks.

`scripts/test_setup_engine.py` covers job bounds, error classification and cancellation of a child and its grandchild without touching an unrelated process. With a bundle and toolchain it also runs the packaged pipeline on synthetic input without network: injected translation failure, cancel during configure, resume, and a no-op repeat.

## The movable player folder

With `--output DIR` (always, for an installed builder; default `~/Games/Tank Battles Recomp`), setup's final `assemble` stage creates or updates a self-contained folder:

| Path | Contents | Replaced by a rebuild |
| --- | --- | --- |
| `Play Tank Battles[.exe]` | Native launcher (`launcher/`), built with the player | yes |
| `player/TankBattlesRecomp[.exe]`, `player/manifest.json` | Your private executable and the list of files this folder's builds own, with SHA-256 | yes |
| `game/dev_hdd0/game/NPUB30154/`, `game/dev_hdd0/home/00000001/exdata/` | Imported content and your unlock | listed files only |
| `game/dev_hdd0/home/00000001/savedata/`, `game/settings.ini` | Saves and preferences (including F4 FXAA) | never |
| `online/`, `logs/` | Optional RPCN profiles (`online/active` names the selected one) and session logs | never |

The launcher sets the same paths `port.py run` does, relative to its own location, then starts the player offline unless `online/active` selects a profile (`--offline` overrides). The folder needs no Python, checkout, workspace or compiler and can be moved or copied. Translations, objects and toolchain caches stay in the workspace. Updates copy only changed files and stage everything before the first replacement. A non-empty folder that is not a player folder is refused before any work starts. `--migrate-saves-from OLD_GAME_DIR` copies saves and settings into a new folder without overwriting anything there.

## Online accounts in the launcher

`Play Tank Battles` opens a small window (SDL2 from the runtime bundle, built-in font) with **Play online as NAME** when signed in, **Play offline**, **Sign in** and **Create account**. Accounts belong to the RPCN server entered (default `rpcn.example.org:31313`) and are separate from PlayStation Network. Each saved account is `online/<name>@<host>_<port>.account`, so the same name on two servers is two accounts; `online/active` selects one. **Sign out** keeps it; **Forget** removes it and its remembered password.

- The launcher checks credentials by running the player's `--rpcn-check` / `--rpcn-create-account` and reading its one-word `[rpcn-result]`: `signed_in`, `created`, `wrong_password`, `unknown_user`, `name_taken`, `email_taken`, `token_required`, `already_signed_in`, `unreachable`, `certificate`, `protocol`, `invalid_profile`. Each word maps to a sentence and a retry or back action. When a server needs an email verification token, the sign-in screen asks for it.
- Passwords never appear on a command line or in a log. Each check and each online launch writes a temporary runtime profile readable only by you (mode 0600 in `$XDG_RUNTIME_DIR`, or an owner-only file on Windows) and erases it afterwards; leftovers from a crash are erased at the next start. **Remember me** stores the password in Windows Credential Manager. On Linux, where no keyring is available everywhere, it goes in the 0600 account file. Without **Remember me** the password lasts only until the launcher closes.
- Public servers use the system's trust store: Windows' native store, or the distribution's CA bundle on Linux, found in the usual Debian/Fedora/openSUSE/Arch locations. Hostnames are always verified. **Advanced** accepts a private server's certificate (path, paste or drag-and-drop); it is copied into `online/` so the folder stays movable. The server's private key is never requested.
- The same functions are scriptable, with the password read from standard input: `"Play Tank Battles" --account sign-in|create|status|sign-out|forget --server HOST:PORT --user NAME [--email E] [--token T] [--ca FILE] [--remember]`. `--play` starts online when the selected account's password is remembered, otherwise offline; `--offline` always starts offline.

`online/` is never part of the player manifest, so rebuilding preserves accounts, and the builder never copies them.

## The standalone builders

`scripts/builder_app.py` is the builder window (Tk from the staged Python). Choose the game package, the unlock EDAT and the decrypted `EBOOT.elf` (or, under Advanced, SELF keys or a RAP), and where to install; press **Build**. It runs the setup engine as a child process and shows each stage, progress from the lifter and Ninja, the log and its file, and a sentence plus action when something fails. **Cancel** stops only the build's processes, and the next **Build** continues from the finished stages. When done it offers **Play** and **Open folder**, and says where saves and settings live; rebuilding into the same folder keeps them. Choices are remembered in `~/.config/tank-battles-recomp/builder.json` (`%APPDATA%\TankBattlesRecomp\builder.json`).

`scripts/package_builder.py` turns the resources into the two downloads:

| | `Builder.AppImage` | `Builder.exe` |
| --- | --- | --- |
| Contents | Resources for Linux (Linux-host toolchain, Linux runtime bundle), `NOTICES.txt` | Resources for Windows (Windows-host toolchain, Windows runtime bundle), `NOTICES.txt`, `sources/libiconv-1.18.tar.gz` |
| Start | AppImage type2 runtime 20251108 (pinned in the toolchain manifest) mounts a zstd squashfs and runs `AppRun`, which starts the staged Python with the builder window | A Zig-built stub verifies the appended zip's SHA-256, unpacks it once with Windows' `tar.exe` into `%LOCALAPPDATA%\TankBattlesRecomp\builder\<digest>` and starts `pythonw.exe` with the builder window |
| Without the default route | `./Builder.AppImage --appimage-extract`, then `squashfs-root/AppRun` (systems without FUSE) | `Builder.exe --extract FOLDER`, then `FOLDER\toolchain\python\pythonw.exe FOLDER\scripts\builder_app.py` |
| Size | about 300 MB | about 250 MB |

```sh
python scripts/package_builder.py resources --target linux --runtime-bundle build-zig/linux/runtime-bundle \
  --toolchain /opt/tb-toolchain --cache ~/.cache/tb-toolchain --output out/linux-resources
python scripts/package_builder.py appdir --resources out/linux-resources --output out/AppDir
python scripts/package_builder.py appimage --appdir out/AppDir --runtime ~/.cache/tb-toolchain/<digest> --output Builder.AppImage
python scripts/package_builder.py resources --target windows ... --toolchain /opt/tb-toolchain-windows --output out/windows-resources
python scripts/package_builder.py stub --toolchain /opt/tb-toolchain --output out/BuilderStub.exe
python scripts/package_builder.py exe --resources out/windows-resources --stub out/BuilderStub.exe --output Builder.exe
```

Both are made on Linux. The Windows toolchain is staged there with `toolchain.py stage --host windows`. Every extracted path stays under Windows' 260-character limit with room for a long user name.

## Continuous integration and release candidates

`.github/workflows/standalone-builders.yml` (manual dispatch or any tag) builds both builders from one revision. Everything comes from `ci/toolchain/manifest.json`, cached by its digest:

1. **build** (Ubuntu) stages the Linux- and Windows-host toolchains and builds the dependencies and both runtimes with Zig. It runs the Linux runtime's tests, links relocated synthetic consumers with only the staged tools, and runs the game-free contract suites. It then packages `Builder.AppImage` and `Builder.exe` and audits both complete payloads with `scripts/audit_builder.py`. Finally it writes `SHA256SUMS`, `provenance.json` (source, toolchain manifest digest, toolchain identity, runtime build and ABI per artifact) and the notices.
2. **linux-consumer** and **windows-consumer** start from a clean runner that downloads only the artifact. Each checks the checksum and relocates the builder into a path with spaces and a non-ASCII character. Linux strips the runner's compilers and tools; on Windows they are not on `PATH`. With the network blocked (a network namespace on Linux, firewall rules for every builder executable on Windows), each opens the builder window and runs `scripts/verify_builder.py` from inside the download. That script translates, compiles and assembles a synthetic player with the packaged engine, then starts it through the launcher. Both jobs also confirm that a modified or damaged download is refused.
3. **stage-release** (tags only, after both consumers pass) creates a draft GitHub release with the two builders, checksums, provenance and notices. Nothing is published automatically.

`scripts/audit_builder.py` inspects every file of an extracted builder or of `Builder.exe`'s payload, not only those `package.json` lists. It refuses:

- PlayStation packages, executables and licences; keys, saves, RPCN accounts and private keys;
- generated title code, recognised by thousands of distinct lifted-function symbols, including object names inside static archives;
- executables and objects outside the verified runtime and toolchain.

It also rejects modified resources, a mismatched source commit and tampered or unsafe `Builder.exe` payloads.
