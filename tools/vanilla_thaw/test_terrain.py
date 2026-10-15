import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import probe_terrain as terrain


class TerrainInputsTests(unittest.TestCase):
    def test_changed_source_rejected_before_native_access(self):
        for source in (b'', bytes(15736), bytes(15735)):
            system = Mock()
            with patch.object(terrain.host, 'native_call') as native:
                with self.assertRaisesRegex(RuntimeError, 'Whole stock terrain source'):
                    terrain.run(None, None, None, None, None, (source, []), None, system, {})
                native.assert_not_called()
                system.assert_not_called()
                self.assertEqual(system.mock_calls, [])

    def test_cache_cannot_escape_or_choose_a_different_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / 'cache'; cache.mkdir()
            qb, image = cache / 'manifest.json', root / 'image.json'
            original = root / 'original'
            image.write_text(json.dumps({'source':str(original)}))
            manifest = {'format':'stock-thaw-core-qb-cache-v1',
                        'sources_revalidated_before_publish':True, 'source_directory':str(original),
                        'members':[{'order':21, 'canonical_name':terrain.SOURCE, 'bytes':15736,
                                    'sha256':terrain.SOURCE_SHA, 'cache_file':'../outside.qb'}]}
            qb.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(RuntimeError, 'escapes private cache'):
                terrain.read_inputs(qb, image)
            manifest['source_directory'] = str(root / 'other')
            qb.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(RuntimeError, 'selected separate stock QB cache'):
                terrain.read_inputs(qb, image)


if __name__ == '__main__':
    unittest.main()
