"""Read-only acknowledgement protocol checks; no game process is accessed."""
import ctypes as C
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import movement_ack as m


def wire(**changes):
    values = dict(magic=b'GTAMACK1', version=1, size=120, pid=17, flags=3,
        created=123, sequence=10, tick=1000, request=80, applied=9,
        source_tick=6, source_us=100000, generation=7,
        ped=0x20000, epoch=3, area=0, ped_area=0, timer=400, frame=24, entry_f7=2, f7=2)
    values.update(changes)
    return m.BODY.pack(*values.values())


def state(**changes):
    value = dict(pid=17, creation_100ns=123, ped_address=0x20000, epoch=3, area=0,
        ped_area=0, f7_requests=2, control_eligible=True, flags=8 | 64, age_ms=1)
    value.update(changes)
    return value


SAMPLE = dict(source_tick=6, source_time_us=100000)


class MovementAckTests(unittest.TestCase):
    def test_packed_fields_and_exact_same_pose_source_identity(self):
        ack = m.decode(wire(), 17, 123, 1002)
        self.assertEqual((m.BODY.size, m.MAPPING_SIZE), (120, 128))
        self.assertEqual(struct.unpack_from('<5Q', wire(), 48), (80, 9, 6, 100000, 7))
        self.assertTrue(m.matches(ack, state(), 80, 9, SAMPLE, 7))
        for change in ({'source_tick': 7}, {'source_time_us': 100001},
                       {'applied_sequence': 8}, {'request': 81}, {'collision_generation': 8},
                       {'active': False}, {'preparing': True}, {'age_ms': 101}):
            with self.subTest(change=change):
                self.assertFalse(m.matches(dict(ack, **change), state(), 80, 9, SAMPLE, 7))
        for field in ('pid', 'creation_100ns', 'ped_address', 'epoch', 'area', 'ped_area', 'f7_requests'):
            with self.subTest(field=field):
                self.assertFalse(m.matches(ack, state(**{field: state()[field]+1}), 80, 9, SAMPLE, 7))

    def test_rejects_wrong_lifetime_stale_future_or_malformed_ack(self):
        for change in ({'magic': b'WRONG123'}, {'version': 2}, {'size': 112}, {'pid': 18},
                       {'created': 124}, {'tick': 1003}, {'tick': 901}, {'sequence': 0},
                       {'flags': 8}, {'flags': m.PREPARING}, {'ped': 0}, {'epoch': 0},
                       {'area': 256}, {'ped_area': 1}, {'request': 0}, {'applied': 0},
                       {'applied': 0x3fffffff}, {'generation': 0}, {'source_tick': 0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                m.decode(wire(**change), 17, 123, 1002)
        with self.assertRaises(ValueError):
            m.decode(wire()[:-1], 17, 123, 1002)
        self.assertFalse(m.decode(wire(flags=2), 17, 123, 1002)['active'])

    def test_reader_mapping_is_read_only_and_owns_no_game_handle(self):
        memory = C.create_string_buffer(m.MAPPING_SIZE)
        C.memmove(memory, struct.pack('<I', 2)+wire()+struct.pack('<I', 2), m.MAPPING_SIZE)
        calls = []

        def exit_code(process, output):
            output._obj.value = 259
            return 1

        api = SimpleNamespace(
            OpenFileMappingW=lambda *args: calls.append(('open', args)) or 77,
            MapViewOfFile=lambda *args: calls.append(('map', args)) or C.addressof(memory),
            GetExitCodeProcess=exit_code, GetTickCount64=lambda: 1002,
            UnmapViewOfFile=lambda view: calls.append(('unmap', view)),
            CloseHandle=lambda handle: calls.append(('close', handle)))
        observer = SimpleNamespace(pid=17, created=123, process=99, api=api)
        reader = m.MovementAck(observer)
        self.assertEqual(reader.read()['applied_sequence'], 9)
        self.assertEqual(calls[0], ('open', (4, False, 'Local\\THAW_GTA_MOVE_ACK_V1_17')))
        self.assertEqual(calls[1][1][1], 4)
        C.c_uint32.from_address(C.addressof(memory)).value = 3
        with self.assertRaisesRegex(ValueError, 'No complete'):
            reader.read()
        reader.close()
        self.assertEqual([value for name, value in calls if name == 'close'], [77])
        self.assertEqual(observer.process, 99)

    def reader_for_wait(self, ack):
        reader = m.MovementAck.__new__(m.MovementAck)
        reader.observer = SimpleNamespace(read=lambda **_: state())
        reader.read = lambda **_: ack
        return reader

    def test_wait_returns_only_exact_acknowledged_sample(self):
        ack = m.decode(wire(), 17, 123, 1002)
        reader = self.reader_for_wait(ack)
        self.assertIs(reader.wait_for(80, 9, SAMPLE, generation=7), ack)
        with self.assertRaises(ValueError):
            reader.wait_for(80, 9, SAMPLE, generation=7, timeout=.1)

    def test_wait_times_out_without_replacing_pending_normal_packet(self):
        reader = self.reader_for_wait(m.decode(wire(applied=8), 17, 123, 1002))
        with patch.object(m.time, 'monotonic', side_effect=[0, 0, .025, .05]), \
                patch.object(m.time, 'sleep') as sleep, self.assertRaises(TimeoutError):
            reader.wait_for(80, 9, SAMPLE, generation=7)
        self.assertEqual(sleep.call_count, 2)
        sleep.assert_called_with(.001)

    def test_wait_rejects_f7_even_if_capture_preceded_the_wait(self):
        reader = self.reader_for_wait(m.decode(wire(f7=3), 17, 123, 1002))
        reader.observer.read = lambda **_: state(f7_requests=3)
        with self.assertRaisesRegex(ValueError, 'F7'):
            reader.wait_for(80, 9, SAMPLE, generation=7)
        reader = self.reader_for_wait(m.decode(wire(), 17, 123, 1002))
        with patch.object(reader.observer, 'read', side_effect=[state(), state(f7_requests=3)]), \
                self.assertRaisesRegex(ValueError, 'F7'):
            reader.wait_for(80, 9, SAMPLE, generation=7)

    def test_wait_rejects_release_superseded_transport_and_scene_changes(self):
        for change in ({'flags': 2}, {'request': 81}, {'applied': 10}):
            reader = self.reader_for_wait(m.decode(wire(**change), 17, 123, 1002))
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'released or superseded'):
                reader.wait_for(80, 9, SAMPLE, generation=7)
        reader = self.reader_for_wait(m.decode(wire(), 17, 123, 1002))
        with patch.object(reader.observer, 'read', side_effect=[state(), state(epoch=4)]), \
                self.assertRaisesRegex(ValueError, 'scene'):
            reader.wait_for(80, 9, SAMPLE, generation=7)


if __name__ == '__main__':
    unittest.main()
