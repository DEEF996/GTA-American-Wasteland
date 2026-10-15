"""Pure complete-graph and provenance tests; no original functions execute."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import probe_gta_rails as p
from test_rail_export import inputs


def fixture():
    graph, scene = inputs()
    source, report = p.rail_export.build_rails(graph, scene)
    return source, report, scene


def native_graph(nodes, base):
    # Intentionally reverse allocation order: names, not original row indices,
    # establish graph identity and native next/previous references.
    names = {row['name']: base+index*0x60 for index, row in enumerate(reversed(nodes))}
    data = bytearray(len(nodes)*0x60)
    for row in nodes:
        at = names[row['name']]-base
        struct.pack_into('<3f', data, at+0x20, *row['position_thaw'])
        struct.pack_into('<I', data, at+0x44, row['name'])
        terrain = next(index for index, name in p.rail_export.terrain_mapping.TERRAINS.items()
                       if name == row['thaw_terrain'])
        struct.pack_into('<I', data, at+0x48, terrain)
        for field, key in ((0x50, 'next_index'), (0x54, 'previous_index')):
            value = names[nodes[row[key]]['name']] if row[key] is not None else 0
            struct.pack_into('<I', data, at+field, value)
    return data


class NativeRailsTests(unittest.TestCase):
    def test_config_contains_only_the_authored_original_format_option(self):
        source = p.dynamic_rail_config()
        self.assertEqual(len(source), 48)
        self.assertEqual(struct.unpack_from('<2I', source), (0, 48))
        self.assertEqual(source[8:28], p.rail_export.HEADER)
        self.assertEqual(struct.unpack_from('<5I', source, 28),
                         (0x10400, p.RAIL_FORMAT_KEY, p.rail_export.qbkey(p.CONFIG_NAME), 0, 0))

    def test_all_positions_and_both_links_match_independent_native_order(self):
        _, report, _ = fixture()
        result = p.validate_graph(native_graph(report['nodes'], 0x20000000), 0x20000000, report['nodes'])
        self.assertEqual((result['nodes'], result['links']), (8, 4))

    def test_duplicate_name_wrong_position_wrong_direction_and_escape_rejected(self):
        _, report, _ = fixture()
        base = 0x20000000
        for change in ('name', 'position', 'next', 'escape', 'unaligned', 'terrain'):
            contents = native_graph(report['nodes'], base)
            if change == 'name': contents[0x44:0x48] = contents[0xa4:0xa8]
            elif change == 'position': struct.pack_into('<f', contents, 0x20, 999.)
            elif change == 'next': struct.pack_into('<I', contents, 0x50, base)
            elif change == 'terrain': struct.pack_into('<I', contents, 0x48, 59)
            else: struct.pack_into('<I', contents, 0x50, base+len(contents) if change == 'escape' else base+1)
            with self.assertRaises(RuntimeError): p.validate_graph(contents, base, report['nodes'])

    def test_exact_source_report_scene_and_relative_native_path(self):
        source, report, scene = fixture()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory).resolve()
            cache = root/'resources'
            cache.mkdir()
            path = root/'gta_rails.qb.wpc'
            path.write_bytes(source)
            with patch.object(p, 'read_global_data', return_value={'col': b'private validated COL'}):
                prepared = p.prepare_inputs(path, report, scene, cache)
            self.assertEqual((cache/'data'/(prepared['native_filename']+'.wpc')).resolve(), path)
            self.assertEqual(prepared['source'], source)
            altered = deepcopy(report)
            altered['nodes'][0]['name'] ^= 1
            with self.assertRaises(RuntimeError): p.prepare_inputs(path, altered, scene, cache)
            path.write_bytes(source[:-4])
            with self.assertRaises(RuntimeError): p.prepare_inputs(path, report, scene, cache)

    def test_global_identity_requires_every_geometry_byte_and_exact_relocation(self):
        source = bytearray(332)
        struct.pack_into('<8I', source, 0, 10, 1, 8, 0, 12, 0, 8, 0)
        struct.pack_into('<IHHHBBI', source, 80, 123, 0, 8, 12, 1, 1, 0)
        struct.pack_into('<3I', source, 296, 8, 0xc0003, 0)
        base = 0x20000000
        native = p.relocated_global_col(source, base)
        p.verify_global_col(native, source, base)
        for offset in (4, 80, 92, 128, 132, 136, 144, 200, 304, 331):
            wrong = bytearray(native)
            wrong[offset] ^= 1
            with self.assertRaises(RuntimeError): p.verify_global_col(wrong, source, base)
        with self.assertRaises(RuntimeError): p.relocated_global_col(source, base+1)
        with self.assertRaises(RuntimeError): p.verify_global_col(source, source, base)

    def test_world_list_validates_backlinks_cycles_and_exact_owned_membership(self):
        world, a, b, sa, sb = p.probe_gta_scene.WORLD, 0x20000, 0x21000, 0x22000, 0x23000
        memory = {world: struct.pack('<5I', 0x6936a8, 0xffffffff, 0, a, b),
                  a: struct.pack('<5I', 0x6936a8, sa, 0, b, world),
                  b: struct.pack('<5I', 0x6936a4, sb, 0, world, a),
                  sa: struct.pack('<40I', 0x69520c, *([0]*39)), sb: struct.pack('<40I', 0x69520c, *([0]*39))}
        read = lambda address, count: memory[address][:count]
        quiet = lambda *args, **kwargs: None
        owned = dict(node=a, scene=sa)
        self.assertEqual(len(p.scene_chain(read, owned, quiet)), 2)
        original = memory[b]
        for offset, value in ((12, b), (16, world), (4, sa+4), (0, 0x12345678)):
            wrong = bytearray(original)
            struct.pack_into('<I', wrong, offset, value)
            memory[b] = bytes(wrong)
            with self.assertRaises((RuntimeError, KeyError)): p.scene_chain(read, owned, quiet)
        memory[b] = original
        with self.assertRaises(RuntimeError): p.scene_chain(read, dict(node=a, scene=sb), quiet)

    def test_existing_global_fields_are_preserved_with_native_getters(self):
        vector = struct.pack('<3f', 1., -50., 2.)
        expected = [{1: (13, 123), 2: (6, vector)}]
        blocks = {100: (0xa0100, 1, 200), 200: (0, 300), 300: (0x1b00, 1, 123, 400),
                  400: (0xd00, 2, 600, 0)}
        results, scratch = {}, 500

        def call(name, args, this):
            if name == 'ArrayPointer':
                self.assertEqual((args, this), ((0,), 100))
                return 200
            self.assertEqual(this, 200)
            key, output, required = args
            results[output] = vector if key == 2 else struct.pack('<I', blocks[300][2])
            return 1

        read = lambda address, size: results[address][:size]
        words = lambda address, count: blocks[address][:count]
        p.verify_global_nodes(100, expected, words, read, call, scratch)
        blocks[300] = (0x1b00, 1, 124, 400)
        with self.assertRaises(RuntimeError): p.verify_global_nodes(100, expected, words, read, call, scratch)
        blocks[300] = (0x1b00, 1, 123, 0)
        with self.assertRaises(RuntimeError): p.verify_global_nodes(100, expected, words, read, call, scratch)
        blocks[300] = (0x1b00, 1, 123, 400)
        blocks[400] = (0xd00, 2, 600, 300)
        with self.assertRaises(RuntimeError): p.verify_global_nodes(100, expected, words, read, call, scratch)


if __name__ == '__main__': unittest.main()
