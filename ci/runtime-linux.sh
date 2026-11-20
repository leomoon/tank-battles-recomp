#!/usr/bin/env bash
set -euo pipefail
phase=${1:?Expected setup, test or build}
source_root=/root/tank-battles-ci
case "$phase" in
  setup)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y build-essential cmake ninja-build python3 python3-venv git \
      libsdl2-dev libvulkan-dev glslang-dev glslang-tools spirv-tools libcurl4-openssl-dev
    # Linux filesystems preserve the permissions tested by private-file fixtures.
    git config --global --add safe.directory "$2"
    git clone --no-hardlinks "$2" "$source_root"
    cd "$source_root"
    git checkout --detach "$3"
    python3 -m venv /root/tank-battles-venv
    source /root/tank-battles-venv/bin/activate
    python -m pip install -r external/ps3recomp/requirements.txt
    cmake -S . -B build-runtime-linux -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DTB_BUILD_RUNTIME_BUNDLE=ON -DPS3RECOMP_RSX_VULKAN=ON
    ;;
  test)
    cd "$source_root"
    source /root/tank-battles-venv/bin/activate
    python scripts/compile_depth_shader.py --check
    cmake --build build-runtime-linux --parallel 4
    ctest --test-dir build-runtime-linux --output-on-failure
    python scripts/test_rpcn_client.py --runner build-runtime-linux/runtime_cpu_smoke
    python scripts/test_patch_network_events.py
    for suite in entitlement texture_decode; do
      cmake -S "tests/$suite" -B "build-test-$suite-linux" -G Ninja -DCMAKE_BUILD_TYPE=Release
      cmake --build "build-test-$suite-linux" --parallel 4
      ctest --test-dir "build-test-$suite-linux" --output-on-failure
    done
    ;;
  build)
    cd "$source_root"
    source /root/tank-battles-venv/bin/activate
    cmake --build build-runtime-linux --target runtime_bundle
    python scripts/runtime_bundle.py verify build-runtime-linux/runtime-bundle --source "$3"
    cp -a build-runtime-linux/runtime-bundle 'relocated runtime bundle'
    python scripts/verify_runtime_consumer.py --bundle 'relocated runtime bundle' --output 'fresh consumer with spaces' --source "$3"
    python scripts/verify_bundle_contracts.py --bundle 'relocated runtime bundle'
    tar -czf "$2/linux-runtime.tar.gz" -C build-runtime-linux runtime-bundle
    ;;
  *) echo "Unknown phase: $phase" >&2; exit 2 ;;
esac
