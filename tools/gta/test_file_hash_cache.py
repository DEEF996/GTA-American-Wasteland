"""Small temporary-file regressions; no game files or processes are touched."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import file_hash_cache as m


class FileHashCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'map.img'
        self.source.write_bytes(b'original geometry')
        self.path = self.root / 'hashes.json'

    def strong(self):
        value = m.file_stamp(self.source)
        if value is None:
            self.skipTest('Fast reuse requires local Windows NTFS/ReFS strong metadata')
        return value

    def test_persisted_hit_does_not_read_source_bytes(self):
        self.strong()
        first = m.FileHashCache(self.path)
        expected = first.digest(self.source)
        self.assertEqual(expected, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(first.stats, dict(hits=0, hashed_bytes=self.source.stat().st_size))
        self.assertTrue(first.save())
        again = m.FileHashCache(self.path)
        with patch.object(m.hashlib, 'file_digest', side_effect=AssertionError('cache hit reread the archive')):
            self.assertEqual(again.digest(self.source), expected)
        self.assertEqual(again.stats, dict(hits=1, hashed_bytes=0))

    @unittest.skipUnless(os.name == 'nt', 'Windows ChangeTime regression')
    def test_same_size_edit_with_restored_mtime_invalidates_real_windows_cache(self):
        before_stamp = self.strong()
        before = self.source.stat()
        cache = m.FileHashCache(self.path)
        old = cache.digest(self.source)
        self.assertTrue(cache.save())
        self.source.write_bytes(b'changed! geometry')
        os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.source.stat().st_size, before.st_size)
        self.assertEqual(self.source.stat().st_mtime_ns, before.st_mtime_ns)
        after_stamp = m.file_stamp(self.source)
        self.assertEqual(after_stamp[:3], before_stamp[:3])
        self.assertEqual(after_stamp[3], before_stamp[3])
        self.assertNotEqual(after_stamp[4], before_stamp[4])
        again = m.FileHashCache(self.path)
        self.assertNotEqual(again.digest(self.source), old)
        self.assertEqual(again.stats['hits'], 0)

    def test_replacement_with_preserved_size_and_mtime_invalidates_identity(self):
        before = self.strong()
        stat_before = self.source.stat()
        cache = m.FileHashCache(self.path)
        old = cache.digest(self.source)
        replacement = self.root / 'replacement.img'
        replacement.write_bytes(b'changed! geometry')
        os.utime(replacement, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))
        os.replace(replacement, self.source)
        self.assertNotEqual(m.file_stamp(self.source)[1], before[1])
        self.assertNotEqual(cache.digest(self.source), old)
        self.assertEqual(cache.stats['hits'], 0)

    def test_no_strong_stamp_always_hashes_even_with_saved_entry(self):
        cache = m.FileHashCache(self.path)
        cache.digest(self.source)
        with patch.object(m, '_strong_stamp', return_value=None):
            result = cache.digest(self.source)
            self.assertEqual(cache.digest(self.source), result)
        self.assertEqual(cache.stats['hits'], 0)
        self.assertEqual(cache.stats['hashed_bytes'], 3*self.source.stat().st_size)
        self.assertEqual(cache.entries, {})

    def test_original_executable_never_uses_digest_cache(self):
        exe = self.root / 'THAW.exe'
        exe.write_bytes(b'not a real executable')
        cache = m.FileHashCache(self.path)
        cache.digest(exe)
        cache.digest(exe)
        self.assertEqual(cache.stats, dict(hits=0, hashed_bytes=2*exe.stat().st_size))
        self.assertEqual(cache.entries, {})

    def test_corrupt_oversized_and_invalid_cache_are_misses(self):
        fixtures = [b'{broken', b'[]', b'{"format":123}', b'['*1200+b']'*1200,
            json.dumps(dict(format=m.FORMAT, entries={str(self.source):dict(stamp=[True]*5, sha256='a'*64)})).encode()]
        for content in fixtures:
            with self.subTest(content=content[:50]):
                self.path.write_bytes(content)
                cache = m.FileHashCache(self.path)
                self.assertEqual(cache.entries, {})
                self.assertEqual(cache.digest(self.source), hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.path.write_bytes(b'x'*1025)
        with patch.object(m, 'MAX_BYTES', 1024):
            self.assertEqual(m.FileHashCache(self.path).entries, {})

    def test_failed_atomic_save_keeps_previous_cache_and_cleans_own_temporary(self):
        self.path.write_bytes(b'old cache preserved')
        cache = m.FileHashCache(self.path)
        cache.digest(self.source)
        with patch.object(m.os, 'replace', side_effect=PermissionError('controlled failure')):
            self.assertFalse(cache.save())
        self.assertEqual(self.path.read_bytes(), b'old cache preserved')
        self.assertEqual(list(self.root.glob('.file-hashes-*.tmp')), [])

    def test_missing_cache_parent_is_optional_and_not_created(self):
        cache = m.FileHashCache(self.root / 'missing' / 'hashes.json')
        self.assertEqual(cache.digest(self.source), hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertFalse(cache.save())
        self.assertFalse((self.root / 'missing').exists())

    def test_redirected_cache_is_not_loaded_or_written(self):
        destination = self.root / 'elsewhere.json'
        destination.write_bytes(b'keep this file')
        try:
            self.path.symlink_to(destination)
        except OSError:
            self.skipTest('Creating test symlinks is unavailable')
        cache = m.FileHashCache(self.path)
        self.assertEqual(cache.entries, {})
        cache.digest(self.source)
        self.assertFalse(cache.save())
        self.assertEqual(destination.read_bytes(), b'keep this file')

    def test_changed_fingerprint_during_hashing_is_rejected(self):
        cache = m.FileHashCache(self.path)
        with patch.object(m, '_strong_stamp', side_effect=[(1, 2, 17, 3, 4), (1, 2, 17, 3, 5)]):
            with self.assertRaisesRegex(ValueError, 'changed during hashing'):
                cache.digest(self.source)
        self.assertEqual(cache.entries, {})

    @unittest.skipUnless(os.name == 'nt', 'Windows stable read handle')
    def test_open_snapshot_blocks_concurrent_writer(self):
        if m._API is None:
            self.skipTest('Win32 metadata API unavailable')
        with m._open(self.source):
            with self.assertRaises(PermissionError):
                self.source.write_bytes(b'changed! geometry')
        self.assertEqual(self.source.read_bytes(), b'original geometry')


if __name__ == '__main__':
    unittest.main()
