# Guided Linux setup

Use Python 3.11 or newer in the virtual environment described in [README](../README.md). The repository contains the recompiler and runtime, but no game, purchased license, private key or generated game code.

Put your original NPUB30154 PKG and purchased unlock EDAT in `game/input/`, then run:

```bash
python scripts/port.py setup --check
python scripts/port.py setup --jobs 4
python scripts/port.py run
```

`--check` reports all missing or mismatched title files, the executable, unlock and RAP requirements, with an action for each. It never installs or decrypts files. Checking a PKG extracts it into temporary storage, which is removed afterward. A missing unlock is a warning because the game can run as a trial. A license-2 unlock requires its purchased 16-byte RAP or an already prepared 16-byte sidecar; the tested original license-3 unlock needs neither.

Choose one executable preparation method:

| Method | Setup option |
| --- | --- |
| Portable SELF decryptor | Put your scetool-format key file at `game/keys`, or use `--keys /path/to/keys` or `PS3_KEYS` |
| Existing local RPCS3 source as key provider | `--rpcs3-source /path/to/rpcs3` |
| Your own RPCS3 executable or AppImage | `--rpcs3 /path/to/rpcs3` |
| Manually decrypted executable | `--elf /path/to/EBOOT.elf` |

The portable method needs NPDRM revision `0001` erk/riv and `NP_klic_free`/`NP_klic_key` entries. Nothing downloads RPCS3 or keys. The RPCS3 executable method runs `--headless --decrypt` on a disposable copy of `EBOOT.BIN`, with temporary XDG configuration/cache directories; its output is suppressed because it may contain key material. Use `--elf` if your RPCS3 build cannot run that command unattended. A manually supplied ELF is copied into the installed game, leaving its source untouched.

You can supply `--pkg /path/to/original.pkg --unlock /path/to/purchased.edat` instead of copying files into `game/input/`. For an installed dump, use `--game-dir /path/to/NPUB30154`; a valid `USRDIR/EBOOT.elf` there is preserved. Add `--rap /path/to/purchased.rap` only when needed. `--import-only` stops after installation, executable preparation and entitlement preparation.

## Verification and retries

Setup checks the full SHA-256 hashes of `PARAM.SFO`, `EBOOT.BIN`, all four asset EDATs and the decrypted executable. Both independently verified ELF layouts, from RPCS3 and the portable decryptor, are accepted. `scripts/setup_inputs.py` contains the tested hashes. An unknown hash stops setup unless you explicitly pass `--allow-unknown-version`. This prints a warning and does not establish compatibility. Wrong title IDs, malformed containers, encrypted executables supplied as ELF, wrong architectures and invalid ELF load/entry bounds remain errors with that flag.

Setup validates a staged import before replacing an installed title. It preserves the source PKG, dump, ELF and license files. The installed unlock is given the canonical content-ID filename. Runtime authentication still validates the purchased unlock; setup's structural checks do not replace it. Asset EDATs decrypt into local caches at first launch.

After a failed or interrupted command, correct the reported problem and rerun the same setup command. An imported package with matching installed files and a verified ELF is reused without requiring the decryption provider again. PPU translation is reused only when the ELF, translation recipe, tool sources and every generated output still match its completion record. Regeneration uses a temporary directory and publishes only complete output; identical output keeps its timestamps. A matching CMake configuration is reused only when its recipe, cache and build rules are intact. Ninja always checks native sources and CMake regeneration dependencies and retains successfully compiled objects after an interruption. Failed stages print the stage name and retry action. A lock prevents two setup commands from replacing or building the same installation concurrently.

`game/setup.json` records input hashes and the selected provider; `game/setup-state.json` records completed translation/build stages. Neither contains keys. `--restart` forces PPU translation regeneration, keeping purchased inputs; native compilation remains incremental. Changes to C++ sources or build settings are checked by CMake/Ninja on every setup run. Setup does not automatically launch the game.

## Game-free tests

```bash
python -m unittest discover -s scripts -p 'test_*setup*.py'
```

These fixtures exercise missing files, hash mismatches, structural rejection, unknown-version warnings, manual ELF and portable decryption, the RPCS3 command contract, license rules, source preservation, rollback, translation invalidation, locking and failed-stage retries. A real purchased-dump fresh-clone build and launch are separate integration checks; no game content belongs in the test fixtures or Git.
