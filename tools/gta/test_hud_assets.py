import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import hud_assets as h
from hud_quads import Assets


class HudAssetsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache, self.original = self.root / 'cache', self.root / 'original'
        self.original.mkdir()
        self.rows = []

        def source(name):
            payload = name.encode('ascii')
            path = self.cache / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            self.rows.append(dict(path=name, bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()))

        for name in h.SETUP_SPRITES + ('observed_panel',):
            source('data/images/panelsprites/' + name + '.img.wpc')
        source('data/images/panelsprites/xenon_textures/nollie_icon.img.wpc')
        for name in ('newtrickfont', 'newtimerfont', 'small', 'dialog', 'buttonspc'):
            source('data/fonts/' + name + '.fnt.wpc')
        source('data/worlds/Global/global.pak.wpc')
        source('data/worlds/Global/global.pab.wpc')
        self.manifest = self.cache / 'manifest.json'
        self.manifest.write_text(json.dumps(dict(format='gta-thaw-private-stock-resources-v1',
            sources_revalidated_before_publish=True, directory=str(self.cache),
            source=str(self.original), files=self.rows)), encoding='utf-8')
        image = bytearray(16)
        struct.pack_into('<4H', image, 8, 2, 2, 2, 2)
        self.draw = dict(kind='sprite', image_key=h.qbkey('observed_panel'), image_header_hex=image.hex())
        self.log = self.root / 'layout.jsonl'
        self.log.write_text(json.dumps(dict(phase='hud_layout_pass', original_layout_update=True,
            canvas=[640, 448], draws=[self.draw])) + '\n', encoding='utf-8')
        self.output = self.root / 'output'
        font = dict(height=20, baseline=15, lookup=[0] * 256,
            glyphs=[dict(uv=[0, 0, 1, 1], width=2, height=2, baseline=1, extra_bytes=[0, 0])])
        for name, replacement in (
                ('pak_member', lambda *_: b'synthetic font member'),
                ('parse_font', lambda _: (copy.deepcopy(font), (2, 2, b'\x80' * 16))),
                ('decode_img', lambda _: (2, 2, b'\x40' * 16))):
            patcher = patch.object(h, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_stance_icons_are_available_without_observing_every_stance(self):
        h.prepare(self.manifest, self.log, self.output)
        metadata = json.loads((self.output / 'manifest.json').read_bytes())
        images = {item['name']: item for item in metadata['textures'] if item['kind'] == 'image'}
        for name in ('nollie_icon', 'fakie_icon', 'switch_icon', 'pressure_icon', 'sw_pressure_icon'):
            self.assertIn(name, images)
            self.assertEqual(images[name]['source'], 'data/images/panelsprites/' + name + '.img.wpc')
        self.assertIn('observed_panel', images)
        self.assertFalse(any('xenon_textures' in row['source'] for row in images.values()))
        assets = Assets(metadata)
        for name in images:
            item = dict(self.draw, image_key=h.qbkey(name))
            self.assertEqual(assets.validate_source(item)['name'], name)

    def test_first_run_import_needs_no_private_layout_recording(self):
        h.prepare(self.manifest, None, self.output)
        metadata=json.loads((self.output/'manifest.json').read_bytes())
        images={item['name'] for item in metadata['textures'] if item['kind']=='image'}
        self.assertEqual(images,set(h.SETUP_SPRITES))
        self.assertEqual(len(metadata['textures']),30)
        self.assertIsNone(metadata['layout_log'])
        self.assertTrue(metadata['native_layout_required'])

    def test_changed_stance_source_is_rejected_before_publication(self):
        (self.cache / 'data/images/panelsprites/nollie_icon.img.wpc').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            h.prepare(self.manifest, self.log, self.output)
        self.assertFalse(self.output.exists())

    def test_console_variant_cannot_replace_missing_original_pc_icon(self):
        metadata = json.loads(self.manifest.read_bytes())
        metadata['files'] = [row for row in metadata['files']
            if row['path'] != 'data/images/panelsprites/nollie_icon.img.wpc']
        self.manifest.write_text(json.dumps(metadata), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'ordinary stock panel sprite for 33a15296'):
            h.prepare(self.manifest, self.log, self.output)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
