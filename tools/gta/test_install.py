import hashlib
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

import install


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        self.original=self.root/'original'; self.original.mkdir()

    def test_output_cannot_replace_existing_or_nest_under_sources(self):
        output=self.root/'new-copy'
        self.assertEqual(install.new_destination(output,(self.original,)),output)
        with self.assertRaisesRegex(ValueError,'NEW'):
            install.new_destination(self.original,(self.original,))
        with self.assertRaisesRegex(ValueError,'separate'):
            install.new_destination(self.original/'new-copy',(self.original,))

    def test_copy_cancel_never_changes_source_and_refuses_existing_target(self):
        source=self.original/'large.bin'; source.write_bytes(b'x'*(3*1024*1024))
        target=self.root/'copy.bin'; count=0
        def advance(n):
            nonlocal count
            count+=n
        with self.assertRaises(install.Cancelled):
            install.copy_checked(source,target,stopped=lambda:count>=1024*1024,advance=advance)
        self.assertEqual(source.stat().st_size,3*1024*1024)
        self.assertEqual(target.stat().st_size,1024*1024)
        with self.assertRaises(FileExistsError): install.copy_checked(source,target)
        self.assertEqual(target.stat().st_size,1024*1024)

    def test_copy_detects_changed_original_and_hashes_result(self):
        source=self.original/'one'; source.write_bytes(b'original')
        target=self.root/'copy'
        result=install.copy_checked(source,target)
        self.assertEqual(result,dict(bytes=8,sha256=hashlib.sha256(b'original').hexdigest()))
        other=self.root/'other'
        with self.assertRaisesRegex(ValueError,'changed'):
            install.copy_checked(source,other,advance=lambda _:source.write_bytes(b'changed source'))

    def test_base_inventory_excludes_installed_plugins(self):
        for name in install.COPY_FILES: (self.original/name).write_bytes(b'base')
        for name in install.COPY_DIRECTORIES:
            (self.original/name).mkdir(); (self.original/name/'base.dat').write_bytes(b'base')
        (self.original/'scripts').mkdir(); (self.original/'scripts/other.asi').write_bytes(b'plugin')
        (self.original/'unknown.dll').write_bytes(b'plugin')
        result=install.base_inventory(self.original)
        self.assertEqual(len(result),len(install.COPY_FILES)+len(install.COPY_DIRECTORIES))
        self.assertFalse(any('other' in item['path'] or 'unknown' in item['path'] for item in result))

    def test_dependency_cache_requires_pinned_hash_and_offline_fails_closed(self):
        cache=self.root/'downloads'; cache.mkdir()
        spec=dict(name='plugin.zip',sha256=hashlib.sha256(b'trusted').hexdigest())
        with self.assertRaisesRegex(ValueError,'missing'): install.dependency(spec,cache,offline=True)
        path=cache/spec['name']; path.write_bytes(b'untrusted')
        with self.assertRaisesRegex(ValueError,'checksum'): install.dependency(spec,cache,offline=True)
        path.write_bytes(b'trusted'); self.assertEqual(install.dependency(spec,cache,offline=True),path)

    def test_json_failure_keeps_previous_file(self):
        path=self.root/'state.json'; install.write_json(path,dict(complete=False))
        with patch.object(install.os,'replace',side_effect=OSError('blocked')):
            with self.assertRaises(OSError): install.write_json(path,dict(complete=True))
        self.assertEqual(json.loads(path.read_text()),dict(complete=False))
        self.assertEqual(list(self.root.glob('.state.json-*')),[])

    def test_deployment_extracts_only_named_members_and_restores_settings(self):
        game=self.root/'game'; game.mkdir(); (game/'thaw_gta').mkdir()
        original=b'original vorbis'; (game/'vorbisFile.dll').write_bytes(original)
        specs=[]; archives=[]
        expected=[('loader.zip',{'proxy.dll':'vorbisFile.dll'}),
            ('fps.zip',{'fix.asi':'scripts/HighFpsFixes.asi','config.ini':'scripts/HighFpsFixes.ini'}),
            ('window.zip',{'window.asi':'scripts/III.VC.SA.WindowedMode.asi'})]
        licenses=self.root/'tools/gta/licenses'; licenses.mkdir(parents=True)
        for name,members in expected:
            archive=self.root/name
            with zipfile.ZipFile(archive,'w') as bundle:
                for source in members:
                    data=b'[general]\nlog=0\noverrideConflictingHooks=1\n[performance]\nfpsLimit=0\n' if source.endswith('.ini') else b'owned dependency'
                    bundle.writestr(source,data)
                bundle.writestr('../escape',b'not selected')
            license=name+'.txt'; (licenses/license).write_text('MIT fixture')
            specs.append(dict(name=name,members=members,license=license,sha256=install.setup_sources.digest(archive)))
            archives.append(archive)
        with patch.object(install,'DEPS',specs),patch.object(install,'ORIGINAL_VORBIS',hashlib.sha256(original).hexdigest()):
            install.deploy_dependencies(game,archives,self.root)
        self.assertEqual((game/'vorbisHooked.dll').read_bytes(),original)
        self.assertFalse((self.root/'escape').exists())
        self.assertIn('fpslimit = 120',(game/'scripts/HighFpsFixes.ini').read_text())
        self.assertIn('mode=2',(game/'scripts/III.VC.SA.WindowedMode.ini').read_text())


class InstallationFlowTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        self.package=self.root/'package'; self.package.mkdir()
        self.gta=self.root/'original-gta'; self.gta.mkdir()
        self.thaw=self.root/'original-thaw'; self.thaw.mkdir()
        self.output=self.root/'GTA_THAW'
        for name in install.COPY_FILES: (self.gta/name).write_bytes(name.encode())
        for name in install.COPY_DIRECTORIES:
            (self.gta/name).mkdir(); (self.gta/name/'fixture.dat').write_bytes(name.encode())
        for name in install.setup_sources.THAW_REQUIRED:
            path=self.thaw/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(name.encode())
        self.before={str(p):p.read_bytes() for directory in (self.gta,self.thaw) for p in directory.rglob('*') if p.is_file()}
        self.report=dict(format='gta-thaw-local-sources-v1',
            gta=dict(directory=str(self.gta),files=[]),
            thaw=dict(directory=str(self.thaw),rethawed_present=False,
                files=install.setup_sources.files(self.thaw,install.setup_sources.THAW_REQUIRED)))
        adapter=self.package/'build/thaw_gta.asi'; adapter.parent.mkdir(); adapter.write_bytes(b'our synthetic ASI')
        self.calls=[]; self.archives=[]; self.specs=[]
        licenses=self.package/'tools/gta/licenses'; licenses.mkdir(parents=True)
        for number,spec in enumerate(install.DEPS):
            archive=self.package/spec['name']
            with zipfile.ZipFile(archive,'w') as bundle:
                for member in spec['members']:
                    payload=b'[general]\nlog=0\noverrideConflictingHooks=1\n[limit]\nfpsLimit=0\n' if member.endswith('.ini') else b'plugin bytes'
                    bundle.writestr(member,payload)
            (licenses/spec['license']).write_text('MIT test license')
            self.specs.append(dict(spec,sha256=install.setup_sources.digest(archive)))
            self.archives.append(archive)
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        def patcher(target,value): self.stack.enter_context(patch(target,value))
        patcher('install.DEPS',self.specs)
        patcher('install.setup_sources.inspect_sources',lambda *_:self.report)
        patcher('install.setup_sources.GTA_SHA256',install.setup_sources.digest(self.gta/'gta_sa.exe'))
        patcher('install.ORIGINAL_VORBIS',install.setup_sources.digest(self.gta/'vorbisFile.dll'))
        patcher('install.adapter_build',lambda *_:(adapter,dict(asi_sha256=install.setup_sources.digest(adapter))))
        patcher('stock_host.validate_host',lambda *_:(self.package/'gta-thaw-runtime.exe',{}))
        patcher('install.shutil.disk_usage',lambda _:SimpleNamespace(free=100*1024**3))
        patcher('install.dependency',lambda spec,*a,**k:self.archives[self.specs.index(spec)])
        patcher('import_qb.PINNED_INPUTS',{r['path']:(r['bytes'],r['sha256']) for r in self.report['thaw']['files']})
        patcher('prepare_image.native_inputs',lambda root:self.calls.append(('pins',root)))
        patcher('import_resources.inventory',lambda _:['THAW.exe'])
        def importer(label):
            def run(sources,output):
                self.calls.append((label,sources,output))
                self.assertEqual(sources,self.output/'thaw_gta/sources.json')
                self.assertTrue(sources.is_file())
                output.mkdir(); (output/'manifest.json').write_text('{}')
            return run
        for module,attribute,label in (('prepare_image','prepare','worker'),('import_qb','import_cache','qb'),
            ('import_profile','prepare','profile'),('import_assets','prepare','assets'),('import_resources','prepare','resources')):
            patcher(module+'.'+attribute,importer(label))
        def terrain_sounds(source,output):
            self.assertEqual(source,self.thaw)
            self.assertEqual(output,self.output/'thaw_gta/stock-terrain-audio')
            output.mkdir(); (output/'manifest.json').write_text('{}')
            self.calls.append(('terrain_audio',source,output))
        patcher('terrain_audio.prepare',terrain_sounds)
        def hud(resources,layout,output):
            self.calls.append(('hud',resources,layout,output))
            self.assertEqual(resources,self.output/'thaw_gta/stock-common-audio-resources/manifest.json')
            self.assertIsNone(layout)
            self.assertTrue(resources.is_file()); output.mkdir(); (output/'manifest.json').write_text('{}')
        patcher('hud_assets.prepare',hud)

    def assert_sources_unchanged(self):
        self.assertEqual({str(p):p.read_bytes() for directory in (self.gta,self.thaw)
                         for p in directory.rglob('*') if p.is_file()},self.before)

    def test_all_phases_then_complete_pointer_and_defaults(self):
        result=install.install(self.gta,self.thaw,self.output,root=self.package,offline=True)
        self.assertTrue(result['complete']); self.assertFalse(result['map_prepared'])
        self.assertEqual([row[0] for row in self.calls],['pins','worker','qb','profile','assets','resources','terrain_audio','hud'])
        self.assertEqual(json.loads((self.package/'installation.json').read_text())['game'],str(self.output))
        self.assertTrue(json.loads((self.output/'thaw_gta/installation.json').read_text())['complete'])
        self.assertTrue(json.loads((self.output/'thaw_gta/settings.json').read_text())['aerial_flips'])
        self.assertTrue((self.output/'scripts/thaw_gta.asi').is_file())
        self.assert_sources_unchanged()

    def test_cancellation_between_imports_leaves_incomplete_never_publishes(self):
        cancelled=False
        def progress(phase,**details):
            nonlocal cancelled
            if phase.startswith('Importing original profile'): cancelled=True
        with self.assertRaises(install.Cancelled):
            install.install(self.gta,self.thaw,self.output,root=self.package,offline=True,
                progress=progress,stopped=lambda:cancelled)
        self.assertEqual([row[0] for row in self.calls],['pins','worker','qb','profile'])
        state=json.loads((self.output/'thaw_gta/installation.json').read_text())
        self.assertFalse(state['complete']); self.assertTrue(state['cancelled'])
        self.assertFalse((self.package/'installation.json').exists())
        self.assertFalse((self.output/'scripts/thaw_gta.asi').exists())
        self.assert_sources_unchanged()

    def test_no_space_rejects_before_new_clone_or_dependencies(self):
        with patch('install.shutil.disk_usage',return_value=SimpleNamespace(free=0)),patch('install.dependency') as dependency:
            with self.assertRaisesRegex(OSError,'free space'):
                install.install(self.gta,self.thaw,self.output,root=self.package,offline=True)
        dependency.assert_not_called()
        self.assertFalse(self.output.exists()); self.assert_sources_unchanged()

    def test_missing_rust_host_rejects_before_copying_or_downloading(self):
        with patch('stock_host.validate_host',side_effect=ValueError('Rust runtime host is missing')), \
                patch('install.dependency') as dependency:
            with self.assertRaisesRegex(ValueError,'Rust runtime host'):
                install.install(self.gta,self.thaw,self.output,root=self.package,offline=True)
        dependency.assert_not_called()
        self.assertFalse(self.output.exists()); self.assert_sources_unchanged()

    def test_package_inside_original_is_rejected_even_with_external_download_cache(self):
        with self.assertRaisesRegex(ValueError,'outside the original'):
            install.install(self.gta,self.thaw,self.output,root=self.gta,dependencies=self.package,offline=True)
        self.assertFalse(self.output.exists()); self.assert_sources_unchanged()


if __name__=='__main__': unittest.main()
