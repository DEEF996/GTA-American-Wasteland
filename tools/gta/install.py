"""First-run local importer. Creates a new isolated GTA copy, never patches sources.

The downloadable integration package contains no GTA or THAW game resources.
This installer imports them from the selected local, fingerprint-checked games.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, SimpleQueue
import shutil
import sys
import tempfile
from threading import Event, Thread
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tools/vanilla_thaw')]
import setup_sources
from import_resources import plain_path

FORMAT='gta-thaw-installation-v1'
COPY_DIRECTORIES=('anim','audio','data','models','text','movies')
COPY_FILES=('gta_sa.exe','eax.dll','ogg.dll','vorbis.dll','vorbisFile.dll','stream.ini')
ORIGINAL_VORBIS='a08923479000cec366967fb8259e0920b7aa18859722c7dda1415726bed4774f'
DEPS=(
    dict(name='Ultimate-ASI-Loader-v9.7.4-NoPDB.zip',
        url='https://github.com/ThirteenAG/Ultimate-ASI-Loader/releases/download/v9.7.4/Ultimate-ASI-Loader-NoPDB.zip',
        sha256='14b3a1ad018899571ac9aa01482977f3c6d49e6cba99f552d01c5acacd1315e1',
        members={'dinput8.dll':'vorbisFile.dll'},license='Ultimate-ASI-Loader-LICENSE.txt'),
    dict(name='HighFpsFixes-v1.9.0.zip',
        url='https://github.com/sonochiwa/sa-high-fps-fixes/releases/download/v1.9.0/HighFpsFixes-v1.9.0.zip',
        sha256='538c5651e3a3bc7e9eea2ef4e82e4a96d8c3378c67e9fb47a50d941b65e18191',
        members={'HighFpsFixes.asi':'scripts/HighFpsFixes.asi','HighFpsFixes.ini':'scripts/HighFpsFixes.ini'},
        license='HighFpsFixes-LICENSE.txt'),
    dict(name='III.VC.SA.WindowedMode-v2.2.zip',
        url='https://github.com/ThirteenAG/III.VC.SA.WindowedMode/releases/download/v2.2/III.VC.SA.WindowedMode.zip',
        sha256='68039e974af4d0312281e5802fce8ef385b4291fbfc77a94229542fa57140b2a',
        members={'III.VC.SA.WindowedMode.asi':'scripts/III.VC.SA.WindowedMode.asi'},
        license='WindowedMode-LICENSE.txt'))
ADAPTER_INI='''[performance]
RemoveLegacyFrameDelay=1
[development]
HudPreview=1
CameraPreview=1
PosePreview=1
MovementHoldPreview=1
MovementCommandPreview=1
MovementTrajectoryPreview=1
MovementInteractivePreview=1
'''
LOADER_INI='''[GlobalSets]
LoadPlugins=1
LoadFromScriptsOnly=1
LoadRecursively=0
DontLoadFromDllMain=1
UseD3D8to9=0
DisableCrashDumps=0
'''
WINDOW_INI='''[window]
mode=2
windowedStyle=2
maximized=1
positionX=0
positionY=0
resolutionX=1280
resolutionY=720
[game]
autoPause=1
autoResume=1
'''


class Cancelled(RuntimeError): pass


def check_cancel(stopped):
    if stopped(): raise Cancelled('Installation cancelled. Original games are unchanged.')


def write_json(path, value):
    path=plain_path(path,missing=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                prefix='.'+path.name+'-',delete=False) as stream:
            temporary=Path(stream.name)
            json.dump(value,stream,indent=2,ensure_ascii=True); stream.write('\n')
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path); temporary=None
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def new_destination(destination, roots):
    destination=plain_path(destination,missing=True).resolve()
    if destination.exists():
        raise ValueError('Choose a NEW output folder. Existing directories are never overwritten.')
    if not destination.parent.is_dir():
        raise ValueError('The parent of the new output folder must already exist.')
    for root in roots:
        root=Path(root).resolve(strict=True)
        if destination.is_relative_to(root) or root.is_relative_to(destination):
            raise ValueError('Output must be separate from both original game installations and the mod package.')
    if any(ord(c)<32 or ord(c)>126 for c in str(destination)) or len(str(destination/'thaw_gta/profile'))>110:
        raise ValueError('Use a short output path with ASCII letters, for example D:\\Games\\GTA_THAW.')
    return destination


def base_inventory(source, stopped=lambda:False):
    source=plain_path(source).resolve(strict=True)
    rows=[]
    def add(path):
        check_cancel(stopped); path=plain_path(path).resolve(strict=True)
        if not path.is_file() or not path.is_relative_to(source):
            raise ValueError('Invalid original game input: '+str(path))
        rows.append(dict(path=path.relative_to(source).as_posix(),bytes=path.stat().st_size))
    for name in COPY_FILES: add(source/name)
    for name in COPY_DIRECTORIES:
        base=plain_path(source/name)
        if not base.is_dir(): raise ValueError('Missing GTA directory: '+name)
        for current, directories, files in os.walk(base,followlinks=False):
            check_cancel(stopped)
            for child in directories: plain_path(Path(current)/child)
            for child in sorted(files): add(Path(current)/child)
    return rows


def copy_checked(source, destination, *, stopped=lambda:False, advance=lambda n:None):
    source=plain_path(source).resolve(strict=True)
    destination=plain_path(destination,missing=True)
    destination.parent.mkdir(parents=True,exist_ok=True)
    before=source.stat(); digest=hashlib.sha256(); copied=0
    with source.open('rb') as incoming, destination.open('xb') as output:
        while block:=incoming.read(1024*1024):
            check_cancel(stopped); output.write(block); digest.update(block); copied+=len(block); advance(len(block))
    after=source.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or copied!=before.st_size:
        raise ValueError('Source changed during copy: '+str(source))
    if setup_sources.digest(destination)!=digest.hexdigest():
        raise OSError('Copy checksum differs: '+str(destination))
    return dict(bytes=copied,sha256=digest.hexdigest())


def adapter_build(root):
    directory=root/'build/gta-native'
    manifest=json.loads(plain_path(directory/'adapter-build.json').read_text(encoding='utf-8-sig'))
    adapter=plain_path(directory/'thaw_gta.asi')
    if (manifest.get('format')!='gta-thaw-telemetry-adapter-v1' or
        manifest.get('supported_exe_sha256')!=setup_sources.GTA_SHA256 or
        manifest.get('packet_tests_passed') is not True or
        setup_sources.digest(adapter)!=manifest.get('asi_sha256')):
        raise ValueError('Native adapter build is missing, changed or did not pass its protocol tests.')
    return adapter,manifest


def dependency(spec, cache, *, offline=False, stopped=lambda:False, progress=lambda *a,**k:None):
    cache=plain_path(cache,missing=True); cache.mkdir(parents=True,exist_ok=True)
    path=plain_path(cache/spec['name'],missing=True)
    if path.exists():
        if setup_sources.digest(path)!=spec['sha256']:
            raise ValueError('Downloaded dependency checksum differs: '+path.name)
        return path
    if offline:
        raise ValueError('Offline dependency is missing: '+str(path))
    check_cancel(stopped); progress('Downloading '+spec['name'])
    request=Request(spec['url'],headers={'User-Agent':'GTA-THAW-local-installer/1'})
    temporary=None
    try:
        with urlopen(request,timeout=30) as response:
            final=urlparse(response.geturl())
            if final.scheme!='https' or final.hostname not in ('github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'):
                raise ValueError('Dependency redirected outside official GitHub release hosting')
            with tempfile.NamedTemporaryFile(dir=cache,prefix='.download-',delete=False) as output:
                temporary=Path(output.name); total=0; digest=hashlib.sha256()
                while block:=response.read(1024*1024):
                    check_cancel(stopped); total+=len(block)
                    if total>32*1024*1024: raise ValueError('Dependency archive exceeds its size limit')
                    output.write(block); digest.update(block)
        if digest.hexdigest()!=spec['sha256']:
            raise ValueError('Official dependency download did not match its pinned SHA-256')
        if path.exists(): raise FileExistsError('Dependency destination appeared during download')
        os.rename(temporary,path); temporary=None
        return path
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def deploy_dependencies(game, archives, root):
    cache=game/'thaw_gta'
    original=game/'vorbisFile.dll'
    if setup_sources.digest(original)!=ORIGINAL_VORBIS:
        raise ValueError('Original GTA vorbisFile.dll differs; refusing to replace another loader.')
    os.rename(original,game/'vorbisHooked.dll')
    (game/'scripts').mkdir()
    for spec, archive in zip(DEPS,archives,strict=True):
        if setup_sources.digest(archive)!=spec['sha256']:
            raise ValueError('Dependency changed before installation')
        with zipfile.ZipFile(archive) as bundle:
            names=bundle.namelist()
            for name,target in spec['members'].items():
                if names.count(name)!=1 or bundle.getinfo(name).file_size>16*1024*1024:
                    raise ValueError('Missing, duplicate or oversized dependency member: '+name)
                with (game/target).open('xb') as output: output.write(bundle.read(name))
        copy_checked(root/'tools/gta/licenses'/spec['license'],cache/spec['license'])
    config=configparser.ConfigParser(inline_comment_prefixes=('#',';'))
    path=game/'scripts/HighFpsFixes.ini'; config.read(path,encoding='utf-8-sig')
    config['general']['log']='1'; config['general']['overrideConflictingHooks']='0'
    sections=[s for s in config.sections() if 'fpslimit' in config[s]]
    if len(sections)!=1: raise ValueError('Pinned High FPS Fixes limiter section changed')
    config[sections[0]]['fpslimit']='120'
    with path.open('w',encoding='ascii') as output: config.write(output)
    (game/'vorbisFile.ini').write_text(LOADER_INI,encoding='ascii')
    (game/'scripts/III.VC.SA.WindowedMode.ini').write_text(WINDOW_INI,encoding='ascii')
    (cache/'adapter.ini').write_text(ADAPTER_INI,encoding='ascii')


def install(gta, thaw, destination, *, root=ROOT, dependencies=None, offline=False,
            progress=lambda *a,**k:None, stopped=lambda:False):
    if os.name!='nt' or sys.version_info<(3,12) or sys.maxsize<2**32:
        raise ValueError('Setup needs Windows and the included 64-bit Python runtime (3.12+ for source checkouts).')
    root=plain_path(root).resolve(strict=True)
    adapter,build=adapter_build(root)
    from stock_host import validate_host
    validate_host(root)
    progress('Checking original game versions...'); check_cancel(stopped)
    report=setup_sources.inspect_sources(gta,thaw)
    from import_qb import PINNED_INPUTS
    from prepare_image import native_inputs
    if report['thaw']['rethawed_present'] or any((r['bytes'],r['sha256'])!=PINNED_INPUTS[r['path']] for r in report['thaw']['files']):
        raise ValueError('This adapter needs the fingerprinted original PC THAW build; reTHAWed and other builds are unsupported.')
    source=Path(report['gta']['directory']); thaw_root=Path(report['thaw']['directory'])
    if root.is_relative_to(source) or root.is_relative_to(thaw_root):
        raise ValueError('Move the integration package outside the original game folders before running setup.')
    native_inputs(thaw_root)
    if setup_sources.digest(source/'vorbisFile.dll')!=ORIGINAL_VORBIS:
        raise ValueError('Select the original GTA directory with its original vorbisFile.dll.')
    game=new_destination(destination,(source,thaw_root,root))
    inventory=base_inventory(source,stopped)
    # Current static world+tile caches are bounded separately; allow room for
    # import staging, resource duplication and the configured 2 GiB tile cache.
    from import_resources import inventory as resource_inventory
    resource_bytes=sum((thaw_root/name).stat().st_size for name in resource_inventory(thaw_root))
    required=sum(row['bytes'] for row in inventory)+resource_bytes*2+4*1024**3
    if shutil.disk_usage(game.parent).free<required:
        raise OSError(f'Not enough free space on the output drive. Need approximately {required/1024**3:.1f} GiB.')
    cache_path=plain_path(dependencies or root/'downloads',missing=True).resolve()
    if any(cache_path.is_relative_to(p) for p in (source,thaw_root,game)):
        raise ValueError('Dependency cache must be outside the selected game directories.')
    archives=[dependency(spec,cache_path,offline=offline,stopped=stopped,progress=progress) for spec in DEPS]
    check_cancel(stopped)
    game.mkdir(); cache=game/'thaw_gta'; cache.mkdir()
    status=dict(format=FORMAT,complete=False,game=str(game),source=str(source),thaw_source=str(thaw_root),
                source_files_modified=False,game_assets_private=True,stage='copying')
    status_path=cache/'installation.json'; write_json(status_path,status)
    try:
        total=sum(row['bytes'] for row in inventory); copied=0; last_notice=0
        def advance(count):
            nonlocal copied,last_notice
            copied+=count
            if time.monotonic()-last_notice>=.2:
                progress('Copying GTA to the isolated folder...',done=copied,total=total); last_notice=time.monotonic()
        rows=[]
        for item in inventory:
            info=copy_checked(source/item['path'],game/item['path'],stopped=stopped,advance=advance)
            rows.append(dict(path=item['path'],**info))
        if setup_sources.digest(game/'gta_sa.exe')!=setup_sources.GTA_SHA256:
            raise ValueError('GTA executable changed after source selection')
        write_json(cache/'base-copy.json',dict(format='gta-thaw-isolated-copy-v1',source=str(source),game=str(game),files=rows))
        sources=cache/'sources.json'; setup_sources.save_configuration(report,sources)
        from import_qb import import_cache
        from import_assets import prepare as assets
        from import_profile import prepare as profile
        from import_resources import prepare as resources
        from prepare_image import prepare as image
        from hud_assets import prepare as hud
        from terrain_audio import prepare as terrain_sounds
        from settings_menu import default_settings,save_settings
        phases=(('Preparing original THAW runtime',lambda:image(sources,cache/'stock-worker')),
            ('Importing original THAW scripts',lambda:import_cache(sources,cache/'stock-qb')),
            ('Importing original profile and statistics',lambda:profile(sources,cache/'stock-profile')),
            ('Importing skeleton, animations and board',lambda:assets(sources,cache/'stock-assets')),
            ('Importing original HUD and sound resources',lambda:resources(sources,cache/'stock-common-audio-resources')),
            ('Importing original wood, metal and fence sounds',lambda:terrain_sounds(thaw_root,cache/'stock-terrain-audio')),
            ('Building private HUD atlases',lambda:hud(cache/'stock-common-audio-resources/manifest.json',None,cache/'stock-hud')))
        for label,action in phases:
            check_cancel(stopped); status['stage']=label; write_json(status_path,status)
            progress(label+'... (Cancel stops after this operation)'); action()
        check_cancel(stopped); progress('Installing adapter, 120 FPS fixes and borderless mode...')
        deploy_dependencies(game,archives,root)
        copy_checked(adapter,game/'scripts/thaw_gta.asi')
        if setup_sources.digest(game/'scripts/thaw_gta.asi')!=build['asi_sha256']:
            raise ValueError('Native adapter changed during installation')
        save_settings(cache/'settings.json',default_settings())
        from release_version import VERSION
        status.update(complete=True,stage='ready',adapter_sha256=build['asi_sha256'],fps_target=120,
            map_prepared=False,whole_map_prepared_on_first_game_launch=True,version=VERSION)
        write_json(status_path,status)
        write_json(root/'installation.json',dict(format=FORMAT,complete=True,game=str(game)))
        progress('Ready. Start Run-GTA-THAW.cmd. The first launch prepares the map for grinding.',done=1,total=1)
        return status
    except BaseException as error:
        status.update(complete=False,error=str(error),cancelled=isinstance(error,Cancelled))
        write_json(status_path,status)
        raise


def gui():
    import tkinter as tk
    from tkinter import filedialog,messagebox,ttk
    window=tk.Tk(); window.title('GTA THAW - first-run setup'); window.geometry('790x390'); window.minsize(640,390)
    window.columnconfigure(1,weight=1)
    tk.Label(window,text='Select your original GTA and THAW installations. Setup creates a NEW isolated GTA copy.',
        wraplength=720,anchor='w',justify='left').grid(row=0,column=0,columnspan=3,sticky='ew',padx=15,pady=15)
    values=[tk.StringVar() for _ in range(3)]
    controls=[]
    def browse(index):
        selected=filedialog.askdirectory(parent=window,title=('Original GTA folder','Original stock THAW folder','Parent folder for the new GTA THAW copy')[index])
        if selected: values[index].set(str(Path(selected)/'GTA_THAW') if index==2 else selected)
    for i,label in enumerate(('Original GTA San Andreas','Original Tony Hawk American Wasteland','New isolated GTA THAW folder')):
        tk.Label(window,text=label,anchor='w').grid(row=i+1,column=0,padx=15,pady=7,sticky='w')
        entry=ttk.Entry(window,textvariable=values[i]); entry.grid(row=i+1,column=1,sticky='ew',pady=7); controls.append(entry)
        button=ttk.Button(window,text='Browse...',command=lambda i=i:browse(i)); button.grid(row=i+1,column=2,padx=15); controls.append(button)
    message=tk.StringVar(value='The integration package includes its runtime; no Python installation is needed. Original games stay unchanged.')
    tk.Label(window,textvariable=message,wraplength=740,anchor='w',justify='left').grid(row=4,column=0,columnspan=3,padx=15,pady=12,sticky='ew')
    progress_bar=ttk.Progressbar(window,mode='indeterminate'); progress_bar.grid(row=5,column=0,columnspan=3,padx=15,pady=10,sticky='ew')
    queue=SimpleQueue(); cancelled=Event(); worker=None
    def update(text,**details): queue.put(('progress',text,details))
    def start():
        nonlocal worker
        if not all(v.get().strip() for v in values):
            messagebox.showerror('GTA THAW','Choose both source folders and a new output folder.',parent=window); return
        selected=[v.get().strip() for v in values]; cancelled.clear()
        for control in controls: control.configure(state='disabled')
        install_button.configure(state='disabled'); cancel_button.configure(state='normal')
        progress_bar.configure(mode='indeterminate'); progress_bar.start()
        def run():
            try: queue.put(('done',install(*selected,progress=update,stopped=cancelled.is_set),{}))
            except Exception as error: queue.put(('error',str(error),{}))
        worker=Thread(target=run,name='gta-thaw-installer'); worker.start()
    def cancel():
        cancelled.set(); message.set('Cancelling safely. Current imports finish before stopping; original games remain unchanged.')
        cancel_button.configure(state='disabled')
    def close():
        if worker and worker.is_alive(): cancel()
        else: window.destroy()
    def tick():
        try:
            while True:
                kind,text,details=queue.get_nowait()
                if kind=='progress':
                    if cancelled.is_set(): continue
                    message.set(text)
                    if details.get('total'):
                        progress_bar.stop(); progress_bar.configure(mode='determinate',maximum=details['total'],value=details['done'])
                    else: progress_bar.configure(mode='indeterminate'); progress_bar.start()
                else:
                    progress_bar.stop(); cancel_button.configure(state='disabled'); install_button.configure(state='normal')
                    for control in controls: control.configure(state='normal')
                    if kind=='done':
                        message.set('Installed. Open Run-GTA-THAW.cmd, load a game and use J. F8 opens settings.')
                        messagebox.showinfo('GTA THAW','Local installation prepared. The first launch builds the grind map cache.',parent=window)
                    else:
                        message.set('Installation did not finish. Any partial output is kept and will not launch.')
                        messagebox.showerror('GTA THAW',str(text)+'\n\nPartial output, if created, is kept in the selected folder. Choose a new folder to retry.',parent=window)
        except Empty: pass
        window.after(100,tick)
    install_button=ttk.Button(window,text='Install local mod',command=start); install_button.grid(row=6,column=1,sticky='e',pady=12)
    cancel_button=ttk.Button(window,text='Cancel',command=cancel,state='disabled'); cancel_button.grid(row=6,column=2,padx=15)
    tk.Label(window,text='Downloads three fixed open-source plugins from GitHub. No game files are downloaded or uploaded.',
        wraplength=740,anchor='w').grid(row=7,column=0,columnspan=3,padx=15,sticky='ew')
    window.protocol('WM_DELETE_WINDOW',close); window.after(100,tick); window.mainloop()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gta',type=Path); parser.add_argument('--thaw',type=Path)
    parser.add_argument('--output',type=Path); parser.add_argument('--dependencies',type=Path)
    parser.add_argument('--offline',action='store_true'); parser.add_argument('--gui',action='store_true')
    args=parser.parse_args()
    if args.gui or not any((args.gta,args.thaw,args.output)): gui(); return
    if not all((args.gta,args.thaw,args.output)): parser.error('Use --gta, --thaw and a NEW --output together.')
    try:
        install(args.gta,args.thaw,args.output,dependencies=args.dependencies,offline=args.offline,
            progress=lambda phase,**details:print(json.dumps(dict(phase=phase,**details)),flush=True))
    except (OSError,ValueError,RuntimeError) as error:
        print(str(error),file=sys.stderr); raise SystemExit(1)


if __name__=='__main__': main()
