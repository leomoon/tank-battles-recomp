# Tank Battles Recomp

A native Linux port of **Tank Battles for PS3 (NPUB30154)** using static recompilation. The game's PPU/SPU code is translated locally into C/C++ and compiled with a PS3 runtime and Vulkan renderer. Battle and Campaign are playable; Windows support is planned.

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
sudo pacman -S --needed base-devel git python python-pip cmake ninja pkgconf sdl2-compat vulkan-headers vulkan-icd-loader glslang spirv-tools
```

**Ubuntu 24.04+ / Debian 13+:**

```bash
sudo apt update
sudo apt install build-essential git python3 python3-venv python3-pip cmake ninja-build pkg-config libsdl2-dev libvulkan-dev glslang-dev glslang-tools spirv-tools
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

To keep your keys elsewhere, add `--keys /path/to/keys` to setup. To use an installed game folder instead of a PKG, pass `--game-dir /path/to/NPUB30154`. Run `python scripts/port.py setup --help` for the other options.

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

The executable still needs the ELF and game data. Python, the SDK, generated C/C++ sources, original PKG and SELF key file are not needed to play. The destination Linux system needs compatible SDL2, glslang/SPIRV-Tools runtime libraries, the Vulkan loader and a GPU driver; this is not a self-contained AppImage. Saves are under `game/dev_hdd0/home/00000001/savedata/`.

This finished folder contains game code, assets and your license material. Keep it private.

## Windows

Planned. Build and packaging instructions will be added when Windows is supported.

## Built with

- [ps3recomp](https://github.com/sp00nznet/ps3recomp) by Ned Heller / sp00nznet: static recompiler and PS3 runtime. Vendored source and license details: [external/README.md](external/README.md).
- [God of War: Ascension Recomp](https://github.com/bc160201983/GoW-Ascension-Recomp): adapted texture-decoding work and implementation references. [MIT notices](licenses/GoW-Ascension-Recomp-MIT.txt).
- [Twisted Metal's SELF decryptor](https://github.com/sp00nznet/twistedmetal), through the God of War port: our portable executable-decryption tool. [MIT license](licenses/twistedmetal-MIT.txt).
- [RPCS3](https://github.com/RPCS3/rpcs3): reference behavior and public RAP format data. [GPL-2.0 notice](licenses/RPCS3-GPL-2.0.txt).
- [SDL2](https://www.libsdl.org/), [Vulkan](https://www.vulkan.org/) and [glslang](https://github.com/KhronosGroup/glslang): input, audio, windowing and graphics.

Unofficial project, not affiliated with Gameloft or Sony. Third-party components retain their own licenses.
