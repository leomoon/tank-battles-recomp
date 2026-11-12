# Tank Battles Recomp

An early-stage project to bring the PlayStation 3 version of Tank Battles (NPUB30154) to PC through static recompilation using [ps3recomp](https://github.com/sp00nznet/ps3recomp).

**THIS REPOSITORY DOES NOT CONTAIN THE GAME.**

To recompile or run the game, you must provide your own decrypted dump of a legitimately purchased copy of Tank Battles for PlayStation 3. Cloning this repository does not supply the game or a license to it.

## Required game data

You must supply the decrypted game executable (`EBOOT.elf`), the corresponding game assets, and any entitlement files required by your purchased edition. A decrypted executable alone is not the complete game. Keep all game dumps, assets, firmware, licenses, and keys local; do not commit or upload them to this repository.

RPCS3 can install your PKG and export a decrypted executable through Tools → Decrypt PS3 Binaries. Installing or running the game does not necessarily leave an exported decrypted ELF on disk. Protected EDAT assets must also be readable by the eventual port, either through its runtime support or a separately prepared decrypted dump from your purchased copy.

No game executable, game assets, decrypted dumps, firmware, license files, decryption keys, or generated translations of the game code are included in this repository. Users must generate the translated code locally from their own decrypted dump.

## Project status

Repository setup and initial binary analysis only. There is no playable native port, build pipeline, or downloadable game executable yet.

Initial static analysis of the NPUB30154 executable found approximately 17,300 candidate PPU functions, 274 imports across 19 libraries, and one embedded SPU ELF of 3,136 bytes. These are analysis results, not a compatibility claim. Programs loaded from assets or system libraries may introduce additional requirements.

Known areas to investigate include graphics and audio, missing media-player interfaces, full-game entitlement handling, save data, and offline behavior for PSN-dependent features. The first target is a native boot and title screen, followed by offline gameplay.

## Development layout

This repository holds Tank Battles-specific configuration, build scripts, and runtime bridges as they are developed. Shared runtime fixes belong in ps3recomp.

Use a separate ps3recomp source checkout alongside this repository:

```text
~/Projects/cpp_projects/
├── ps3recomp/
└── tank-battles-recomp/
```

The upstream [project template](https://github.com/sp00nznet/ps3recomp/tree/master/templates/project) and [getting-started guide](https://github.com/sp00nznet/ps3recomp/blob/master/docs/GETTING_STARTED.md) describe the recompilation pipeline. Game-specific build instructions will be added when the port is scaffolded and tested.

Use `game/` for local game data, `analysis/` for generated analysis, `recompiled/` for generated PPU code, `spu/` for extracted or translated SPU programs, and `build/` for build output. These directories are ignored by Git.

## Contributing

Contribute tooling, configuration, documentation, and original runtime fixes. Do not submit game files, firmware, licenses, keys, memory dumps, or generated game-code translations in commits, issues, or attachments. Review staged files before committing; ignore rules are a convenience and do not cover every possible asset filename.

This is an unofficial project and is not affiliated with or endorsed by Gameloft or Sony. Tank Battles and its original content belong to their respective rights holders.
