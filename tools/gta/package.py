"""Create an explicit GTA integration package with no imported game material.

Default includes our native ASI, Rust runtime host and private Python runtime.
--source-only omits all compiled builds and third-party runtime binaries.
Never reads installations, research captures, private SDKs, extracted caches or
the old CoD release packager's private asset inventory.
"""
import argparse
import hashlib
import json
from pathlib import Path

from install import ROOT, adapter_build
from import_resources import plain_path
from release_version import VERSION

PIN_METADATA={'component_bootstrap.json','game_context_bootstrap.json','gameplay_frames.json',
              'resource_bootstrap.json','scene_component_sizes.json','skater_bootstrap.json','terrain_audio_sources.json',
              'vehicle_native_pins.json'}
EXTENSIONS={'.py','.cpp','.h','.hpp','.def','.ps1','.md'}
COMMON=('assets_export.py','assets_models.py','assets_skeleton.py','carentan_qb.py','carentan_col.py')
REUSED=('export_hud.py','build_board.py','retarget.py')
HOST_SOURCE=('Cargo.toml','Cargo.lock','README.md','build.ps1','src/main.rs','src/gta_bridge.rs')
RUNTIME_HOST_SOURCE=('Cargo.toml','Cargo.lock','README.md','build.rs','build.ps1','tls.c','src/main.rs','src/image.rs','src/file_events.rs')
ROOT_FILES=('LICENSE','THIRD_PARTY_NOTICES.md','Run-GTA-THAW.cmd','Configure-GTA-THAW.cmd',
            'Run-GTA-American-Wasteland.cmd')
PUBLIC_DOCS={'docs/release/README.md':'README.md', 'docs/release/README_PL.md':'README_PL.md',
             'docs/release/BUILDING.md':'docs/BUILDING.md',
             'docs/release/launcher.png':'docs/launcher.png'}


def selected_files(root=ROOT, *, source_only=False):
    root=plain_path(root).resolve(strict=True)
    selected={}
    def add(relative,output=None):
        path=plain_path(root/relative).resolve(strict=True)
        if not path.is_file() or not path.is_relative_to(root):
            raise ValueError('Missing or external integration file: '+relative)
        key=output or relative
        if key in selected: raise ValueError('Duplicate package entry: '+key)
        selected[key]=path
    for name in ROOT_FILES:
        add(name)
    for name,output in PUBLIC_DOCS.items(): add(name,output)
    # Retain canonical inputs so a source release can package itself again.
    for name in PUBLIC_DOCS: add(name)
    add('docs/gta-first-run.md')
    for directory in ('tools/gta','tools/vanilla_thaw'):
        for path in sorted((root/directory).rglob('*')):
            relative=path.relative_to(root)
            # This independent crate is an explicit boundary: no root CoD
            # workspace, vendored dependencies, caches or incidental host files.
            if relative.parts[:3] in (('tools','gta','host'),('tools','gta','runtime_host')): continue
            if any(part in ('__pycache__','target','private-sdk','data') for part in relative.parts): continue
            if path.is_file() and (path.suffix in EXTENSIONS or
                    (directory=='tools/vanilla_thaw' and path.name in PIN_METADATA)):
                add(relative.as_posix())
    for name in HOST_SOURCE: add('tools/gta/host/'+name)
    for name in RUNTIME_HOST_SOURCE: add('tools/gta/runtime_host/'+name)
    for name in COMMON: add('tools/'+name)
    for name in REUSED: add('tools/waw/'+name)
    for path in sorted((root/'tools/gta/licenses').glob('*.txt')):
        add(path.relative_to(root).as_posix())
    if not source_only:
        from atomic_store import validate_build
        from stock_host import validate_host
        from launcher_build import validate_build as validate_launcher
        adapter_build(root)
        validate_host(root)
        validate_build(root)
        validate_launcher(root)
        add('build/gta-native/thaw_gta.asi'); add('build/gta-native/adapter-build.json')
        add('build/gta-runtime/gta-thaw-runtime.exe'); add('build/gta-runtime/host-build.json')
        add('build/gta-runtime/gta-thaw-tls.dll')
        add('build/gta-transport/thaw-gta-atomic.dll'); add('build/gta-transport/transport-build.json')
        add('build/gta-launcher/GTA American Wasteland.exe','GTA American Wasteland.exe')
        add('build/gta-launcher/launcher-build.json')
    return selected


def create(output, *, root=ROOT, source_only=False, python_cache=None, offline=False):
    files=selected_files(root,source_only=source_only)
    output=plain_path(output,missing=True).resolve()
    if output.exists(): raise ValueError('Package output must be a new directory')
    # A package can be staged under dist, but never into selected source subtrees.
    for base in (Path(root)/'tools',Path(root)/'docs',Path(root)/'src',Path(root)/'build'):
        if output.is_relative_to(base.resolve()): raise ValueError('Package cannot replace a project source/build subtree')
    runtime_cache=None if source_only else plain_path(python_cache or Path(root)/'downloads/python',missing=True).resolve()
    if runtime_cache is not None and runtime_cache.is_relative_to(output):
        raise ValueError('Python download cache must be outside the package output')
    output.mkdir(parents=True)
    rows=[]
    if not source_only:
        marker=b'This integration package requires its included runtime/python directory.\n'
        (output/'PORTABLE_RUNTIME_REQUIRED').write_bytes(marker)
        rows.append(dict(path='PORTABLE_RUNTIME_REQUIRED',bytes=len(marker),sha256=hashlib.sha256(marker).hexdigest()))
    for relative,source in sorted(files.items()):
        data=source.read_bytes(); destination=output/relative; destination.parent.mkdir(parents=True,exist_ok=True)
        with destination.open('xb') as stream: stream.write(data)
        rows.append(dict(path=relative,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
    runtime=None
    if not source_only:
        from portable_python import stage
        runtime=stage(output/'runtime/python',runtime_cache,offline=offline)
        rows.extend(dict(row,path='runtime/python/'+row['path']) for row in runtime['files'])
    (output/'.gitignore').write_text('research/\nlocal/\nruntime/\ndownloads/\nbuild/\n.build/\ndist/\ninstallation.json\nlauncher-settings.json\n**/__pycache__/\n'
        '**/target/\n**/settings.json\n**/thaw_gta/\n*.log\n*.db\n*.sqlite\n',encoding='ascii')
    manifest=dict(format='gta-thaw-public-integration-package-v2',version=VERSION,
        source_only=source_only,original_game_assets_included=False,original_game_executables_included=False,
        third_party_binaries_included=not source_only,private_research_included=False,
        external_python_installation_required=source_only,
        python_runtime=None if runtime is None else {key:runtime[key] for key in
            ('version','architecture','source_url','source_sha256')},files=sorted(rows,key=lambda row:row['path']))
    (output/'PACKAGE_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return dict(output=str(output),files=len(rows),bytes=sum(r['bytes'] for r in rows),source_only=source_only)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path); parser.add_argument('--source-only',action='store_true')
    parser.add_argument('--python-cache',type=Path); parser.add_argument('--offline',action='store_true')
    args=parser.parse_args()
    if args.output: print(json.dumps(create(args.output,source_only=args.source_only,
        python_cache=args.python_cache,offline=args.offline),indent=2))
    else:
        files=selected_files(source_only=args.source_only)
        print(json.dumps(dict(files=len(files),bytes=sum(p.stat().st_size for p in files.values()),
            game_assets_included=False,compiled_adapter_included=not args.source_only,
            rust_runtime_host_included=not args.source_only,
            python_runtime_added_when_packaging=not args.source_only),indent=2))


if __name__=='__main__': main()
