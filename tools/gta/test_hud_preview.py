import copy
import json
import math
from pathlib import Path
import random
import struct
import tempfile
import unittest
from unittest.mock import patch

import hud_preview as h


def state():
    return dict(protocol='GTATHAW2', pid=17, creation_100ns=123, ped_address=0x20000,
                epoch=3, area=0, ped_area=0, control_eligible=True, flags=25, age_ms=1)


def glyph():
    return dict(texture=2, color=0x80808080,
                vertices=[[10,20,0,0], [30,20,1,0], [30,40,1,1], [10,40,0,1]])


class HudTransportTests(unittest.TestCase):
    def packet(self, s=None, rows=None, **kwargs):
        return h.packet(s or state(), h.scene(state()), 99, 7, 1000,
                        kwargs.get('source_tick', 1), kwargs.get('source_time_us', 16667),
                        kwargs.get('atlas_hash', bytes(range(32))), [glyph()] if rows is None else rows)

    def test_native_wire_offsets_and_unused_quads_are_zero(self):
        raw = self.packet()
        self.assertEqual(len(raw), 73860)
        self.assertEqual(struct.unpack_from('<8s4I', raw), (b'GTAHUD01', 1, 73860, 17, 3))
        self.assertEqual(struct.unpack_from('<4Q4I2Q', raw, 24),
                         (123, 99, 7, 1000, 0x20000, 0, 0, 0, 1, 16667))
        self.assertEqual(raw[88:120], bytes(range(32)))
        self.assertEqual(struct.unpack_from('<2fI', raw, 120), (640., 448., 1))
        self.assertEqual(struct.unpack_from('<2I4f', raw, 132), (2, 0x80808080, 10., 20., 0., 0.))
        self.assertFalse(any(raw[204:]))
        self.assertFalse(any(self.packet(rows=[])[132:]))

    def test_invalid_scene_clock_or_atlas_never_serialized(self):
        for key, value in [('epoch', 4), ('age_ms', 101), ('control_eligible', False), ('flags', 17)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.packet(dict(state(), **{key: value}))
        for kwargs in ({'source_tick': 0}, {'source_time_us': 0}, {'source_time_us': 2**64},
                       {'atlas_hash': bytes(32)}, {'atlas_hash': b'x'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.packet(**kwargs)
        self.packet(source_tick=432000, source_time_us=7200000000)

    def test_texture_index_uv_and_geometry_bounds(self):
        invalid = [dict(glyph(), texture=64), dict(glyph(), color=-1)]
        for vertex in range(4):
            for index in range(4):
                values = (math.nan, math.inf, -math.inf,
                          -4097 if index < 2 else -.01, 4097 if index < 2 else 1.01)
                for value in values:
                    row = copy.deepcopy(glyph())
                    row['vertices'][vertex][index] = value
                    invalid.append(row)
        for row in invalid:
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.packet(rows=[row])
        with self.assertRaises(ValueError):
            self.packet(rows=[glyph()] * 1025)

    def test_independent_uvs_and_boundary_vertices_preserve_exact_wire_values(self):
        rows = [dict(texture=63, color=0xffffffff,
                     vertices=[[-4096.,4096.,-0.,1.], [4096.,-4096.,1.,-0.],
                               [-0.,0.,.125,.875], [0.,-0.,.625,.375]])]
        rng = random.Random(187)
        for _ in range(256):
            rows.append(dict(texture=rng.randrange(64), color=rng.randrange(2**32),
                vertices=[[rng.uniform(-4096,4096),rng.uniform(-4096,4096),rng.random(),rng.random()]
                          for _ in range(4)]))
        raw = self.packet(rows=rows)
        expected = b''.join(struct.pack('<II16f', row['texture'],row['color'],
            *[value for vertex in row['vertices'] for value in vertex]) for row in rows)
        self.assertEqual(raw[132:132+len(expected)],expected)
        self.assertFalse(any(raw[132+len(expected):]))

    def test_corrupt_local_atlas_is_rejected_before_opening_game_transport(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(h, 'Publisher') as transport:
            root = Path(directory)
            (root / 'hud-atlases.bin').write_bytes(b'GTAHUDTX' + struct.pack('<2I', 1, 1))
            (root / 'manifest.json').write_text(json.dumps(dict(bundle='hud-atlases.bin',
                bundle_sha256='0' * 64, textures=[{}])), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'atlas bytes'):
                h.HudPublisher(object(), root / 'manifest.json')
            transport.assert_not_called()


if __name__ == '__main__':
    unittest.main()
