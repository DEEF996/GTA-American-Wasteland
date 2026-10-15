"""Future NodeArray resources never parse/activate before matching COL publication."""
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_audio_bank import Memory
import test_world_rails
from probe_world_rails import RailStream, prepare_rail_validation, verified_rail_identity, memory_resource_input


class RailPreloadTests(unittest.TestCase):
    def fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        s = RailStream.__new__(RailStream)
        s.directory, s.system = Path(temp.name), Memory()
        s.context = {'actor': 0x10000, 'components': {0x5bd63b29: 0x60000}}
        s.rail_manager, s.params = 0x20000, 0x30000
        s.node_manager = 0x70000
        s.resource_manager, s.process = 0x90000, 123
        s.system.put(0x6dd3f4, s.resource_manager, 1)
        s.system.allocate = lambda data: (s.system.write(0xa0000, data) or 0xa0000)
        s.free = Mock(return_value=1)
        s.scene_native = SimpleNamespace(references=SimpleNamespace(blocks=lambda: []))
        s.words = lambda address, count: struct.unpack('<'+str(count)+'I', s.system.read(address, count*4))
        s.current = dict(key='old-empty', nodes=2, group=0x50000, nodes_by_name={})
        s.groups = [s.current]
        s.headers = lambda: [dict(group=0x50000)]
        s.last_key, s.stage, s.pending, s.prefetched = 'old-empty', None, None, None
        s._forecast_inputs = s._forecast_key = s._verified_inputs = s._verified_key = None
        s.poisoned = False
        s.idle, s.parameters, s.log = Mock(), Mock(), Mock()
        s.observe = Mock()
        future = test_world_rails.WorldRailObservationTests().prepared_fixture()
        s.system.put(0x40000, 0xa0100, future['rails']['report']['node_count'], 0)
        import rail_export
        filename = 'gta_thaw/streamed-rails/'+future['rails']['report']['basename']+'.qb.Wpc'
        s.system.put(s.node_manager+0xe04, 0x80000)
        fields = [0]*13
        fields[0], fields[8], fields[9], fields[10], fields[11] = (
            0x68f060, rail_export.qbkey(filename), future['rails']['report']['array_checksum'], 0x40000, 1)
        s.system.put(0x80000, *fields)
        def native(name, args=(), this=None):
            if name == 'NodeArrayFindNamed': return 0x40000
            if name == 'NodeArrayMemoryLoad':
                s.system.put(0x80008, 0xb0000, 0xb0000, 0)
                s.system.put(0x80018, len(future['rails']['source']))
                return 0xb0000
            return 1
        s.call = Mock(side_effect=native)
        old = dict(col_sha256='old', rails=dict(source=None, report=None))
        return s, old, future

    def test_preload_and_original_frames_do_not_parse_future_or_change_current_graph(self):
        s, old, future = self.fixture()
        self.assertFalse(s.prewarm(future))  # Immutable memory input only.
        s.call.assert_not_called()
        s.system.put(s.context['actor'], 10)  # Another completed original frame.
        self.assertTrue(s.prewarm(future))
        self.assertEqual([c.args[0] for c in s.call.call_args_list],
                         ['NodeArrayMemoryLoad', 'NodeArrayFindNamed'])
        source = s.prefetched
        for frame in range(11, 15):
            s.system.put(s.context['actor'], frame)
            self.assertTrue(s.update(old)['ready'])
            self.assertTrue(s.prewarm(future))
        self.assertIs(s.prefetched, source)
        self.assertEqual(s.last_key, 'old-empty')
        self.assertIsNone(s.pending)
        self.assertEqual(s.call.call_count, 2)
        self.assertTrue(source['loaded'])

    def test_matching_published_col_consumes_loaded_resource_without_second_load(self):
        s, _, future = self.fixture()
        s.prewarm(future); s.prewarm(future)
        s.system.put(s.context['actor'], 77)
        self.assertFalse(s._update(future, None)['ready'])
        self.assertEqual(s.stage, 'parse')
        self.assertIsNone(s.prefetched)
        self.assertEqual(s.pending['key'], verified_rail_identity(future))
        self.assertEqual(struct.unpack_from('<I', s.pending['actor_before'])[0], 77)
        self.assertEqual([c.args[0] for c in s.call.call_args_list].count('NodeArrayMemoryLoad'), 1)
        self.assertEqual(s.last_key, 'old-empty')

    def test_owner_survives_an_unrelated_later_load_and_cancellation(self):
        s, _, future = self.fixture()
        s.prewarm(future); s.prewarm(future)
        resource = s.prefetched
        self.assertEqual((resource['asset'], resource['array']), (0x80000, 0x40000))
        for last_loaded in (0x81000, 0):
            s.system.put(s.node_manager+0xe04, last_loaded)
            self.assertEqual(s._resource_owner(resource), (0x80000, 0x40000))
        s.prewarm(None)  # Unloads the retained verified resource, not the latest asset.
        self.assertIsNone(s.prefetched)
        self.assertIn('NodeArrayManUnload', [c.args[0] for c in s.call.call_args_list])

    def test_retained_owner_rejects_reused_array_pointer_path_type_and_resource_flag(self):
        for offset in (0, 0x20, 0x24, 0x28, 0x2c):
            with self.subTest(offset=offset):
                s, _, future = self.fixture()
                s.prewarm(future); s.prewarm(future)
                s.system.put(0x80000+offset, 99)
                with self.assertRaisesRegex(RuntimeError, 'resource identity'):
                    s._resource_owner(s.prefetched)
        s, _, future = self.fixture()
        s.prewarm(future); s.prewarm(future)
        s.system.put(0x41000, 0xa0100, future['rails']['report']['node_count'], 0)
        s.call.side_effect = lambda name, *args: 0x41000
        with self.assertRaisesRegex(RuntimeError, 'retained array ownership'):
            s._resource_owner(s.prefetched)

    def test_reusing_an_existing_group_validates_its_own_asset_not_last_loaded(self):
        s, _, future = self.fixture()
        s.prewarm(future); s.prewarm(future)
        existing = dict(s.prefetched, group=0x51000, nodes=future['rails']['report']['node_count'])
        s.prefetched = None
        s.groups.append(existing)
        s.system.put(s.node_manager+0xe04, 0x81000)
        s.queue_activation = Mock(return_value={'ready': True})
        self.assertTrue(s._update(future, None)['ready'])
        self.assertIs(s.current, existing)
        s.queue_activation.assert_called_once()

    def test_active_grind_preserves_original_group_and_prefetched_resource(self):
        s, _, future = self.fixture()
        s.prewarm(future); s.prewarm(future)
        resource = s.prefetched
        sample = dict(mode=0, walking_gate=0, state=4, walk_state=6)
        self.assertTrue(s.update(future, sample)['ready'])
        self.assertIs(s.prefetched, resource)
        self.assertEqual(s.last_key, 'old-empty')
        s.observe.assert_called_once_with(sample)
        self.assertNotIn('ParseNodeArray', [c.args[0] for c in s.call.call_args_list])

    def test_changed_or_cancelled_forecast_unloads_only_unparsed_owned_resource(self):
        for loaded in (False, True):
            with self.subTest(loaded=loaded):
                s, old, future = self.fixture()
                s.prewarm(future)
                if loaded: s.prewarm(future)
                path = s.prefetched['path']
                self.assertFalse(s.prewarm(old))
                self.assertIsNone(s.prefetched)
                self.assertFalse(path.exists())
                calls = [c.args[0] for c in s.call.call_args_list]
                self.assertEqual(calls.count('NodeArrayManUnload'), int(loaded))
                self.assertNotIn('RailOrphanCleanup', calls)
                self.assertNotIn('ParseNodeArray', calls)
                self.assertEqual(s.last_key, 'old-empty')
                self.assertEqual(s.groups, [s.current])
                self.assertFalse(s.prewarm(None))

    def test_forecast_never_interrupts_actual_native_installation(self):
        s, _, future = self.fixture()
        s.stage = 'parse'
        self.assertFalse(s.prewarm(future))
        self.assertIsNone(s.prefetched)
        s.idle.assert_not_called(); s.call.assert_not_called()

    def test_noop_forecast_does_not_repeat_current_frame_native_checks(self):
        s, old, future = self.fixture()
        self.assertTrue(s.update(old)['ready'])
        s.idle.reset_mock()
        s.prewarm(None)
        s.idle.assert_not_called()
        s.prewarm(future); s.prewarm(future)
        s.idle.reset_mock(); s.call.reset_mock()
        for _ in range(60):
            self.assertTrue(s.prewarm(future))
        s.idle.assert_not_called(); s.call.assert_not_called()
        # Cancellation still checks live state before unloading its resource.
        s.prewarm(None)
        self.assertGreater(s.idle.call_count,0)
        self.assertIn('NodeArrayManUnload',[c.args[0] for c in s.call.call_args_list])

    def test_preload_validation_is_reused_only_for_same_retained_worker_object(self):
        from copy import deepcopy
        s, old, future = self.fixture()
        with patch('probe_world_rails.verified_rail_identity', wraps=verified_rail_identity) as verify:
            s.prewarm(future); s.prewarm(future)
            self.assertEqual(verify.call_count,1)
            self.assertFalse(s._update(future,None)['ready'])
            self.assertEqual(verify.call_count,1)
        # A new worker result with matching hash still validates its full seal.
        s, old, future = self.fixture()
        future['rails']['validation']=prepare_rail_validation(future)
        s.prewarm(future)
        changed=deepcopy(future)
        changed['rails']['report']['nodes'][0]['position_thaw'][0]+=1
        with self.assertRaisesRegex(RuntimeError,'binding changed'):
            s._update(changed,None)

    def test_current_object_forecast_reuses_validation_but_new_object_checks(self):
        s, old, _ = self.fixture()
        with patch('probe_world_rails.verified_rail_identity', wraps=verified_rail_identity) as verify:
            self.assertTrue(s.update(old)['ready'])
            self.assertFalse(s.prewarm(old))
            self.assertEqual(verify.call_count,1)
            self.assertFalse(s.prewarm(dict(old)))
            self.assertEqual(verify.call_count,2)

    def test_native_preload_mutation_poisons_service_without_resuming_source(self):
        for address in (0x10000, 0x20000, 0x603f0, 0x6dcf84, 0x842b18+0xc8):
            with self.subTest(address=address):
                s, _, future = self.fixture()
                s.prewarm(future)
                def corrupt(name, *args):
                    s.system.put(address, 99)
                    return 1
                s.call.side_effect = corrupt
                with self.assertRaises(RuntimeError): s.prewarm(future)
                self.assertTrue(s.poisoned)
                with self.assertRaisesRegex(RuntimeError, 'poisoned'): s.prewarm(future)

    def test_memory_load_uses_original_copy_descriptor_and_releases_only_transfer(self):
        s, _, future = self.fixture()
        s.prewarm(future)
        resource = s.prefetched
        payload = memory_resource_input(resource)
        self.assertEqual(payload[:0x80].split(b'\0', 1)[0],
                         (resource['native_filename']+'.Wpc').encode('ascii'))
        self.assertEqual(payload[0x80:0x94], bytes(20))
        self.assertEqual(payload[0x94:], future['rails']['source'])
        self.assertFalse(resource['path'].exists())
        self.assertTrue(s.prewarm(future))
        import rail_export
        self.assertEqual(s.call.call_args_list[0].args, ('NodeArrayMemoryLoad', (
            rail_export.qbkey(resource['native_filename']), 13, 0, 0xa0094,
            len(future['rails']['source']), 0, 1, 0xa0000, 0), s.resource_manager))
        s.free.assert_called_once_with(s.process, 0xa0000, 0, 0x8000)
        self.assertEqual(s.system.read(0xa0000, len(payload)), payload)
        self.assertEqual(s.system.read(0x83f1b0, len(resource['native_filename'])+1),
                         resource['native_filename'].encode('ascii')+b'\0')

    def test_changed_memory_payload_or_path_is_rejected_before_native_allocation(self):
        for field in ('bytes', 'size', 'key', 'parent_path', 'embedded_nul', 'long_name'):
            s, _, future = self.fixture()
            s.prewarm(future)
            resource = s.prefetched
            if field == 'bytes': resource['source'] = resource['source'][:-1]+b'x'
            elif field == 'size': resource['report']['qb_bytes'] += 1
            elif field == 'key': resource['key'] = '0'*64
            else:
                resource['report']['basename'] = {'parent_path': '../x', 'embedded_nul': 'x\0',
                                                  'long_name': 'x'*120}[field]
                resource['native_filename'] = 'gta_thaw/streamed-rails/'+resource['report']['basename']+'.qb'
            s.system.allocate = Mock(side_effect=AssertionError('unsafe allocation'))
            with self.subTest(field=field), self.assertRaises(RuntimeError): s._load_resource(resource)
            s.system.allocate.assert_not_called(); s.free.assert_not_called()

    def test_memory_timeout_or_bad_native_copy_never_frees_unproven_live_input(self):
        for defect in ('timeout', 'alias_input', 'size', 'descriptor', 'manager', 'global_name'):
            s, _, future = self.fixture()
            s.prewarm(future)
            native = s.call.side_effect
            def changed(name, args=(), this=None):
                value = native(name, args, this)
                if name == 'NodeArrayMemoryLoad':
                    if defect == 'timeout': raise RuntimeError('original timeout')
                    if defect == 'alias_input':
                        value = 0xa0094
                        s.system.put(0x80008, value, value)
                    elif defect == 'size': s.system.put(0x80018, 1)
                    elif defect == 'descriptor': s.system.put(0x80010, 0xa0000)
                    elif defect == 'manager': s.system.put(0x6dd3f4, 0x91000)
                    elif defect == 'global_name': s.system.write(0x83f1b0, b'x')
                return value
            s.call.side_effect = changed
            with self.subTest(defect=defect), self.assertRaises(RuntimeError): s.prewarm(future)
            self.assertTrue(s.poisoned)
            s.free.assert_not_called()

    def test_required_tile_uses_same_memory_loader_and_original_parse_phase(self):
        s, _, future = self.fixture()
        self.assertFalse(s._update(future, None)['ready'])
        self.assertEqual(s.stage, 'load')
        self.assertFalse(s._update(future, None)['ready'])
        self.assertEqual(s.stage, 'parse')
        self.assertFalse(s._update(future, None)['ready'])
        self.assertEqual(s.stage, 'verify')
        self.assertEqual([c.args[0] for c in s.call.call_args_list], [
            'NodeArrayMemoryLoad', 'NodeArrayFindNamed', 'NodeArrayFindNamed', 'ParseNodeArray'])


if __name__ == '__main__':
    unittest.main()
