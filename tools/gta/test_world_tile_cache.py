"""Persistent private tiles: restart reuse, exact identities and corrupt misses."""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import geometry
from test_scene_export import fixture
from test_world_stream import Deferred, state, activation
import world_stream as w
import world_tile_cache as c


class TileCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'world.db'
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT)')
            db.executemany('INSERT INTO metadata VALUES (?,?)',
                          [('schema', json.dumps(geometry.SCHEMA)), ('complete', 'true')])
            db.commit()

    def request(self, *, origin=(0., 0., 0.), current=None, active=None):
        with w.WorldStream(self.db, origin=origin, executor=Deferred()) as stream:
            stream.request(current or state(), active or activation())
            return stream.executor.jobs[0][1]

    def compile(self, request):
        region = fixture(position=(0., 0., 0.))
        region['center'], region['radius'] = request['recipe']['center'], request['recipe']['radius']
        with patch.object(geometry, 'query_world', return_value=region) as query:
            result = w._compile(request)
        return result, query.call_count

    def rewrite(self, cache, change):
        with zipfile.ZipFile(cache.path) as archive:
            blobs = {name: archive.read(name) for name in archive.namelist()}
        change(blobs)
        with zipfile.ZipFile(cache.path, 'w') as archive:
            for name, data in blobs.items():
                archive.writestr(name, data)

    def test_second_stream_reuses_prepared_tile_without_query_or_compiler(self):
        request = self.request()
        first, calls = self.compile(request)
        self.assertEqual(calls, 1)
        self.assertTrue(first['timings_ms']['cache_saved'])
        with patch.object(geometry, 'query_world', side_effect=AssertionError('unexpected query')), \
                patch.object(w.scene_export, 'build_scene', side_effect=AssertionError('unexpected compiler')):
            second = w._compile(self.request(current=dict(state(), pid=321, epoch=9)))
        self.assertTrue(second['timings_ms']['cache_hit'])
        self.assertGreaterEqual(second['timings_ms']['cache_load'], 0)
        self.assertEqual(second['inputs']['col'], first['inputs']['col'])
        self.assertEqual(second['inputs']['seal'], first['inputs']['seal'])
        self.assertEqual(c._encoded({k:v for k,v in second['inputs'].items() if k != 'col'}),
                         c._encoded({k:v for k,v in first['inputs'].items() if k != 'col'}))

    def test_map_code_clock_ipl_origin_and_tile_changes_cannot_use_previous_tile(self):
        original = self.request()
        self.compile(original)
        requests = [self.request(origin=(1., 0., 0.)),
                    self.request(current=dict(state(), clock_hour=15)),
                    self.request(current=state((32., 0., 0.))),
                    self.request(active=dict(active_streams=[])),
                    self.request(current=dict(state(), area=1, ped_area=1))]
        changed_map = deepcopy(original)
        changed_map['recipe']['database_sha256'] = 'a'*64
        changed_rails = deepcopy(original)
        changed_rails['recipe']['rail_cache_sha256'] = 'b'*64
        for request in requests + [changed_map, changed_rails]:
            with self.subTest(recipe=request['recipe']):
                self.assertIsNone(c.TileCache(request).load())
        with patch.object(c, 'compiler_fingerprint', return_value='c'*64):
            self.assertIsNone(c.TileCache(original).load())

    def test_equivalent_visibility_hours_reuse_content_but_keep_raw_ticket_identity(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('CREATE TABLE definitions (payload TEXT)')
            db.commit()
        first = self.request()
        self.compile(first)
        later = self.request(current=dict(state(), clock_hour=15))
        self.assertEqual(first['key'], later['key'])
        self.assertIsNotNone(c.TileCache(later).load())

    def test_fingerprint_reads_converter_source_content(self):
        baseline = c.compiler_fingerprint()
        original = Path.read_bytes
        for dependency in c.DEPENDENCIES:
            with self.subTest(dependency=dependency):
                def changed(path):
                    data = original(path)
                    return data+b'\n# changed' if path.as_posix().endswith(dependency) else data
                with patch.object(Path, 'read_bytes', changed):
                    self.assertNotEqual(c.compiler_fingerprint(), baseline)

    def test_disk_rails_are_fully_reparsed_and_mismatched_report_recompiled(self):
        import global_rails
        import rail_export
        import rails
        request = self.request()
        rail_path = self.db.with_name('synthetic_rails.db')
        rail_path.write_bytes(b'synthetic immutable rail cache')
        request.update(rail_database=str(rail_path), rail_database_stamp=w._file_stamp(rail_path))
        request['recipe']['rail_cache_sha256'] = 'a'*64
        region = fixture(box=True, position=(0., 0., 0.))
        region['center'], region['radius'] = request['recipe']['center'], request['recipe']['radius']

        def authored(_path, _sha, scene, **_):
            source, report = rail_export.build_rails(rails.generate(region), scene, sort_native=True)
            return dict(source=source, report=report)

        with patch.object(geometry, 'query_world', return_value=region), \
                patch.object(global_rails, 'prepare_rails', side_effect=authored):
            first = w._compile(request)
        self.assertTrue(first['timings_ms']['cache_saved'])
        with patch.object(geometry, 'query_world', side_effect=AssertionError('cache missed')), \
                patch.object(rail_export, 'read_nodes', wraps=rail_export.read_nodes) as parser:
            loaded = w._compile(request)
        self.assertTrue(loaded['timings_ms']['cache_hit'])
        parser.assert_called_once()
        self.assertEqual(loaded['inputs']['rails']['source'], first['inputs']['rails']['source'])
        self.assertNotIn('candidate_provenance', first['inputs']['rails']['report'])
        self.assertNotIn('candidate_provenance', loaded['inputs']['rails']['report'])
        self.assertEqual(c._encoded(loaded['inputs']['rails']['report']),
                         c._encoded(first['inputs']['rails']['report']))
        self.assertEqual(loaded['inputs']['rails']['validation'], first['inputs']['rails']['validation'])
        cache = c.TileCache(request)
        # IPC compaction runs after save/load validation, preserving complete
        # provenance for independent offline replay from the private archive.
        with zipfile.ZipFile(cache.path) as archive:
            persisted = json.loads(archive.read('metadata.json'))['result']
        self.assertIn('candidate_provenance', persisted['inputs']['rails']['report'])
        self.assertEqual(persisted['inputs']['rails']['report']['nodes'], first['inputs']['rails']['report']['nodes'])

        def mismatch(blobs):
            metadata = json.loads(blobs['metadata.json'])
            metadata['result']['inputs']['rails']['report']['nodes'][0]['position_thaw'][0] += 1
            blobs['metadata.json'] = c._encoded(metadata)
        self.rewrite(cache, mismatch)
        with patch.object(geometry, 'query_world', return_value=region) as query, \
                patch.object(global_rails, 'prepare_rails', side_effect=authored):
            rebuilt = w._compile(request)
        query.assert_called_once()
        self.assertFalse(rebuilt['timings_ms']['cache_hit'])

    def test_valid_hit_still_rejects_changed_database_stamp(self):
        request = self.request()
        self.compile(request)
        request['database_stamp'] = (1, 2, 3)
        with self.assertRaises(w.RegionFailed):
            w._compile(request)

    def test_corrupt_truncated_wrong_identity_and_wrong_seal_recompile(self):
        request = self.request()
        for problem in ('truncated', 'col', 'identity', 'seal', 'selection'):
            with self.subTest(problem=problem):
                cache = c.TileCache(request)
                if cache.path.exists():
                    cache.path.unlink()
                self.compile(request)
                if problem == 'truncated':
                    cache.path.write_bytes(cache.path.read_bytes()[:40])
                else:
                    def corrupt(blobs):
                        if problem == 'col':
                            blobs['collision.col'] = b'bad' + blobs['collision.col'][3:]
                            return
                        metadata = json.loads(blobs['metadata.json'])
                        if problem == 'identity':
                            metadata['identity']['key'] = '0'*64
                        elif problem == 'seal':
                            metadata['result']['inputs']['cases'][0]['hit'] = False
                        else:
                            import probe_gta_scene
                            inputs = metadata['result']['inputs']
                            inputs['selection']['interior'] += 1
                            inputs['seal'] = probe_gta_scene._seal(inputs)
                        blobs['metadata.json'] = c._encoded(metadata)
                    self.rewrite(cache, corrupt)
                result, calls = self.compile(request)
                self.assertEqual(calls, 1)
                self.assertFalse(result['timings_ms']['cache_hit'])
                self.assertIsNotNone(cache.load())

    def test_unexpected_archive_paths_are_never_extracted(self):
        request = self.request()
        self.compile(request)
        cache = c.TileCache(request)
        self.rewrite(cache, lambda blobs: blobs.update({'../outside.txt': b'escape'}))
        self.assertIsNone(cache.load())
        self.assertFalse((self.db.parent / 'outside.txt').exists())
        self.assertFalse((cache.root / 'outside.txt').exists())

    def test_oversized_members_are_rejected_before_decompression(self):
        request = self.request()
        self.compile(request)
        cache = c.TileCache(request)
        with patch.dict(c.LIMITS, {'metadata.json': 1}), \
                patch.object(zipfile.ZipFile, 'read', side_effect=AssertionError('oversized read')):
            self.assertIsNone(cache.load())

    def test_linked_cache_directory_is_not_read_written_or_pruned(self):
        request = self.request()
        result, _ = self.compile(request)
        cache = c.TileCache(request)
        with patch.object(Path, 'is_junction', return_value=True), \
                patch.object(zipfile, 'ZipFile', side_effect=AssertionError('followed link')), \
                patch.object(Path, 'unlink', side_effect=AssertionError('deleted linked file')):
            self.assertIsNone(cache.load())
            self.assertFalse(cache.save(result))

    def test_failed_atomic_publish_keeps_previous_complete_entry_and_cleans_temp(self):
        request = self.request()
        result, _ = self.compile(request)
        cache = c.TileCache(request)
        before = cache.path.read_bytes()
        with patch.object(c.os, 'replace', side_effect=OSError('disk full/read only')):
            self.assertFalse(cache.save(result))
        self.assertEqual(cache.path.read_bytes(), before)
        self.assertEqual(list(cache.root.glob('*.tmp')), [])
        self.assertIsNotNone(cache.load())

    def test_readonly_missing_cache_does_not_fail_compilation(self):
        with patch.object(Path, 'mkdir', side_effect=PermissionError('read only')):
            result, calls = self.compile(self.request())
        self.assertEqual(calls, 1)
        self.assertFalse(result['timings_ms']['cache_saved'])

    def test_lru_removes_only_owned_entries_and_preserves_recent_hit(self):
        request = self.request()
        result, _ = self.compile(request)
        cache = c.TileCache(request)
        old = cache.root / ('tile-' + '0'*64 + '.zip')
        old.write_bytes(b'x'*100)
        os.utime(old, (1, 1))
        unrelated = cache.root / 'keep.zip'
        unrelated.write_bytes(b'not ours')
        with patch.object(c, 'MAX_BYTES', cache.path.stat().st_size+50):
            cache._trim()
        self.assertFalse(old.exists())
        self.assertTrue(unrelated.exists())
        self.assertIsNotNone(cache.load())


if __name__ == '__main__':
    unittest.main()
