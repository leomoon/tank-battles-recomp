# Tank Battles Recomp

A native Linux and Windows port of **Tank Battles for PS3 (NPUB30154)** using static recompilation. The game's PPU/SPU code is translated locally into C/C++ and compiled with a PS3 runtime and Vulkan renderer. Battle and Campaign are playable.

**This repository does not contain the game, its assets, purchased licenses, private keys or generated game code. You must provide your own legitimately purchased copy.**

## What you need

Put your original game files here:

```text
game/
├── input/
│   ├── tank-battles_NPUB30154.pkg
│   └── tank-battles_NPUB30154_key.edat
└── keys                  # Your scetool-format SELF key file
```

The tested original unlock EDAT needs no RAP. If your purchased unlock uses license type 2, also provide its `.rap` in `game/input/`. Everything under `game/` is ignored by Git.

For executable decryption, supply a scetool-format key file supporting NPDRM revision `0001`, including `NP_klic_free` and `NP_klic_key`. Alternatively, pass an existing local RPCS3 source checkout with `--rpcs3-source /path/to/rpcs3`. No keys or game files are downloaded, and no manual RPCS3 extraction/decryption is needed.

## Linux

### Build dependencies

Requires Python 3.11+ and a C17/C++20 compiler.

**Arch Linux / Omarchy:**

```bash
sudo pacman -S --needed base-devel git python python-pip cmake ninja pkgconf sdl2-compat vulkan-headers vulkan-icd-loader glslang spirv-tools curl
```

**Ubuntu 24.04+ / Debian 13+:**

```bash
sudo apt update
sudo apt install build-essential git python3 python3-venv python3-pip cmake ninja-build pkg-config libsdl2-dev libvulkan-dev glslang-dev glslang-tools spirv-tools libcurl4-openssl-dev
```

Also install your GPU's Vulkan driver. Arch uses [sdl2-compat](https://archlinux.org/packages/extra/x86_64/sdl2-compat/) for SDL2; Ubuntu's [glslang packages](https://packages.ubuntu.com/noble/glslang-dev) include the development libraries required by the build.

### Build and run

```bash
git clone https://github.com/leomoon/tank-battles-recomp.git
cd tank-battles-recomp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r external/ps3recomp/requirements.txt cmake ninja
```

Add your files as shown above, then:

```bash
python scripts/port.py setup --jobs 4
python scripts/port.py run
```

Setup extracts the package, decrypts the executable, prepares the unlock, translates the game and builds a stripped Release executable at `build-player/TankBattlesRecomp`. Asset caches are created at first launch, so that launch takes longer. Lower `--jobs` if compilation uses too much memory. The ps3recomp source is already included.

Use `setup --check` to report missing files before building. Setup resumes verified work after an interruption. A manually decrypted executable can be supplied with `--elf /path/to/EBOOT.elf`, or use your RPCS3 executable with `--rpcs3 /path/to/rpcs3`. Installed-dump and other options: [Setup reference](docs/SETUP.md).

Controllers are supported. Keyboard: **Z = Cross, X = Circle, arrows = movement, Enter = Start**. **F3** toggles the FPS overlay; **F11 / Alt+Enter** toggles fullscreen.

The overlay starts hidden. Window/fullscreen and overlay preferences are saved in `game/settings.ini`. Session logs go to `~/.local/state/tank-battles-recomp/logs/` (or `$XDG_STATE_HOME`); `run --console` prints to the terminal. Developer profiles and diagnostic switches: [Runtime reference](docs/RUNTIME.md).

### Keep a finished build

For your own use outside this source checkout, copy `build-player/TankBattlesRecomp` and `game/dev_hdd0/` into a new folder, then create an empty `game/dev_hdd1/` directory:

```text
TankBattles/
├── TankBattlesRecomp
└── game/
    ├── dev_hdd0/          # Installed game, unlock and saves; copy the whole folder
    │   └── game/NPUB30154/USRDIR/EBOOT.elf
    └── dev_hdd1/          # Writable cache
```

From that folder, launch:

```bash
./TankBattlesRecomp game/dev_hdd0/game/NPUB30154/USRDIR/EBOOT.elf
```

The executable still needs the ELF and game data. Python, the SDK, generated C/C++ sources, original PKG and SELF key file are not needed to play. The destination Linux system needs compatible SDL2, glslang/SPIRV-Tools and libcurl runtime libraries, the Vulkan loader and a GPU driver; this is not a self-contained AppImage. Saves are under `game/dev_hdd0/home/00000001/savedata/`.

This finished folder contains game code, assets and your license material. Keep it private.

## Windows

Windows 10 or 11 (x64) with a Vulkan GPU driver. Tested on an Intel Iris Pro 580 NUC, which reaches about 30 FPS; it is CPU-limited, see [performance notes](docs/OPTIMIZATION.md). Install the newest GPU driver available for your hardware.

### Build dependencies

- [Visual Studio Build Tools 2026](https://visualstudio.microsoft.com/downloads/) with **Desktop development with C++** and **C++ Clang Compiler for Windows** (clang-cl). CMake comes with it.
- [Python](https://www.python.org/downloads/) 3.11 or newer, and Git.

Run every command below in **Developer PowerShell for VS 2026**. If `clang-cl --version` is not found there, add the bundled LLVM tools for the session:

```powershell
$env:PATH = "$env:VCINSTALLDIR\Tools\Llvm\x64\bin;$env:PATH"
```

Then:

```powershell
git clone https://github.com/leomoon/tank-battles-recomp.git
cd tank-battles-recomp
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r external/ps3recomp/requirements.txt
```

### Runtime bundle

The player links a prebuilt, game-free *runtime bundle*: the PS3 runtime, Vulkan renderer and host services. It must come from the same commit as your checkout. Either:

- **Download it** from the repository's **Test and build Windows and Linux runtimes** GitHub Actions run for your tag's commit, and extract the Windows artifact to a folder such as `build-runtime\runtime-bundle`. This workflow runs only on tag pushes, in one job: Windows and Linux tests first, then Windows and Linux bundle builds. Linux builds and tests use Ubuntu 24.04 through WSL on the Windows runner. Each platform has its own downloadable artifact. Your clang-cl version must match the bundle compiler (currently Clang 22.1.3); setup refuses a mismatch.
- **Or build it** with the same pinned [vcpkg](https://github.com/microsoft/vcpkg) dependencies as CI:

  ```powershell
  git clone https://github.com/microsoft/vcpkg C:\vcpkg
  git -C C:\vcpkg checkout 0df93a05b9de080129e653ec4776d7e14a1b2e4f
  C:\vcpkg\bootstrap-vcpkg.bat -disableMetrics
  cmake -S . -B build-runtime -A x64 -T ClangCL -DTB_BUILD_RUNTIME_BUNDLE=ON -DPS3RECOMP_RSX_VULKAN=ON `
    -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded -DCMAKE_TOOLCHAIN_FILE=C:/vcpkg/scripts/buildsystems/vcpkg.cmake `
    -DVCPKG_MANIFEST_DIR=ci/windows-runtime -DVCPKG_TARGET_TRIPLET=x64-windows-static
  cmake --build build-runtime --config Release --parallel 4
  cmake --build build-runtime --config Release --target runtime_bundle
  ```

  The bundle is written to `build-runtime\runtime-bundle`. Delete that folder before packing again.

Details: [Runtime bundle reference](docs/RUNTIME_BUNDLE.md).

### Build and run

Add your files as shown in [What you need](#what-you-need), then:

```powershell
python scripts/port.py setup --runtime-bundle build-runtime\runtime-bundle --jobs 4
python scripts/port.py run
```

Setup extracts the package, decrypts the executable, prepares the unlock, translates the game and compiles it locally against the bundle. The result is `build-player\Release\TankBattlesRecomp.exe`. The full compile takes about 10 minutes on a 4-core CPU; lower `--jobs` if it uses too much memory. The first launch creates asset caches and takes longer. `setup --check` reports missing files without building.

Controls and saved preferences are the same as on Linux. Session logs go to `%LOCALAPPDATA%\tank-battles-recomp\logs\`; `run --console` prints to the terminal. Saves are under `game\dev_hdd0\home\00000001\savedata\`.

To play without Python, put this in a `.bat` file in the repository folder:

```bat
@echo off
cd /d "%~dp0"
build-player\Release\TankBattlesRecomp.exe "game\dev_hdd0\game\NPUB30154\USRDIR\EBOOT.elf"
```

The build folder and `game\` contain game code, assets and your license material. Keep them private.

### Optional: a faster, profile-guided build

Profile-guided optimization lowers the main thread's CPU time by about 10% on the tested NUC. It compiles an instrumented player, records one Battle round with it and recompiles. The profile comes from your game, so it stays local:

```powershell
python scripts/port.py configure --build-dir build-pgo-gen --runtime-bundle build-runtime\runtime-bundle -- -DTB_TITLE_PGO=generate
cmake --build build-pgo-gen --config Release --target TankBattlesRecomp
New-Item -ItemType Directory -Force analysis\pgo | Out-Null
$env:LLVM_PROFILE_FILE = "$PWD\analysis\pgo\battle-%p.profraw"
python scripts/port.py run --build-dir build-pgo-gen   # play a Battle round for a minute, then close the window
Remove-Item Env:LLVM_PROFILE_FILE
llvm-profdata merge -o analysis\pgo\battle.profdata analysis\pgo\battle-*.profraw
python scripts/port.py configure --build-dir build-player --runtime-bundle build-runtime\runtime-bundle -- "-DTB_TITLE_PGO=$PWD\analysis\pgo\battle.profdata"
cmake --build build-player --config Release --target TankBattlesRecomp
```

The instrumented build runs noticeably slower; only the final build is for playing. Repeat the profile after the translated game sources change.

## Built with

- [ps3recomp](https://github.com/sp00nznet/ps3recomp) by Ned Heller / sp00nznet: static recompiler and PS3 runtime. Vendored source and license details: [external/README.md](external/README.md).
- [God of War: Ascension Recomp](https://github.com/bc160201983/GoW-Ascension-Recomp): adapted texture-decoding work and implementation references. [MIT notices](licenses/GoW-Ascension-Recomp-MIT.txt).
- [Twisted Metal's SELF decryptor](https://github.com/sp00nznet/twistedmetal), through the God of War port: our portable executable-decryption tool. [MIT license](licenses/twistedmetal-MIT.txt).
- [RPCS3](https://github.com/RPCS3/rpcs3): reference behavior and public RAP format data. [GPL-2.0 notice](licenses/RPCS3-GPL-2.0.txt).
- [SDL2](https://www.libsdl.org/), [Vulkan](https://www.vulkan.org/) and [glslang](https://github.com/KhronosGroup/glslang): input, audio, windowing and graphics.

Unofficial project, not affiliated with Gameloft or Sony. Third-party components retain their own licenses.
