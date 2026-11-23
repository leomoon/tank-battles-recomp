# Tank Battles Recomp

Play **Tank Battles for PS3 (NPUB30154)** natively on Linux and Windows. The standalone builder compiles your own copy locally. **The download contains no game files.**

## Build and play

Download your platform's builder from [GitHub Releases](https://github.com/leomoon/tank-battles-recomp/releases). Both builders include the compiler and build tools. You’ll need a Vulkan-capable GPU and driver to play.

- On **Linux (x86-64)**, make `Builder.AppImage` executable in your file manager or with `chmod +x Builder.AppImage`, then open it. For systems without FUSE, see the [AppImage launch options](docs/TOOLCHAIN.md#the-standalone-builders).
- On **Windows (x64)**, open `Builder.exe`.

1. In the builder, select your purchased **NPUB30154 game package** (`.pkg`) and its **unlock file** (`.edat`).
2. Select your decrypted `EBOOT.elf`, or supply your scetool-format SELF keys under **Advanced** so the builder can decrypt it. Add your `.rap` under **Advanced** if your unlock uses license type 2. [Input details](docs/BUILD_FROM_SOURCE.md#what-you-need)
3. Choose **Install to**, click **Build**, then click **Play** when it finishes.

To play again, open **Play Tank Battles** (`Play Tank Battles.exe` on Windows) in the folder you chose. Keep or move the whole folder together. It contains your game, saves, and settings, so keep it private. Rebuilding into the same folder preserves your saves and settings.

## Source build and credits

Built with ps3recomp, SDL2, and Vulkan. The developer setup, full credits, and third-party licenses are in the [source-build guide](docs/BUILD_FROM_SOURCE.md).
