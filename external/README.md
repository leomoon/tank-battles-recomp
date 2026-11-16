# Vendored dependencies

`ps3recomp/` contains the tracked source tree from [sp00nznet/ps3recomp](https://github.com/sp00nznet/ps3recomp) at commit [`de4f090328ea08bef6b2f15caec0b85e343a7ece`](https://github.com/sp00nznet/ps3recomp/commit/de4f090328ea08bef6b2f15caec0b85e343a7ece), imported without source changes. These files are part of this repository; no submodule initialization or separate SDK checkout is required. Upstream Git history and local generated files are not included.

The upstream MIT license is preserved in [ps3recomp/LICENSE](ps3recomp/LICENSE). Bundled components retain their original notices, including [at3_standalone/COPYING.LGPLv2.1](ps3recomp/third_party/at3_standalone/COPYING.LGPLv2.1) and [smaa/LICENSE.txt](ps3recomp/third_party/smaa/LICENSE.txt).

Port-specific adaptations remain in the repository root and `sdk_overrides/`. `scripts/instrument_sdk.py` applies those adaptations to build-local SDK copies. When importing an upstream update, review it against the recorded revision, preserve its license notices, update this record, and rerun the synthetic boot and affected runtime fixtures before testing the game.
