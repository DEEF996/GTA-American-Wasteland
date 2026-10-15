import unittest
from types import SimpleNamespace

from probe_gameplay_options import GameplayOptions, AerialGesture, aerial_direction
from probe_frame_input import packet
import struct


class GameplayOptionsTests(unittest.TestCase):
    def controller(self, *, flipping=False, valid_readback=True):
        value = GameplayOptions.__new__(GameplayOptions)
        value.enabled, value.params = True, {True: 100, False: 200}
        value.context = {'components': {0x5bd63b29: 400}}
        value._validate = lambda: b'clock'
        calls = []
        value.log = lambda *args, **kwargs: None
        value.gesture = AerialGesture(lambda _: True)
        value.system = SimpleNamespace(read=lambda *_: bytes([flipping]),
                                       word=lambda _: 0 if valid_readback else 8)
        def call(name, args=(), this=None):
            calls.append((name, args))
            return 1 if name == 'Change' else 800
        value._call = call
        return value, calls

    def test_invalid_and_active_flip_settings_cannot_mutate_table(self):
        value, calls = self.controller(flipping=True)
        for invalid in (1, 0, 'on', None):
            with self.assertRaises(RuntimeError):
                value.set_aerial_flips(invalid)
        self.assertTrue(value.set_aerial_flips(False)['deferred'])
        self.assertTrue(value.enabled)
        self.assertEqual(calls, [])

    def test_held_diagonal_grab_uses_original_events_without_changing_camera(self):
        prepared = []
        gesture = AerialGesture(lambda direction: prepared.append(direction) or True)
        source = packet((10, 230, 0, 255), 0x20 | 8)
        sample = dict(mode=0, walking_gate=0, state=1)
        masks = []
        for frame in range(6):
            result = gesture.map(source, sample)
            self.assertEqual(result[:2], source[:2])
            self.assertEqual(result[2:4], bytes([128,128]) if frame in (0,2) else source[2:4])
            masks.append(struct.unpack_from('<I', result, 0x24)[0])
        self.assertEqual(prepared, [(-1, 1)])
        self.assertEqual(masks, [0x28, 0x4028, 0x28, 0x4028, 0x28, 0x28])

    def test_landing_and_disable_cancel_gesture_and_walk_does_not_trigger(self):
        prepared = []
        gesture = AerialGesture(lambda direction: prepared.append(direction) or True)
        source = packet((128,128,255,128), 0x20)
        walking = dict(mode=1, walking_gate=0, state=1)
        self.assertEqual(gesture.map(source, walking), source)
        self.assertEqual(prepared, [])
        gesture.reset()
        air = dict(mode=0, walking_gate=0, state=1)
        gesture.map(source, air)
        self.assertIsNotNone(gesture.pending)
        self.assertEqual(gesture.map(source, air, enabled=False), source)
        self.assertIsNone(gesture.pending)

    def test_idempotent_option_does_not_replace_live_table(self):
        value, calls = self.controller(flipping=True)
        self.assertFalse(value.set_aerial_flips(True)['changed'])
        self.assertEqual(calls, [])

    def test_native_acknowledgement_precedes_published_option(self):
        value, calls = self.controller()
        self.assertTrue(value.set_aerial_flips(False)['changed'])
        self.assertFalse(value.enabled)
        self.assertEqual(calls[0], ('Change', (200, 0)))
        value, calls = self.controller(valid_readback=False)
        with self.assertRaisesRegex(RuntimeError, 'readback'):
            value.set_aerial_flips(False)
        self.assertTrue(value.enabled)


if __name__ == '__main__':
    unittest.main()
