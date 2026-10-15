"""Map-input preparation uses tiny original-format fixtures, never a game."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import geometry
import global_rails
import map_preparation as m
from file_hash_cache import file_stamp
from test_geometry import col_fixture, img_fixture


class MapPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.game = self.root / 'game'
        self.cache = self.game / 'thaw_gta'
        self.scratch = self.root / 'scratch'
        for folder in ('data', 'models', 'thaw_gta'):
            (self.game / folder).mkdir(parents=True)
        (self.cache / 'base-copy.json').write_text(json.dumps(dict(
            format='gta-thaw-isolated-copy-v1', game=str(self.game), source=str(self.root / 'original'))))
        (self.game / 'data/default.dat').write_text('IDE data/objects.ide\n')
        (self.game / 'data/gta.dat').write_text('IPL data/base.ipl\n')
        (self.game / 'data/object.dat').write_text('; no moving model definitions\n')
        (self.game / 'data/objects.ide').write_text('objs\n1000, fixture, tex, 150, 0\nend\n')
        self.placement = self.game / 'data/base.ipl'
        self.placement.write_text('inst\n1000,fixture,0,0,0,0,0,0,0,1,-1\nend\n')
        (self.game / 'models/gta3.img').write_bytes(img_fixture([('fixture.col', col_fixture())]))
        (self.game / 'models/gta_int.img').write_bytes(img_fixture([]))

    def prepare(self, **kwargs):
        return m.prepare(self.game, self.scratch, **kwargs)

    def changed_source(self):
        self.placement.write_text(self.placement.read_text().replace('fixture,0,0,0,0', 'fixture,0,4,0,0'))

    def test_first_prepare_publishes_valid_pair_then_reuses_it_without_rebuild(self):
        progress = []
        first = self.prepare(progress=lambda stage, **kw: progress.append(stage))
        self.assertTrue(first['changed'])
        database = m.database_path(self.game)
        self.assertEqual(database, Path(first['database']))
        self.assertEqual(m._pair(database)[0]['world_sha256'], first['world_sha256'])
        self.assertEqual(progress[0], 'checking_sources')
        self.assertEqual(progress[-1], 'map_prepared')
        self.assertIn('building_rails', progress)
        with patch.object(geometry, 'build_world', side_effect=AssertionError('unexpected import')), \
                patch.object(global_rails, 'build_world_rails', side_effect=AssertionError('unexpected rail build')):
            second = self.prepare()
        self.assertFalse(second['changed'])
        self.assertEqual(first['database'], second['database'])
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_unchanged_original_legacy_database_is_reused_without_rebuild(self):
        self.scratch.mkdir()
        world = self.scratch / 'world.db'
        geometry.build_world(self.game, world)
        global_rails.build_world_rails(world, world.with_name('global_rails.db'))
        for name in ('world.db', 'global_rails.db'):
            shutil.copyfile(self.scratch / name, self.cache / name)
        with patch.object(geometry, 'build_world', side_effect=AssertionError('legacy import')), \
                patch.object(global_rails, 'build_world_rails', side_effect=AssertionError('legacy rails')):
            result = self.prepare()
        self.assertFalse(result['changed'])
        self.assertEqual(m.database_path(self.game), self.cache / 'world.db')
        self.assertFalse((self.cache / 'world-current.json').exists())

    def test_same_size_same_mtime_map_change_rebuilds_and_preserves_previous_generation(self):
        first = self.prepare()
        before = self.placement.stat()
        self.changed_source()
        os.utime(self.placement, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.placement.stat().st_size, before.st_size)
        second = self.prepare()
        self.assertTrue(second['changed'])
        self.assertNotEqual(first['world_sha256'], second['world_sha256'])
        self.assertNotEqual(first['rail_sha256'], second['rail_sha256'])
        self.assertTrue(Path(first['database']).exists())
        region = geometry.query_world(Path(second['database']), (4, 0, 0), 10)
        self.assertEqual(region['instances'][0]['position'], [4., 0., 0.])

    def test_new_dat_reference_is_discovered_and_imported(self):
        first = self.prepare()
        (self.game / 'data/new.ipl').write_text(self.placement.read_text().replace('fixture,0,0,0,0', 'fixture,0,8,0,0'))
        (self.game / 'data/gta.dat').write_text('IPL data/base.ipl\nIPL data/new.ipl\n')
        second = self.prepare()
        self.assertTrue(second['changed'])
        self.assertEqual(second['source_count'], first['source_count']+1)
        region = geometry.query_world(Path(second['database']), (0, 0, 0), 20)
        self.assertEqual(len(region['instances']), 2)

    def test_unchanged_sources_use_saved_hashes_but_changed_files_are_rechecked(self):
        if file_stamp(self.placement) is None:
            self.skipTest('A filesystem with reliable Windows change metadata is required')
        self.prepare()
        warm = self.prepare()
        self.assertEqual(warm['source_validation']['hits'], warm['source_count'])
        self.assertEqual(warm['source_validation']['hashed_bytes'], 0)
        # Replacing a file with identical bytes must revalidate its new identity,
        # but must not needlessly rebuild unchanged world geometry.
        original = self.placement.read_bytes()
        previous = self.placement.stat()
        replacement = self.placement.with_suffix('.replacement')
        replacement.write_bytes(original)
        os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        os.replace(replacement, self.placement)
        checked = self.prepare()
        self.assertFalse(checked['changed'])
        self.assertEqual(checked['source_validation']['hashed_bytes'], len(original))
        self.assertEqual(checked['source_validation']['hits'], checked['source_count'] - 1)

    def test_saved_hashes_do_not_hide_removed_sources_or_a_broken_cache(self):
        self.prepare()
        (self.cache / 'map-source-hashes.json').write_bytes(b'broken cache')
        checked = self.prepare()
        self.assertFalse(checked['changed'])
        self.assertGreater(checked['source_validation']['hashed_bytes'], 0)
        self.placement.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing'):
            self.prepare()

    def test_failed_rebuild_or_publication_keeps_the_old_complete_pair(self):
        first = self.prepare()
        pointer = (self.cache / 'world-current.json').read_bytes()
        self.changed_source()
        for name in ('build', 'publish'):
            with self.subTest(stage=name):
                target, attribute = (global_rails, 'build_world_rails') if name == 'build' else (m.os, 'replace')
                with patch.object(target, attribute, side_effect=OSError('controlled failure')):
                    with self.assertRaisesRegex(OSError, 'controlled failure'):
                        self.prepare()
                self.assertEqual((self.cache / 'world-current.json').read_bytes(), pointer)
                self.assertEqual(m._pair(m.database_path(self.game))[0]['world_sha256'], first['world_sha256'])
                self.assertEqual(list(self.scratch.iterdir()), [])
                self.assertFalse(any(path.name.startswith('staging-') for path in
                                     (self.cache / 'world-generations').iterdir()))

    def test_modifying_sources_during_rail_build_never_publishes(self):
        first = self.prepare()
        self.changed_source()
        original = global_rails.build_world_rails
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.placement.write_text(self.placement.read_text()+'# changed while preparing\n')
            return result
        with patch.object(global_rails, 'build_world_rails', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed before publication'):
                self.prepare()
        self.assertEqual(str(m.database_path(self.game)), first['database'])

    def test_cancel_before_publication_keeps_previous_generation(self):
        first = self.prepare()
        self.changed_source()
        stop = False
        def progress(stage, **_):
            nonlocal stop
            if stage == 'publishing':
                stop = True
        with self.assertRaises(m.PreparationCancelled):
            self.prepare(progress=progress, stopped=lambda: stop)
        self.assertEqual(str(m.database_path(self.game)), first['database'])

    def test_corrupt_pair_rebuilds_instead_of_authorizing_stale_geometry(self):
        self.prepare()
        database = m.database_path(self.game)
        database.with_name('global_rails.db').write_bytes(b'broken')
        repaired = self.prepare()
        self.assertTrue(repaired['changed'])
        self.assertNotEqual(Path(repaired['database']), database)
        self.assertEqual(m._pair(Path(repaired['database']))[0]['world_sha256'], repaired['world_sha256'])
        self.assertEqual(database.with_name('global_rails.db').read_bytes(), b'broken')

    def test_pointer_cannot_escape_owned_cache(self):
        self.prepare()
        pointer = self.cache / 'world-current.json'
        value = json.loads(pointer.read_bytes())
        for generation in ('../../original', 'C:/elsewhere', 'map-other'):
            value['generation'] = generation
            pointer.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'Invalid world-generation'):
                m.database_path(self.game)

    def test_possible_active_modloader_map_override_is_reported_but_unrelated_files_are_allowed(self):
        folder = self.game / 'modloader/visuals'
        folder.mkdir(parents=True)
        (self.game / 'modloader.asi').touch()
        (folder / 'fix.ini').write_text('fps=120')
        (folder / 'skin.txd').touch()
        self.assertTrue(self.prepare()['changed'])
        (folder / 'map.col').touch()
        with self.assertRaisesRegex(ValueError, 'Mod Loader map override'):
            self.prepare()
        (self.game / 'modloader.asi').unlink()  # No loaded virtual override mechanism.
        self.assertFalse(self.prepare()['changed'])

    def test_never_builds_inside_or_writes_to_the_original_source_installation(self):
        with self.assertRaisesRegex(ValueError, 'outside the game'):
            m.prepare(self.game, self.game / 'scratch')
        manifest = self.cache / 'base-copy.json'
        value = json.loads(manifest.read_bytes())
        value['source'] = str(self.game)
        manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'isolated GTA copy'):
            self.prepare()


if __name__ == '__main__':
    unittest.main()
