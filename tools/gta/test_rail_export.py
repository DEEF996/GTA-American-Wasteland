"""Pure authored NodeArray tests; synthetic geometry and no original game bytes."""
from copy import deepcopy
import struct
import unittest

import rail_export as r
import rails
import scene_export
from test_scene_export import fixture


def inputs():
    region = fixture(box=True)
    graph = rails.generate(region)
    _, scene = scene_export.build_scene(region, primitive_policy=scene_export.REJECT_SPHERES, hour=14)
    return graph, scene


class RailExportTests(unittest.TestCase):
    def test_metres_origin_axes_links_material_and_determinism(self):
        graph, scene = inputs()
        before = deepcopy((graph, scene))
        payload, report = r.build_rails(graph, scene)
        self.assertEqual((graph, scene), before)
        self.assertEqual(r.build_rails(graph, scene), (payload, report))
        self.assertEqual(report['node_count'], 8)
        self.assertEqual(report['link_count'], 4)
        self.assertEqual({n['gta_surface_material'] for n in report['nodes']}, {47})
        for rail, pair in zip(graph['rails'], [report['nodes'][i:i+2] for i in range(0, 8, 2)]):
            for point, row in zip(rail['points_gta'], pair):
                expected = r.g.gta_to_thaw(point, origin=scene['coordinates']['origin_gta'])
                self.assertEqual(tuple(row['position_thaw']), struct.unpack('<3f', struct.pack('<3f', *expected)))
            self.assertIsNone(pair[0]['previous_index'])
            self.assertIsNone(pair[1]['next_index'])
        self.assertFalse(report['grind_contact_verified'])

    def test_different_scene_or_scale_refused(self):
        for change in ('region', 'scale', 'area'):
            graph, scene = inputs()
            if change == 'region': scene['source_region_sha256'] = '0'*64
            elif change == 'scale': scene['coordinates']['thaw_units_per_gta_unit'] = 1
            else: scene['selection']['interior'] = 1
            with self.assertRaises(ValueError): r.build_rails(graph, scene)

    def test_duplicate_segments_names_closed_rings_and_bad_values_refused(self):
        for change in ('segment', 'name', 'ring', 'nan', 'collapsed'):
            graph, scene = inputs()
            if change == 'segment': graph['rails'][1]['points_gta'] = graph['rails'][0]['points_gta'][::-1]
            elif change == 'name': graph['rails'][1]['name'] = graph['rails'][0]['name']
            elif change == 'ring': graph['rails'][0]['points_gta'].append(graph['rails'][0]['points_gta'][0])
            elif change == 'collapsed': graph['rails'][0]['points_gta'][1] = graph['rails'][0]['points_gta'][0]
            else: graph['rails'][0]['points_gta'][0] = [float('nan'), 0., 0.]
            with self.assertRaises(ValueError): r.build_rails(graph, scene)

    def test_budget_or_missing_provenance_refused(self):
        for change in ('dropped', 'empty', 'large', 'provenance'):
            graph, scene = inputs()
            if change == 'dropped': graph['budget']['applied'] = True
            elif change == 'empty': graph['rails'] = []
            elif change == 'large': graph['rails'] *= 2049
            else: graph['rails'][0]['sources'] = []
            with self.assertRaises(ValueError): r.build_rails(graph, scene)

    def test_corrupted_qb_struct_links_and_sorted_lookup_refused(self):
        graph, scene = inputs()
        payload, _ = r.build_rails(graph, scene)
        for offset, value in ((4, len(payload)+4), (60, 0xffffffff), (len(payload)-4, 0xffffffff)):
            data = bytearray(payload)
            struct.pack_into('<I', data, offset, value)
            with self.assertRaises(ValueError): r.read_nodes(data)

    def test_every_node_still_checks_constant_fields_vectors_and_links(self):
        graph, scene = inputs()
        payload, report = r.build_rails(graph, scene)
        expected = [{key: row[key] for key in ('name', 'position_thaw', 'next_index', 'thaw_type', 'thaw_terrain')}
                    for row in report['nodes']]
        for node_index in (2, 3):  # Both linked and terminal nodes, beyond the first pair.
            node = struct.unpack_from('<I', payload, 60+node_index*4)[0]
            field = struct.unpack_from('<I', payload, node+4)[0]
            offsets = {}
            while field:
                _, key, _, following = struct.unpack_from('<4I', payload, field)
                offsets[key] = field
                field = following
            edits = [(offsets[r.qbkey('Class')]+4, r.qbkey('UnexpectedField')),
                     (offsets[r.qbkey('Name')], 0xd00), (offsets[0]+8, r.qbkey('NotCreated'))]
            edits.extend((offsets[r.qbkey(name)]+8, r.qbkey('IncorrectValue'))
                         for name in ('Class', 'Type', 'TerrainType'))
            for name in ('Pos', 'Angles'):
                vector = struct.unpack_from('<I', payload, offsets[r.qbkey(name)]+8)[0]
                edits.append((vector, 0x101))
                edits.append((vector+4, 0x7fc00000 if name == 'Pos' else 0x3f800000))
            if r.qbkey('Links') in offsets:
                links = struct.unpack_from('<I', payload, offsets[r.qbkey('Links')]+8)[0]
                edits.extend(((links+4, 2), (links+8, report['node_count'])))
            for offset, value in edits:
                data = bytearray(payload)
                self.assertEqual(r.read_nodes(data), expected)
                struct.pack_into('<I', data, offset, value)
                with self.subTest(node=node_index, offset=offset), self.assertRaises(ValueError):
                    r.read_nodes(data)  # An earlier validation never caches mutable input.

    def test_streaming_names_are_distinct_and_sorted_links_keep_every_segment(self):
        graph, scene = inputs()
        original_graph = deepcopy(graph)
        exports = [r.build_rails(graph, scene, basename=name, sort_native=True)
                   for name in ('gta_r_0001', 'gta_r_0002')]
        self.assertEqual(graph, original_graph)
        self.assertFalse({row['name'] for row in exports[0][1]['nodes']} &
                         {row['name'] for row in exports[1][1]['nodes']})
        for source, report in exports:
            nodes = report['nodes']
            self.assertEqual([row['name'] for row in nodes], sorted(row['name'] for row in nodes))
            self.assertEqual(len(r.read_nodes(source, report['basename'])), 8)
            links = {(tuple(row['position_thaw']), tuple(nodes[row['next_index']]['position_thaw']))
                     for row in nodes if row['next_index'] is not None}
            expected = {(tuple(r.g.gta_to_thaw(a, origin=scene['coordinates']['origin_gta'])),
                         tuple(r.g.gta_to_thaw(b, origin=scene['coordinates']['origin_gta'])))
                        for chain in graph['rails'] for a, b in zip(chain['points_gta'], chain['points_gta'][1:])}
            expected = {tuple(tuple(struct.unpack('<3f', struct.pack('<3f', *point))) for point in edge)
                        for edge in expected}
            self.assertEqual(links, expected)
            with self.assertRaises(ValueError): r.read_nodes(source)

    def test_streaming_basename_cannot_escape_or_overflow_native_path(self):
        graph, scene = inputs()
        for name in ('../gta', 'gta/rails', 'gta.rails', 'a'*33, '', 12):
            with self.subTest(name=name), self.assertRaises(ValueError):
                r.build_rails(graph, scene, basename=name)


if __name__ == '__main__': unittest.main()
