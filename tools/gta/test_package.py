import json
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
import subprocess
import shutil
import sys
import sysconfig
import tomllib
from unittest.mock import patch

import package


class PackageTests(unittest.TestCase):
    def test_source_selection_excludes_game_material_and_has_runtime_dependencies(self):
        files=package.selected_files(source_only=True)
        self.assertTrue({'tools/gta/install.py','tools/gta/settings_menu.py',
            'tools/vanilla_thaw/probe_loader.py','tools/vanilla_thaw/skater_bootstrap.json',
            'tools/vanilla_thaw/vehicle_native_pins.json','tools/gta/vehicle_input.py',
            'tools/gta/atomic_store.py','tools/gta/transport_native/atomic_store.cpp',
            'tools/gta/transport_native/atomic_store_test.cpp','tools/gta/transport_native/build.ps1',
            'tools/assets_models.py','tools/waw/export_hud.py','tools/waw/retarget.py','Configure-GTA-THAW.cmd'} <= files.keys())
        self.assertEqual({name.removeprefix('tools/gta/host/') for name in files if name.startswith('tools/gta/host/')},
                         set(package.HOST_SOURCE))
        self.assertEqual({name.removeprefix('tools/gta/runtime_host/') for name in files if name.startswith('tools/gta/runtime_host/')},
                         set(package.RUNTIME_HOST_SOURCE))
        forbidden={'.exe','.dll','.asi','.bin','.wpc','.pak','.pab','.img','.db','.sqlite','.zip'}
        self.assertFalse(any(Path(name).suffix in forbidden for name in files))
        self.assertFalse(any(name.startswith(('research/','assets/','local/','downloads/','build/')) for name in files))
        self.assertEqual({Path(name).name for name in files if name.endswith('.json')},package.PIN_METADATA)

    def test_standalone_host_allowlist_excludes_dependencies_builds_and_old_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            required=[*package.ROOT_FILES,*package.PUBLIC_DOCS,'docs/gta-first-run.md']
            required += ['tools/'+name for name in package.COMMON]
            required += ['tools/waw/'+name for name in package.REUSED]
            required += ['tools/gta/host/'+name for name in package.HOST_SOURCE]
            required += ['tools/gta/runtime_host/'+name for name in package.RUNTIME_HOST_SOURCE]
            excluded=['Cargo.toml','Cargo.lock','src/main.rs','tools/gta/host/extra.rs',
                      'tools/gta/host/vendor/dependency/README.md','tools/gta/host/vendor/dependency/src/lib.rs',
                      'tools/gta/host/target/generated.rs','tools/gta/host/target/report.md',
                      'tools/gta/host/private-sdk/helper.py','tools/gta/host/data/assets.bin',
                      'tools/gta/host/.cargo/config.toml','tools/gta/runtime_host/target/leaked.rs',
                      'tools/gta/runtime_host/vendor/README.md','tools/gta/runtime_host/private-sdk/helper.py']
            for name in required+excluded:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
            selected=package.selected_files(root,source_only=True)
            self.assertTrue(set(required)<=selected.keys())
            self.assertFalse(set(excluded)&selected.keys())
            (root/'tools/gta/host/src/gta_bridge.rs').unlink()
            with self.assertRaises((ValueError,FileNotFoundError)):
                package.selected_files(root,source_only=True)

    def test_integration_requires_validated_atomic_transport_and_both_build_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            required=[*package.ROOT_FILES,*package.PUBLIC_DOCS,'docs/gta-first-run.md']
            required += ['tools/'+name for name in package.COMMON]
            required += ['tools/waw/'+name for name in package.REUSED]
            required += ['tools/gta/host/'+name for name in package.HOST_SOURCE]
            required += ['tools/gta/runtime_host/'+name for name in package.RUNTIME_HOST_SOURCE]
            required += ['build/gta-native/thaw_gta.asi','build/gta-native/adapter-build.json',
                         'build/gta-runtime/gta-thaw-runtime.exe','build/gta-runtime/host-build.json',
                         'build/gta-runtime/gta-thaw-tls.dll',
                         'build/gta-transport/thaw-gta-atomic.dll','build/gta-transport/transport-build.json',
                         'build/gta-launcher/GTA American Wasteland.exe','build/gta-launcher/launcher-build.json']
            for name in required:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
            with patch.object(package,'adapter_build'),patch('stock_host.validate_host'), \
                    patch('atomic_store.validate_build') as validate, patch('launcher_build.validate_build') as launcher:
                files=package.selected_files(root)
                validate.assert_called_once_with(root.resolve())
                launcher.assert_called_once_with(root.resolve())
                self.assertIn('GTA American Wasteland.exe',files)
                self.assertTrue({'build/gta-transport/thaw-gta-atomic.dll',
                                 'build/gta-transport/transport-build.json'} <= files.keys())
                validate.side_effect=ValueError('atomic DLL/source hash mismatch')
                with self.assertRaisesRegex(ValueError,'hash mismatch'):
                    package.selected_files(root)
                validate.side_effect=None
                (root/'build/gta-transport/thaw-gta-atomic.dll').unlink()
                with self.assertRaises((ValueError,FileNotFoundError)):
                    package.selected_files(root)
                validate.reset_mock()
                self.assertFalse(any(name.startswith('build/') for name in
                                     package.selected_files(root,source_only=True)))
                validate.assert_not_called()

    def test_manifest_follows_explicit_selection_and_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'owned.py'; source.write_text('print("own source")\n')
            output=root/'package'
            with patch.object(package,'selected_files',return_value={'tools/owned.py':source}):
                result=package.create(output,root=root,source_only=True)
                self.assertEqual(result['files'],1)
                manifest=json.loads((output/'PACKAGE_MANIFEST.json').read_text())
                self.assertFalse(manifest['original_game_assets_included'])
                self.assertEqual(manifest['version'],package.VERSION)
                self.assertEqual(manifest['files'][0]['path'],'tools/owned.py')
                with self.assertRaisesRegex(ValueError,'new directory'):
                    package.create(output,root=root,source_only=True)
                self.assertEqual((output/'tools/owned.py').read_bytes(),source.read_bytes())
                self.assertIn('**/target/',(output/'.gitignore').read_text())

    def test_integration_stages_private_python_and_manifest_hashes_every_runtime_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'owned.py'; source.write_text('our code')
            output=root/'package'; cache=root/'downloads'
            def stage(destination,actual_cache,*,offline):
                self.assertEqual(destination,output/'runtime/python'); self.assertEqual(actual_cache,cache)
                self.assertTrue(offline)
                destination.mkdir(parents=True)
                rows=[]
                for name,data in (('python.exe',b'synthetic private interpreter'),('LICENSE.txt',b'PSF license fixture')):
                    (destination/name).write_bytes(data)
                    rows.append(dict(path=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
                return dict(version='test',architecture='amd64',source_url='https://www.python.org/test',
                            source_sha256='0'*64,files=rows)
            with patch.object(package,'selected_files',return_value={'tools/owned.py':source}), \
                    patch('portable_python.stage',side_effect=stage) as bundled:
                package.create(output,root=root,python_cache=cache,offline=True)
            bundled.assert_called_once()
            manifest=json.loads((output/'PACKAGE_MANIFEST.json').read_text())
            self.assertTrue(manifest['third_party_binaries_included'])
            self.assertFalse(manifest['external_python_installation_required'])
            self.assertFalse(manifest['original_game_executables_included'])
            self.assertEqual(manifest['python_runtime']['architecture'],'amd64')
            self.assertTrue((output/'PORTABLE_RUNTIME_REQUIRED').is_file())
            for row in manifest['files']:
                data=(output/row['path']).read_bytes()
                self.assertEqual((len(data),hashlib.sha256(data).hexdigest()),(row['bytes'],row['sha256']))

    def test_source_package_does_not_stage_a_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'owned.py'; source.write_text('our code')
            with patch.object(package,'selected_files',return_value={'tools/owned.py':source}), \
                    patch('portable_python.stage') as bundled:
                package.create(root/'source',root=root,source_only=True)
            bundled.assert_not_called()
            manifest=json.loads((root/'source/PACKAGE_MANIFEST.json').read_text())
            self.assertIsNone(manifest['python_runtime'])
            self.assertFalse(manifest['third_party_binaries_included'])
            self.assertFalse((root/'source/PORTABLE_RUNTIME_REQUIRED').exists())

    def test_private_runtime_download_cannot_be_placed_inside_the_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); output=root/'package'
            with patch.object(package,'selected_files',return_value={}),patch('portable_python.stage') as bundled:
                with self.assertRaisesRegex(ValueError,'outside the package'):
                    package.create(output,root=root,python_cache=output/'downloads')
            bundled.assert_not_called(); self.assertFalse(output.exists())

    @unittest.skipUnless(os.name=='nt','Windows command launchers')
    def test_launchers_use_local_python_without_path_python_and_preserve_exit_codes(self):
        # This checks launcher selection and flags. Official runtime extraction
        # and its dependency closure have separate portable_python tests.
        python=Path(getattr(sys,'_base_executable',sys.executable))
        dll=python.parent/f'python{sys.version_info.major}{sys.version_info.minor}.dll'
        if not dll.is_file(): self.skipTest('Test interpreter does not expose its adjacent Windows DLL')
        with tempfile.TemporaryDirectory(prefix='gta thaw launcher ') as directory:
            root=Path(directory); runtime=root/'runtime/python'; runtime.mkdir(parents=True)
            shutil.copy2(python,runtime/'python.exe')
            for source in python.parent.glob('*.dll'): shutil.copy2(source,runtime/source.name)
            (runtime/f'python{sys.version_info.major}{sys.version_info.minor}._pth').write_text(
                str(Path(sysconfig.get_path('stdlib')).resolve())+'\n',encoding='utf-8')
            (root/'PORTABLE_RUNTIME_REQUIRED').write_text('fixture')
            scripts=root/'tools/gta'; scripts.mkdir(parents=True)
            environment=dict(os.environ,PATH=str(Path(os.environ['SystemRoot'])/'System32'),
                             PYTHONHOME='Z:\\not-an-interpreter',PYTHONPATH='Z:\\untrusted-modules')
            probe=('import json,sys\nprint(json.dumps({"executable":sys.executable,"args":sys.argv[1:],'
                   '"isolated":sys.flags.isolated,"ignore_environment":sys.flags.ignore_environment,'
                   '"no_user_site":sys.flags.no_user_site,"dont_write_bytecode":sys.dont_write_bytecode}))\n'
                   'raise SystemExit(int(sys.argv[1]))\n')
            for launcher,script in (('Run-GTA-THAW.cmd','launch_playtest.py'),('Configure-GTA-THAW.cmd','install.py')):
                shutil.copy2(package.ROOT/launcher,root/launcher); (scripts/script).write_text(probe)
                for exit_code in (0,37):
                    command=f'"{os.environ["COMSPEC"]}" /d /s /c ""{root/launcher}" {exit_code} "argument with spaces""'
                    result=subprocess.run(command,
                        env=environment,cwd=root,capture_output=True,text=True,input='\n',timeout=15)
                    self.assertEqual(result.returncode,exit_code,result.stderr+'\n'+result.stdout)
                    row=json.loads(next(line for line in result.stdout.splitlines() if line.startswith('{')))
                    self.assertEqual(Path(row['executable']).resolve(),(runtime/'python.exe').resolve())
                    self.assertEqual(row['args'],[str(exit_code),'argument with spaces'])
                    self.assertTrue(all(row[key] for key in ('isolated','ignore_environment','no_user_site','dont_write_bytecode')))
            (runtime/'python.exe').unlink()
            command=f'"{os.environ["COMSPEC"]}" /d /s /c ""{root/"Run-GTA-THAW.cmd"}""'
            result=subprocess.run(command,
                env=environment,cwd=root,capture_output=True,text=True,input='\n',timeout=15)
            self.assertEqual(result.returncode,1)
            self.assertIn('bundled runtime is missing',result.stdout)

    def test_fresh_source_package_imports_runtime_without_workspace_or_site_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'source'
            package.create(output,source_only=True)
            host=output/'tools/gta/host'
            cargo=tomllib.loads((host/'Cargo.toml').read_text())
            lock=tomllib.loads((host/'Cargo.lock').read_text())
            self.assertEqual(cargo['package']['name'],'gta-thaw-host')
            self.assertFalse(cargo['package']['publish'])
            self.assertTrue(any(row['name']=='gta-thaw-host' and row['version']==cargo['package']['version']
                                for row in lock['package']))
            self.assertIn('mod gta_bridge;',(host/'src/main.rs').read_text())
            self.assertTrue((host/'src/gta_bridge.rs').is_file())
            runtime_host=output/'tools/gta/runtime_host'
            runtime_cargo=tomllib.loads((runtime_host/'Cargo.toml').read_text())
            runtime_lock=tomllib.loads((runtime_host/'Cargo.lock').read_text())
            self.assertEqual(runtime_cargo['package']['name'],'gta-thaw-runtime')
            self.assertFalse(runtime_cargo['package']['publish'])
            self.assertIn('workspace',runtime_cargo)
            self.assertTrue(any(row['name']=='gta-thaw-runtime' for row in runtime_lock['package']))
            self.assertIn('mod image;',(runtime_host/'src/main.rs').read_text())
            self.assertTrue((runtime_host/'src/image.rs').is_file())
            self.assertTrue((runtime_host/'src/file_events.rs').is_file())
            self.assertTrue((runtime_host/'tls.c').is_file())
            code=('import sys, hashlib; from pathlib import Path; '
                  'sys.path.insert(0,str(Path.cwd()/"tools/gta")); '
                  'import install, live_session, hud_assets, launch_playtest, retarget_pose, terrain_audio, vehicle_input, probe_vehicle_world; '
                  'assert terrain_audio.pins()["native_count"]==144; '
                  'assert hashlib.sha256(Path(probe_vehicle_world.__file__).with_name("vehicle_native_pins.json").read_bytes()).hexdigest()==probe_vehicle_world.METADATA_SHA; '
                  'assert all(Path(m.__file__).is_relative_to(Path.cwd()) '
                  'for m in (install,live_session,hud_assets,launch_playtest,retarget_pose)); '
                  'print("isolated imports passed")')
            result=subprocess.run([sys.executable,'-I','-S','-B','-c',code],cwd=output,
                                  capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('isolated imports passed',result.stdout)


if __name__=='__main__': unittest.main()
