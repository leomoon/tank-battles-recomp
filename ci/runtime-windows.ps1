param([Parameter(Mandatory=$true)][ValidateSet('test','build')][string]$Phase)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$build = 'build-runtime-windows'
if ($Phase -eq 'test') {
    python -c "import json; m=json.load(open('$build/runtime-build.json')); assert m['compiler_id']=='Clang' and m['compiler_version']=='22.1.3',m"
    $validator = Get-ChildItem "$build/vcpkg_installed/x64-windows-static/tools" -Recurse -Filter 'glslang*.exe' | Select-Object -First 1
    if (!$validator) { throw 'Pinned glslang compiler missing' }
    New-Item -ItemType Directory -Force build-shader-tools | Out-Null
    Copy-Item $validator.FullName build-shader-tools/glslangValidator.exe
    $env:PATH = "$pwd/build-shader-tools;$env:PATH"
    python scripts/compile_depth_shader.py --check
    cmake --build $build --config Release --parallel 4
    ctest --test-dir $build -C Release --output-on-failure
    python scripts/test_rpcn_client.py --runner "$build/Release/runtime_cpu_smoke.exe"
    python scripts/test_patch_network_events.py
    foreach ($suite in @('entitlement', 'texture_decode')) {
        $output = "build-test-$suite-windows"
        cmake -S "tests/$suite" -B $output -A x64 -T ClangCL
        cmake --build $output --config Release --parallel 4
        ctest --test-dir $output -C Release --output-on-failure
    }
} else {
    cmake --build $build --config Release --target runtime_bundle
    python scripts/runtime_bundle.py verify "$build/runtime-bundle" --source "$env:GITHUB_SHA"
    # Relocation/consumer checks remain in this same job, before upload.
    Copy-Item -Recurse "$build/runtime-bundle" 'relocated runtime bundle'
    python scripts/verify_runtime_consumer.py --bundle 'relocated runtime bundle' --output 'fresh consumer with spaces' --source "$env:GITHUB_SHA"
    python scripts/verify_bundle_contracts.py --bundle 'relocated runtime bundle'
}
