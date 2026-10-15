"""Concurrent mapping publication without any real process, input or game writes."""
import ctypes as C
from contextlib import ExitStack
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from native import inspect_state as state_map
from keyboard_input import KeyboardInput, InputUnavailable
from streamed_scene import StreamedScene
from world_stream import StaleRegion
import movement_ack as ack_map
from test_keyboard_input import Keys
from test_movement_ack import wire as ack_wire, SAMPLE


def state_wire(**changes):
    fields = dict(magic=b'GTATHAW2', version=2, size=272, pid=17, capabilities=3,
        created=123, sequence=10, tick=1000, ped=0x20000, epoch=3, area=0, ped_area=0,
        timer=400, frame=24, f7=2, flags=1 | 8 | 16 | 64 | 128,
        x=1., y=2., z=3., vx=.1, vy=.2, vz=.3, heading=.5, health=100., step=1.,
        error=0, profile=b'I:\\GTA_THAW\\thaw_gta\\profile\0', hour=18, minute=34, second=56,
        controls=0, cutscene=0, motion=1)
    fields.update(changes)
    return state_map.BODY.pack(*fields.values())


def publish(memory, body, generation=2):
    C.memmove(memory, struct.pack('<I', generation)+body+struct.pack('<I', generation), len(body)+8)


class SnapshotReadTests(unittest.TestCase):
    def setUp(self):
        self.now, self.exit_code, self.yields = 0., 259, 0
        self.after_yield = lambda: None
        self.state_memory = C.create_string_buffer(state_map.MAPPING_SIZE)
        self.ack_memory = C.create_string_buffer(ack_map.MAPPING_SIZE)
        publish(self.state_memory, state_wire())
        publish(self.ack_memory, ack_wire())
        def alive(process, output):
            output._obj.value = self.exit_code
            return 1
        self.api = SimpleNamespace(GetExitCodeProcess=alive, GetTickCount64=lambda: 1002)
        self.observer = state_map.Observer.__new__(state_map.Observer)
        self.observer.pid, self.observer.created, self.observer.process = 17, 123, 99
        self.observer.exe = Path(r'I:\GTA_THAW\gta_sa.exe')
        self.observer.api, self.observer.view = self.api, C.addressof(self.state_memory)
        self.ack = ack_map.MovementAck.__new__(ack_map.MovementAck)
        self.ack.observer, self.ack.api, self.ack.view = self.observer, self.api, C.addressof(self.ack_memory)
        scope = ExitStack()
        self.addCleanup(scope.close)
        scope.enter_context(patch.object(state_map.time, 'monotonic', side_effect=lambda: self.now))
        def yield_writer(seconds):
            self.now += seconds
            self.yields += 1
            self.after_yield()
        scope.enter_context(patch.object(state_map.time, 'sleep', side_effect=yield_writer))

    def test_state_and_ack_wait_for_odd_writer_and_return_new_complete_sample(self):
        for reader, memory, body in ((self.observer, self.state_memory, state_wire(sequence=11, f7=3)),
                                     (self.ack, self.ack_memory, ack_wire(sequence=11, f7=3))):
            with self.subTest(reader=type(reader).__name__):
                C.c_uint32.from_buffer(memory).value = 3
                self.after_yield = lambda: publish(memory, body, 4)
                result = reader.read()
                self.assertEqual((result['sequence'], result['f7_requests']), (11, 3))
        self.assertEqual(self.yields, 2)

    def test_torn_copy_is_discarded_before_retrying_current_generation(self):
        original_copy = C.string_at
        copied = []
        def concurrent_copy(*args):
            body = original_copy(*args)
            if not copied:
                publish(self.state_memory, state_wire(sequence=11, epoch=4), 4)
            copied.append(body)
            return body
        with patch.object(state_map.C, 'string_at', side_effect=concurrent_copy):
            result = self.observer.read()
        self.assertEqual((result['sequence'], result['epoch']), (11, 4))
        self.assertEqual((len(copied), self.yields), (2, 1))

    def test_contention_has_bounded_typed_failure_and_respects_callers_deadline(self):
        C.c_uint32.from_buffer(self.state_memory).value = 3
        with self.assertRaises(state_map.SnapshotBusy):
            self.observer.read()
        self.assertAlmostEqual(self.now, .02)
        self.now = 0.
        with self.assertRaises(state_map.SnapshotBusy):
            self.observer.read(deadline=.003)
        self.assertAlmostEqual(self.now, .003)

    def test_process_exit_during_writer_wait_is_not_mistaken_for_contention(self):
        C.c_uint32.from_buffer(self.state_memory).value = 3
        self.after_yield = lambda: setattr(self, 'exit_code', 0)
        with self.assertRaisesRegex(ValueError, 'process ended') as error:
            self.observer.read()
        self.assertNotIsInstance(error.exception, state_map.SnapshotBusy)
        self.assertEqual(self.yields, 1)

    def test_complete_invalid_or_stale_snapshots_are_never_retried_or_cached(self):
        for reader, memory, body in ((self.observer, self.state_memory, state_wire(pid=18)),
                (self.observer, self.state_memory, state_wire(tick=1)),
                (self.ack, self.ack_memory, ack_wire(pid=18)),
                (self.ack, self.ack_memory, ack_wire(tick=1))):
            publish(memory, body)
            with self.subTest(reader=type(reader).__name__), self.assertRaises(ValueError) as error:
                reader.read()
            self.assertNotIsInstance(error.exception, state_map.SnapshotBusy)
        self.assertEqual(self.yields, 0)

    def test_keyboard_path_recovers_but_still_obeys_new_control_state(self):
        keys = Keys()
        keys.owner = self.observer.pid
        keyboard = KeyboardInput(self.observer, keys)
        C.c_uint32.from_buffer(self.state_memory).value = 3
        self.after_yield = lambda: publish(self.state_memory, state_wire(sequence=11), 4)
        self.assertEqual(struct.unpack_from('<I', keyboard.read().packet, 0x24)[0], 0x40)
        C.c_uint32.from_buffer(self.state_memory).value = 5
        self.after_yield = lambda: publish(self.state_memory, state_wire(sequence=12, controls=1), 6)
        with self.assertRaises(InputUnavailable):
            keyboard.read()
        self.assertEqual(self.yields, 2)

    def test_streamed_scene_recovers_concurrent_copy_but_rejects_actual_hour_transition(self):
        scene = StreamedScene.__new__(StreamedScene)
        scene.observer = self.observer
        def ipls():
            C.c_uint32.from_buffer(self.state_memory).value = 3
            return dict(active_streams=[])
        scene.ipls = SimpleNamespace(read=ipls)
        self.after_yield = lambda: publish(self.state_memory, state_wire(sequence=11), 4)
        state, _ = scene._environment()
        self.assertEqual(state['sequence'], 11)
        self.after_yield = lambda: publish(self.state_memory, state_wire(sequence=12, hour=19), 6)
        with self.assertRaisesRegex(StaleRegion, 'changed during IPL'):
            scene._environment()
        self.assertEqual(self.yields, 2)

    def test_wait_for_recovers_both_busy_maps_and_keeps_exact_source_ack(self):
        C.c_uint32.from_buffer(self.state_memory).value = 3
        C.c_uint32.from_buffer(self.ack_memory).value = 3
        def finish():
            if self.yields == 1:
                publish(self.state_memory, state_wire(sequence=11), 4)
            else:
                publish(self.ack_memory, ack_wire(sequence=11), 4)
        self.after_yield = finish
        result = self.ack.wait_for(80, 9, SAMPLE, generation=7)
        self.assertEqual((result['sequence'], result['source_tick']), (11, 6))
        self.assertEqual(self.yields, 2)

    def test_wait_for_persistent_busy_ack_still_expires_at_shared_50ms_deadline(self):
        C.c_uint32.from_buffer(self.ack_memory).value = 3
        with self.assertRaises(TimeoutError):
            self.ack.wait_for(80, 9, SAMPLE, generation=7)
        self.assertAlmostEqual(self.now, .05)


if __name__ == '__main__':
    unittest.main()
