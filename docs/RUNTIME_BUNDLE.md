# Game-free Windows runtime bundle

The `Game-free Windows Vulkan runtime` workflow compiles the reusable host services, ps3recomp runtime and Vulkan renderer. Its artifact contains no Tank Battles executable, purchased files, licences/keys, game translations or actual generated SPU workloads. The complete player still requires local translation, compilation and linking from the user's purchased inputs. Guided reuse in `port.py` is the next implementation step.

## Build and artifact

CI uses Windows 2025, clang-cl 22.1.3, the Release static Microsoft CRT and `x64-windows-static` dependencies. The vcpkg repository and dependency baseline are pinned to `0df93a05b9de080129e653ec4776d7e14a1b2e4f`; the manifest is in `ci/windows-runtime/vcpkg.json`. This includes SDL2 with Vulkan support, glslang with HLSL legalization and command-line tools, Vulkan headers and libiconv. Vulkan itself is loaded from the installed GPU driver's system loader at runtime; the bundle does not redistribute a driver.

The source build entry point is:

```sh
cmake -S . -B build-runtime -DTB_BUILD_RUNTIME_BUNDLE=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build-runtime --parallel 4
ctest --test-dir build-runtime --output-on-failure
cmake --build build-runtime --target runtime_bundle
```

Windows CI supplies its pinned vcpkg toolchain, `-A x64 -T ClangCL`, `-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded` and the manifest directory. Use a fresh artifact destination when repackaging. The normal player build remains unchanged and continues using the local game translations.

The archive contains:

- Separate host, GPU boot, CPU smoke boot and SDK static libraries. Their source lists and archive members are audited before publication.
- A common `ppu_recomp.h` ABI without title-specific function declarations, SDK headers, an entry-point source and a relocatable consumer CMake include.
- Release static dependency libraries and public headers, dependency package metadata and licence notices. Build caches, downloaded source caches and private data are excluded.
- Original synthetic CPU and real-backend GPU test executables, plus the existing host-service/entitlement contract executables.
- A manifest identifying the source commit, compiler, target, CRT, ABI hash, compiled-source hashes, archive members and SHA-256 of every payload file.

The common ABI is emitted by the matching lifter. Configuration rejects a title header with a different ABI preamble. The tiny `runtime_entry.cpp` remains a local compilation unit and provides the Windows UTF-8 entry point. Generated PPU functions, their table, actual SPU workloads and their registries stay outside the reusable archives.

## Download verification and consumer test

A separate clean Windows job downloads the bundle into a path with spaces, verifies the selected source identity and every file hash, then compiles only original synthetic PPU/SPU code and the entry point. It links against the downloaded archives and executes the smoke title without rebuilding the host/runtime. The same job reruns downloaded input, audio, HLE, cache, entitlement and timeline fixtures. Missing/changed/extra payloads and mismatched source, ABI, compiler, OS or CRT are errors.

To exercise the same check manually with a matching checkout and compiler:

```sh
python scripts/verify_runtime_consumer.py --bundle "path to extracted bundle" --output "new consumer directory" --source SELECTED_COMMIT_SHA
python scripts/verify_bundle_contracts.py --bundle "path to extracted bundle"
```

The synthetic smoke verifies loader segments/BSS/TLS, HLE dispatch, guest thread creation/join, three FIFO flips, vblank delivery, exit status and a statically translated SPU result. Its vblank thread assertion follows the port's dedicated interrupt ticker, rather than the SDK smoke boot's main-thread callback queue. This test explicitly uses the null graphics backend (software rendering with GDI on Windows) and does not validate a Windows GPU.

`runtime_shader_check` links the production SDK archive and translates vertex and fragment HLSL to SPIR-V without a GPU. CI runs it before packaging and again from the downloaded bundle. This catches an inactive shader compiler even when the synthetic CPU smoke passes.

## GPU and real-game validation

Hosted CI compiles `vulkan_draw_groups`, `vulkan_depth16` and `vulkan_nv3089`; it does not execute them without a suitable physical GPU. They use the production instrumented Vulkan backend and existing exact ordering, depth and gather assertions. Run them on the Windows NUC before claiming renderer support. Physical controllers/audio, real-game rendering, resize/focus/fullscreen, driver limits and NUC performance remain separate hardware acceptance tasks.

Linux retains its existing GPU regressions and unchanged strict real-game Battle performance gate. A compiled Windows renderer and passing CPU smoke are insufficient evidence for Windows gameplay correctness or 60 FPS support.

## Verified CI

[The complete Windows validation run](https://github.com/leomoon/tank-battles-recomp/actions/runs/44024961021) passed on source snapshot `551696b3c178a7ba001048ee1ff5807b97b472e4`: all 15 build-job CTest groups, exact embedded shaders, archive/content audit, artifact publication and the separate fresh-runner synthetic link/boot and downloaded contract suites. GPU test tools were compiled in Windows CI and executed on the Linux NVIDIA host; Windows hardware execution remains pending. Detailed local evidence is in `analysis/runtime-bundle/verification.json`.

Physical Windows testing subsequently found that snapshot's SDK archive contained the disabled shader-translation stub. The Windows port had set the enabling source property outside the directory that owns the SDK target. The corrected configuration applies the property with `TARGET_DIRECTORY ps3recomp_runtime` and validates it on Windows as well as Linux. Old bundles require replacement; a newer GPU cannot enable a compiler omitted from the binary.

The corrected local Windows build passes all 16 CPU test groups and boots the private player on Iris Pro 580 with `backend-ready a=1 b=1 c=1`, verified purchased unlock, translated guest draws and 1280x720 window presentations. The 30-second boot check ends by timeout and does not establish gameplay or clean shutdown acceptance. The draw-groups GPU fixture passes its former pipeline-creation failure but still fails its later presented-center pixel assertion; physical rendering acceptance remains pending.
