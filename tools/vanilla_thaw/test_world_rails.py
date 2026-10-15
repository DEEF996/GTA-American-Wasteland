from copy import deepcopy
import hashlib
import pickle
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from probe_world_rails import observe, rail_identity, prepare_rail_validation, verified_rail_identity, retired_candidates, activation_stub, STREAM_PINS, RailStream, MAX_UPDATE_STAGES


class WorldRailObservationTests(unittest.TestCase):
    def batch_fixture(self, results):
        stream = RailStream.__new__(RailStream)
        stream.poisoned = False
        stream.log = Mock()
        stream._update = Mock(side_effect=results)
        return stream

    def test_small_native_phases_complete_without_waiting_render_frames(self):
        rows = [dict(ready=False, stage='load'), dict(ready=False, stage='parse'), dict(ready=True, stage=None)]
        stream = self.batch_fixture(rows)
        inputs, sample = object(), object()
        with patch('probe_world_rails.time.perf_counter', side_effect=[0, .001, .002, .003]):
            self.assertEqual(stream.update(inputs, sample), rows[-1])
        self.assertEqual(stream._update.call_count, 3)
        self.assertTrue(all(call.args == (inputs, sample) for call in stream._update.call_args_list))
        self.assertFalse(stream.poisoned)

    def test_native_batch_yields_on_time_or_stage_budget(self):
        row = dict(ready=False, stage='parse')
        stream = self.batch_fixture([row]*MAX_UPDATE_STAGES)
        with patch('probe_world_rails.time.perf_counter', side_effect=[0, .006, .009]):
            self.assertEqual(stream.update({}), row)
        self.assertEqual(stream._update.call_count, 2)
        stream = self.batch_fixture([row]*MAX_UPDATE_STAGES)
        with patch('probe_world_rails.time.perf_counter', return_value=0):
            self.assertEqual(stream.update({}), row)
        self.assertEqual(stream._update.call_count, MAX_UPDATE_STAGES)

    def test_failed_native_batch_is_poisoned_and_never_resumed(self):
        stream = self.batch_fixture([RuntimeError('native failure')])
        with self.assertRaisesRegex(RuntimeError, 'native failure'):
            stream.update({})
        self.assertTrue(stream.poisoned)
        with self.assertRaisesRegex(RuntimeError, 'poisoned'):
            stream.update({})
        self.assertEqual(stream._update.call_count, 1)

    def fixture(self):
        core, group, manager = 0x10000, 0x20000, 0x30000
        expected = dict(name=123, position_thaw=[1., 2., 3.], source_chain='candidate0')
        node = bytearray(0x60)
        struct.pack_into('<3f', node, 0x20, *expected['position_thaw'])
        struct.pack_into('<I', node, 0x44, expected['name'])
        memory = {core+0x3f0: struct.pack('<2I', group+0x60, manager), group+0x60: node}
        system = SimpleNamespace(read=lambda address, size: bytes(memory[address][:size]))
        context = dict(components={0x5bd63b29: core})
        installed = dict(group=group, nodes=2, rail_manager=manager, nodes_by_name={123: expected})
        sample = dict(mode=0, walking_gate=0, state=4, walk_state=6)
        return sample, context, system, installed, memory

    def test_streaming_retirement_preserves_every_borrowed_group(self):
        groups = [dict(key='current', group=0x20000, nodes=2),
                  dict(key='old_borrowed', group=0x30000, nodes=8),
                  dict(key='old_free', group=0x40000, nodes=4)]
        for pointer in (0x30000, 0x30060, 0x302ff):
            for owner in ('core', 'walk', 'camera_owner', 'other_deferred_owner'):
                blocks = [(owner, struct.pack('<3I', 0, pointer, 0))]
                self.assertEqual(retired_candidates(groups, 'current', blocks), [groups[2]])
        self.assertEqual(retired_candidates(groups, 'current', [('core', bytes(64))]), groups[1:])

    def test_streaming_tile_requires_matched_origin_col_and_complete_authored_array(self):
        import rail_export
        from test_rail_export import inputs
        graph, scene = inputs()
        payload, report = rail_export.build_rails(graph, scene, basename='gta_r_test', sort_native=True)
        prepared = dict(col_sha256=scene['col_sha256'], coordinates=scene['coordinates'],
                        rails=dict(source=payload, report=report))
        self.assertEqual(rail_identity(prepared), report['qb_sha256'])
        for field in ('col', 'origin', 'qb'):
            bad = dict(prepared)
            if field == 'col': bad['col_sha256'] = '0'*64
            elif field == 'origin': bad['coordinates'] = dict(scene['coordinates'], origin_gta=[0,0,0])
            else: bad['rails'] = dict(source=payload[:-1]+bytes([payload[-1]^1]), report=report)
            with self.subTest(field=field), self.assertRaises(RuntimeError): rail_identity(bad)
        self.assertEqual(rail_identity(dict(col_sha256='tile', rails=dict(source=None, report=None))), 'tile-empty')

    def prepared_fixture(self):
        import rail_export
        from test_rail_export import inputs
        graph, scene = inputs()
        payload, report = rail_export.build_rails(graph, scene, basename='gta_r_sealed', sort_native=True)
        return dict(col_sha256=scene['col_sha256'], coordinates=scene['coordinates'],
                    rails=dict(source=payload, report=report))

    def test_worker_full_validation_survives_ipc_without_reparsing_on_control_thread(self):
        import rail_export
        prepared = self.prepared_fixture()
        with patch.object(rail_export, 'read_nodes', wraps=rail_export.read_nodes) as parser:
            prepared['rails']['validation'] = prepare_rail_validation(prepared)
        parser.assert_called_once()
        received = pickle.loads(pickle.dumps(prepared))
        with patch.object(rail_export, 'read_nodes', side_effect=AssertionError('control-thread reparse')):
            self.assertEqual(verified_rail_identity(received), prepared['rails']['report']['qb_sha256'])

    def test_worker_seal_rejects_mutated_bytes_report_names_and_coordinate_bindings(self):
        prepared = self.prepared_fixture()
        prepared['rails']['validation'] = prepare_rail_validation(prepared)
        for field in ('bytes', 'bytes_and_hash', 'position', 'next_index', 'previous_index',
                      'name', 'source_chain', 'basename', 'array_checksum', 'node_count',
                      'col', 'col_and_report', 'origin', 'origin_and_report', 'format', 'key', 'seal'):
            bad = deepcopy(prepared)
            rails, report = bad['rails'], bad['rails']['report']
            if field in ('bytes', 'bytes_and_hash'):
                rails['source'] = rails['source'][:-1]+bytes([rails['source'][-1]^1])
                if field == 'bytes_and_hash':
                    report['qb_sha256'] = hashlib.sha256(rails['source']).hexdigest()
                    rails['validation']['key'] = report['qb_sha256']
            elif field == 'position': report['nodes'][0]['position_thaw'][0] += 1
            elif field in ('next_index', 'previous_index'): report['nodes'][0][field] = 0
            elif field == 'name': report['nodes'][0]['name'] ^= 1
            elif field == 'source_chain': report['nodes'][0]['source_chain'] += '_changed'
            elif field == 'basename': report['basename'] += '_changed'
            elif field in ('array_checksum', 'node_count'): report[field] += 1
            elif field in ('col', 'col_and_report'):
                bad['col_sha256'] = '0'*64
                if field == 'col_and_report': report['scene_col_sha256'] = bad['col_sha256']
            elif field in ('origin', 'origin_and_report'):
                bad['coordinates'] = deepcopy(bad['coordinates'])
                bad['coordinates']['origin_gta'][0] += 1
                if field == 'origin_and_report': report['coordinates'] = deepcopy(bad['coordinates'])
            else: rails['validation'][field] = 'changed'
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                verified_rail_identity(bad)

    def test_unsealed_input_still_requires_complete_independent_parser(self):
        import rail_export
        prepared = self.prepared_fixture()
        with patch.object(rail_export, 'read_nodes', wraps=rail_export.read_nodes) as parser:
            self.assertEqual(verified_rail_identity(prepared), rail_identity(prepared))
        self.assertEqual(parser.call_count, 2)
        prepared['rails']['report']['nodes'][0]['position_thaw'][0] += 1
        with self.assertRaisesRegex(RuntimeError, 'complete report'):
            prepare_rail_validation(prepared)

    def test_empty_worker_graph_is_bound_to_exact_collision(self):
        prepared = dict(col_sha256='tile', rails=dict(source=None, report=None))
        prepared['rails']['validation'] = prepare_rail_validation(prepared)
        self.assertEqual(verified_rail_identity(prepared), 'tile-empty')
        prepared['col_sha256'] = 'another-tile'
        prepared['rails']['validation']['key'] = 'another-tile-empty'
        with self.assertRaisesRegex(RuntimeError, 'binding changed'):
            verified_rail_identity(prepared)

    def test_original_activation_batch_uses_thiscall_and_fits_one_worker_page(self):
        from probe_loader import STUB
        manager, record = 0x20000, 0x30000
        names = list(range(1, 129))
        code = activation_stub(names, False, manager, record)
        self.assertLessEqual(len(code), 4096)
        self.assertEqual(code[:10], b'\xc7\x05'+struct.pack('<2I', record, 2))
        at = 10
        for name in names:
            expected = b''.join(b'\x68'+struct.pack('<I', value) for value in (1, 0, name))
            expected += b'\xb9'+struct.pack('<I', manager)+b'\xb8'+struct.pack('<I', STREAM_PINS['RailManagerSetActive'][1])+b'\xff\xd0'
            self.assertEqual(code[at:at+len(expected)], expected)
            at += len(expected)
        self.assertEqual(code[at:], b'\xc7\x05'+struct.pack('<2I', record, 6)+STUB)
        for bad in ([], [1, 1], list(range(129)), [0], [-1], [0x100000000]):
            with self.subTest(bad=bad[:3]), self.assertRaises(RuntimeError): activation_stub(bad, False, manager, record)

    def test_native_order_is_independent_of_source_names(self):
        sample, context, system, installed, _ = self.fixture()
        result = observe(sample, context, system, installed)
        self.assertEqual(result['contact'], 'rail')
        self.assertEqual(result['native_node_index'], 1)
        self.assertEqual(result['rail_name'], 123)
        self.assertEqual(result['source_chain'], 'candidate0')

    def test_foreign_misaligned_or_changed_native_node_is_rejected(self):
        for field in ('foreign_manager', 'out_of_group', 'misaligned', 'name', 'position'):
            sample, context, system, installed, memory = self.fixture()
            if field in ('foreign_manager', 'out_of_group', 'misaligned'):
                pointer = 0x200c0 if field == 'out_of_group' else 0x20061 if field == 'misaligned' else 0x20060
                manager = 0x30004 if field == 'foreign_manager' else 0x30000
                memory[0x103f0] = struct.pack('<2I', pointer, manager)
            else:
                memory[0x20060][0x44 if field == 'name' else 0x20] ^= 1
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                observe(sample, context, system, installed)

    def test_ground_and_air_do_not_require_or_read_rail_memory(self):
        for state, label in ((0, 'ground'), (1, 'air')):
            self.assertEqual(observe(dict(mode=0, walking_gate=0, state=state), {}, None, None),
                             dict(contact=label))

    def dynamic_fixture(self):
        from probe_vehicle_world import VehicleWorld
        sample, context, system, installed, memory = self.fixture()
        owner = 0x40000
        memory[owner] = struct.pack('<I', 0x68f7b8)
        world = VehicleWorld.__new__(VehicleWorld)
        world.context, world.system = context, system
        world.word = lambda address: (struct.unpack_from('<I', memory[0x103f0], 4)[0] if address == 0x103f4
                                      else struct.unpack('<I', system.read(address, 4))[0])
        world.vehicles = {17: dict(owner=owner, rails=installed, external_id=73, retired=True)}
        stream = RailStream.__new__(RailStream)
        stream.context, stream.system, stream.groups = context, system, []
        stream.dynamic_rails = world
        return sample, stream, world, installed, memory

    def test_verified_retired_vehicle_rail_keeps_its_original_identity(self):
        sample, stream, _, installed, _ = self.dynamic_fixture()
        result = stream.observe(sample)
        self.assertEqual(result, dict(contact='rail', rail_node=installed['group']+0x60,
            native_node_index=1, rail_name=123, source_chain='candidate0',
            rail_manager=installed['rail_manager'], dynamic_vehicle=True,
            vehicle_id=73, vehicle_owner=0x40000))
        stream.stage = None
        stream.idle, stream.status = Mock(), Mock(return_value=dict(ready=True))
        with patch('probe_world_rails.verified_rail_identity', side_effect=AssertionError('grind graph changed')):
            self.assertEqual(stream._update({}, sample), dict(ready=True))

    def test_dynamic_delegation_keeps_manager_node_and_owner_guards(self):
        for field in ('manager', 'pointer', 'misaligned', 'name', 'position', 'owner'):
            sample, stream, _, _, memory = self.dynamic_fixture()
            if field == 'manager': memory[0x103f0] = struct.pack('<2I', 0x20060, 0x30004)
            elif field == 'pointer': memory[0x103f0] = struct.pack('<2I', 0x200c0, 0x30000)
            elif field == 'misaligned': memory[0x103f0] = struct.pack('<2I', 0x20061, 0x30000)
            elif field == 'owner': memory[0x40000] = struct.pack('<I', 0)
            else: memory[0x20060][0x44 if field == 'name' else 0x20] ^= 1
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                stream.observe(sample)

    def test_unowned_vehicle_manager_falls_back_to_strict_static_ownership(self):
        sample, stream, world, installed, _ = self.dynamic_fixture()
        world.vehicles = {}
        stream.groups = [installed]
        self.assertNotIn('dynamic_vehicle', stream.observe(sample))
        stream.dynamic_rails = None
        self.assertEqual(stream.observe(sample)['rail_name'], 123)
        stream.dynamic_rails, stream.groups = world, []
        with self.assertRaisesRegex(RuntimeError, 'unowned streaming rail graph'):
            stream.observe(sample)

    def test_nonrail_stream_observation_does_not_query_vehicle_or_static_memory(self):
        stream = RailStream.__new__(RailStream)
        stream.dynamic_rails = Mock()
        stream.dynamic_rails.observe_rail.side_effect = AssertionError('nonrail vehicle read')
        for state, label in ((0, 'ground'), (1, 'air')):
            self.assertEqual(stream.observe(dict(mode=0, walking_gate=0, state=state)), dict(contact=label))


if __name__ == '__main__':
    unittest.main()
