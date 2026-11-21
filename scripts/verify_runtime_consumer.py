"""Link only original synthetic title code against a relocated prebuilt runtime."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from runtime_bundle import verify

ROOT=Path(__file__).resolve().parents[1]

def run(*args):
    subprocess.run([str(value) for value in args],check=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--source',required=True)
    parser.add_argument('--toolchain',help='Staged standalone-builder toolchain (scripts/toolchain.py stage). Builds with only its CMake, Ninja, Python and Zig, with PATH limited to it')
    args=parser.parse_args();bundle=Path(args.bundle).resolve();out=Path(args.output).resolve()
    manifest=verify(bundle,source=args.source)
    out.mkdir(parents=True,exist_ok=False)
    data=out/'synthetic'
    sdk=ROOT/'external/ps3recomp'
    run(sys.executable,ROOT/'scripts/prepare_runtime_fixture.py','--sdk',sdk,'--output',data)
    run(sys.executable,ROOT/'scripts/runtime_abi.py','--sdk',sdk,'--output',out/'abi','--check-title-header',data/'ppu_recomp.h')
    if json.loads((out/'abi/abi.json').read_text())['header_sha256']!=manifest['abi_sha256']:
        raise SystemExit('Local lifter differs from the downloaded runtime ABI')
    spu=out/'synthetic-spu'
    run(sys.executable,sdk/'tools/build_spu_workloads.py','--input',data/'synthetic-spu.elf','--lifted',spu,'--out',spu/'spu_workloads.c','--constructor')
    # This project has no SDK runtime sources or root add_subdirectory call.
    cmake='''cmake_minimum_required(VERSION 3.20)
project(RuntimeConsumer LANGUAGES C CXX)
set(CMAKE_C_STANDARD 17)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded)
set(TB_RUNTIME_EXPECTED_SOURCE "@SOURCE@")
set(TB_RUNTIME_CPU_SMOKE ON)
set(TB_RUNTIME_ROOT "@BUNDLE@")
include("@BUNDLE@/cmake/RuntimeConsumer.cmake")
include("@SPU@/sources.cmake")
add_executable(consumer "@DATA@/smoke_recomp.c" "@BUNDLE@/src/runtime_entry.cpp" ${SPU_GENERATED_SOURCES})
target_include_directories(consumer BEFORE PRIVATE "@DATA@")
target_link_libraries(consumer PRIVATE TankBattlesRuntime)
set_target_properties(consumer PROPERTIES LINKER_LANGUAGE CXX)
'''.replace('@SOURCE@',args.source).replace('@BUNDLE@',bundle.as_posix()).replace('@SPU@',spu.as_posix()).replace('@DATA@',data.as_posix())
    (out/'CMakeLists.txt').write_text(cmake)
    target_windows=manifest['build'].get('system')=='Windows'
    if args.toolchain:
        tc=Path(args.toolchain).resolve()
        identity=json.loads((tc/'toolchain.json').read_text())
        exe_suffix='.exe' if identity['host']=='windows' else ''
        cmake_exe=tc/'cmake/bin'/('cmake'+exe_suffix)
        python=tc/'python'/('python.exe' if identity['host']=='windows' else 'bin/python3')
        target='windows' if target_windows else 'linux'
        configure=[cmake_exe,'-S',out,'-B',out/'build','-G','Ninja','-DCMAKE_BUILD_TYPE=Release',
                   '-DCMAKE_MAKE_PROGRAM='+str(tc/('ninja'+exe_suffix)),
                   '-DCMAKE_TOOLCHAIN_FILE='+str(tc/'cmake'/f'zig-{target}.cmake'),'-DPython3_EXECUTABLE='+str(python)]
        # Only staged tools are reachable; a runner's own compilers cannot satisfy the build.
        env={'PATH':os.pathsep.join(str(p) for p in (tc/'bin',tc/'cmake/bin',python.parent)),
             'ZIG_GLOBAL_CACHE_DIR':str(out/'zig-cache'),'ZIG_LOCAL_CACHE_DIR':str(out/'zig-cache'),
             'HOME':str(out),'TMPDIR':str(out),'TEMP':str(out),'TMP':str(out),
             **{k:os.environ[k] for k in ('SYSTEMROOT','WINDIR','COMSPEC') if k in os.environ}}
        subprocess.run([str(v) for v in configure],check=True,env=env)
        subprocess.run([str(cmake_exe),'--build',str(out/'build'),'--parallel','4'],check=True,env=env)
        exe=out/'build'/('consumer.exe' if target_windows else 'consumer')
    else:
        configure=['cmake','-S',out,'-B',out/'build']
        if os.name=='nt':configure+=['-A','x64','-T','ClangCL']
        else:configure+=['-G','Ninja','-DCMAKE_BUILD_TYPE=Release']
        run(*configure)
        run('cmake','--build',out/'build','--config','Release','--parallel','2')
        exe=out/'build'/('Release/consumer.exe' if os.name=='nt' else 'consumer')
    if target_windows!=(os.name=='nt'):
        if not exe.is_file():
            raise SystemExit('Cross-target consumer did not link: '+str(exe))
        print('PASS: relocated artifact cross-linked the synthetic title for',manifest['build'].get('target') or 'Windows','(run it on that OS)')
        return
    run(sys.executable,ROOT/'scripts/verify_runtime_smoke.py','--runner',exe,'--data',data)
    print('PASS: relocated artifact linked and ran synthetic static PPU/SPU without rebuilding host/runtime')

if __name__=='__main__':main()
