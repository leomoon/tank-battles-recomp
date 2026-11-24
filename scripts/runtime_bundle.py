"""Package and verify explicitly selected game-free host artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def archive_members(path):
    """Read both GNU and COFF ar containers, including long member names."""
    blob=Path(path).read_bytes()
    if not blob.startswith(b'!<arch>\n'):
        raise ValueError('Not a static archive: '+str(path))
    offset=8;names=b'';result=[]
    while offset<len(blob):
        header=blob[offset:offset+60]
        if len(header)!=60 or header[58:]!=b'`\n':
            raise ValueError('Malformed static archive: '+str(path))
        size=int(header[48:58]);name=header[:16].decode('ascii').strip()
        data=blob[offset+60:offset+60+size]
        if len(data)!=size:raise ValueError('Truncated static archive')
        if name=='//':names=data
        elif name not in ('/','/SYM64/'):
            if name.startswith('/') and name[1:].isdigit():
                name=names[int(name[1:]):].split(b'\n',1)[0].split(b'\0',1)[0].decode().rstrip('/')
            elif name.startswith('#1/'):
                name=data[:int(name[3:])].rstrip(b'\0').decode()
            else:name=name.rstrip('/')
            if name:result.append(name.replace('\\','/').rsplit('/',1)[-1])
        offset+=60+size+(size%2)
    if not result:raise ValueError('Empty static archive: '+str(path))
    return result

def verify(root, source=None, abi=None):
    root=Path(root).resolve()
    manifest=json.loads((root/'manifest.json').read_text())
    if manifest.get('schema')!=1 or manifest.get('kind')!='game-free-runtime':
        raise ValueError('Unsupported runtime manifest')
    if source and manifest['source_commit']!=source:
        raise ValueError('Runtime source commit mismatch')
    if abi and manifest['abi_sha256']!=abi:
        raise ValueError('Runtime ABI mismatch')
    files=manifest['files']
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if actual!=set(files)|{'manifest.json'}:
        raise ValueError('Runtime file allowlist mismatch')
    for name,digest in files.items():
        path=root/name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Unsafe runtime path: '+name)
        if sha(path)!=digest:
            raise ValueError('Runtime file hash mismatch: '+name)
    if sha(root/'include/ppu_recomp.h')!=manifest['abi_sha256']:
        raise ValueError('Runtime ABI header mismatch')
    return manifest

def pack(args):
    build=Path(args.build).resolve();out=Path(args.output).resolve()
    if out.exists():
        raise ValueError('Bundle destination already exists; use a fresh output directory')
    if args.configuration!='Release':
        raise ValueError('Runtime bundles require Release configuration')
    metadata=json.loads((build/'runtime-build.json').read_text())
    metadata['configuration']=args.configuration
    # Reject title sources in either reusable archive before copying anything.
    for source in metadata['archive_sources']:
        path=Path(source).resolve()
        allowed=path.is_relative_to(ROOT) and not any(part in {'game','recompiled','spu','generated_spu','synthetic'} for part in path.relative_to(ROOT).parts[:1])
        # SDK runtime/spu sources are ordinary source, never lifted workloads.
        if not path.is_file():
            raise ValueError('Missing reusable archive source: '+source)
        if not allowed or ('generated_spu' in path.parts) or path.name.startswith('smoke') or path.is_relative_to(ROOT/'recompiled') or path.is_relative_to(ROOT/'game') or path.is_relative_to(ROOT/'spu'):
            raise ValueError('Forbidden reusable archive source: '+source)
    out.mkdir(parents=True)
    def copy(source,relative):
        destination=out/relative;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,destination)
        destination.chmod(0o644)
    copy(args.host,'lib/'+Path(args.host).name)
    copy(args.runtime,'lib/'+Path(args.runtime).name)
    copy(args.boot,'lib/'+Path(args.boot).name)
    copy(args.cpu_boot,'lib/'+Path(args.cpu_boot).name)
    allowed_tools={'runtime_cpu_smoke','runtime_shader_check','vulkan_draw_groups','vulkan_depth16','vulkan_nv3089',
                   'runtime_display_settings_check','runtime_display_window_check','runtime_render_scale_check','vulkan_render_scale',
                   'hle_fixture','positions_fixture','hotplug_fixture','keyboard_fixture','audio_fixture',
                   'cache_fixture','timeline_fixture','entitlement_fixture','audio_burst_fixture',
                   'matching_rooms','peer_udp_fixture','sysnet_fixture'}
    for tool in args.tool:
        name=Path(tool).name
        if (name[:-4] if name.endswith('.exe') else name) not in allowed_tools:
            raise ValueError('Tool is outside the game-free allowlist: '+name)
        copy(tool,'bin/'+name)
        (out/'bin'/name).chmod(0o755)
    copy(build/'runtime-abi/ppu_recomp.h','include/ppu_recomp.h')
    copy(build/'runtime-abi/abi.json','include/abi.json')
    sdk=ROOT/'external/ps3recomp'
    for folder in ('include','runtime/spu','runtime/platform','runtime/memory','libs/video'):
        for header in (sdk/folder).rglob('*.h'):
            copy(header,'sdk/'+header.relative_to(sdk).as_posix())
    for name in ('runtime_entry.cpp','host_utf8_args.h','LICENSE'):
        copy(ROOT/name,('src/' if name!='LICENSE' else 'licences/port/')+name)
    copy(sdk/'LICENSE','licences/ps3recomp/LICENSE')
    for path in (ROOT/'licenses').glob('*.txt'):
        copy(path,'licences/attributions/'+path.name)
    copy(ROOT/'cmake/RuntimeConsumer.cmake','cmake/RuntimeConsumer.cmake')
    copy(ROOT/'scripts/runtime_bundle.py','tools/runtime_bundle.py')
    if not metadata.get('toolchain'):
        copy(ROOT/'ci/windows-runtime/vcpkg.json','provenance/vcpkg.json')
    metadata['source_sha256']={str(Path(p).relative_to(ROOT)):sha(Path(p)) for p in metadata['archive_sources']}
    (out/'provenance').mkdir(exist_ok=True)
    copy(build/'instrumented_sdk/manifest.json','provenance/sdk-overlay.json')
    (out/'provenance/build.json').write_text(json.dumps(metadata,indent=2)+'\n')
    # Only release static libraries, public headers, package metadata and notices.
    # No vcpkg download/build caches, executables, debug libraries or DLLs.
    if args.dependencies:
        deps=Path(args.dependencies)
        zig_prefix=(deps/'deps.json').is_file()
        if not (deps/'lib').is_dir() or not (zig_prefix or (deps.parent/'vcpkg/status').is_file()):
            raise ValueError('Dependency tree must be a vcpkg triplet directory beside vcpkg/status, or a scripts/build_runtime_deps.py prefix with deps.json: '+str(deps))
        if zig_prefix:
            # Static libraries, headers and relocatable CMake packages built by the
            # pinned Zig; DLL import libraries, executables and scripts stay out.
            copy(deps/'deps.json','provenance/deps.json')
            copy(ROOT/'ci/toolchain/manifest.json','provenance/toolchain-manifest.json')
        else:
            copy(deps.parent/'vcpkg/status','provenance/vcpkg-status.txt')
        for folder in ('include','lib','share'):
            if not (deps/folder).is_dir():
                continue
            for path in (deps/folder).rglob('*'):
                if path.is_file():
                    if path.is_symlink() or path.suffix.lower() in {'.dll','.exe','.pdb','.bin','.elf','.edat','.rap','.rif','.rifkey','.pkg','.self'}:
                        raise ValueError('Unexpected dependency payload: '+str(path))
                    if zig_prefix and (path.name.endswith('.dll.a') or '.so' in path.suffixes or path.suffix in ('.la','.pc') or 'pkgconfig' in path.parts):
                        continue
                    copy(path,'deps/'+path.relative_to(deps).as_posix())
    members={}
    expected={Path(p).name for p in metadata['archive_sources']}
    for archive in (args.host,args.runtime,args.boot,args.cpu_boot):
        values=archive_members(archive)
        for member in values:
            if member.endswith('.obj'):stem=member[:-4]
            elif member.endswith('.o'):stem=member[:-2]
            else:raise ValueError('Unexpected archive member: '+member)
            if stem not in expected and not any(Path(p).stem==stem for p in metadata['archive_sources']):
                raise ValueError('Unlisted archive member: '+member)
        members[Path(archive).name]=values
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    manifest=dict(schema=1,kind='game-free-runtime',source_commit=commit,
                  abi_sha256=sha(out/'include/ppu_recomp.h'),build=metadata,archive_members=members,
                  host_library=Path(args.host).name,runtime_library=Path(args.runtime).name,
                  boot_library=Path(args.boot).name,cpu_boot_library=Path(args.cpu_boot).name,
                  files={p.relative_to(out).as_posix():sha(p) for p in sorted(out.rglob('*')) if p.is_file()})
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    verify(out,source=commit,abi=manifest['abi_sha256'])
    print('Verified game-free runtime:',len(manifest['files']),'files; source',commit)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('pack')
    for name in ('build','output','host','runtime','boot','cpu-boot'):p.add_argument('--'+name,required=True)
    p.add_argument('--tool',action='append',default=[])
    p.add_argument('--dependencies')
    p.add_argument('--configuration',default='Release')
    p=sub.add_parser('verify');p.add_argument('root');p.add_argument('--source');p.add_argument('--abi')
    args=parser.parse_args()
    if args.command=='pack':pack(args)
    else:
        value=verify(args.root,args.source,args.abi)
        print('PASS: runtime manifest, allowlist, file hashes and ABI',value['source_commit'])

if __name__=='__main__':main()
