import ctypes as C
from ctypes import wintypes as W
import struct
import unittest
from keyboard_input import KeyboardInput, InputUnavailable, validate_bindings


class Observer:
    pid = 42
    numlock = True
    def read(self):
        return dict(flags=8 | (128 if self.numlock else 0), control_eligible=True)


class Keys:
    def __init__(self):
        self.owner, self.numlock, self.polls = 42, 1, []
        self.held = {0x65, 0x1f, 0x20}
    def GetForegroundWindow(self): return 7
    def GetWindowThreadProcessId(self, window, owner):
        C.cast(owner, C.POINTER(W.DWORD))[0] = self.owner
        return 1
    def GetKeyboardLayout(self, thread): return 1
    def MapVirtualKeyExW(self, scan, kind, layout): return scan
    def GetKeyState(self, key): return self.numlock
    def GetAsyncKeyState(self, key):
        self.polls.append(key)
        return 0x8000 if key in self.held else 0


class KeyboardTest(unittest.TestCase):
    def test_simultaneous_direction_and_ollie_without_text(self):
        keys = Keys()
        result = KeyboardInput(Observer(), keys).read()
        self.assertEqual(tuple(result.packet[:4]), (128, 128, 255, 255))
        self.assertEqual(struct.unpack_from('<I', result.packet, 0x24)[0], 0x40)
        self.assertFalse(result.recenter_camera)
        self.assertNotIn(0x43, keys.polls)  # unbound C is never inspected

    def test_focus_and_numlock_fail_before_polling(self):
        for owner, numlock in ((99, 1), (42, 0)):
            keys = Keys()
            keys.owner, keys.numlock = owner, numlock
            observer = Observer()
            observer.numlock = bool(numlock)
            with self.assertRaises(InputUnavailable): KeyboardInput(observer, keys).read()
            self.assertEqual(keys.polls, [])

    def test_space_uses_the_original_walk_or_skate_context(self):
        keys = Keys()
        keys.held = {0x39}
        result = KeyboardInput(Observer(), keys).read()
        self.assertTrue(result.recenter_camera)
        self.assertEqual(struct.unpack_from('<I', result.packet, 0x24)[0], 0)
        result = KeyboardInput(Observer(), keys).read(skating=True)
        self.assertFalse(result.recenter_camera)
        self.assertEqual(struct.unpack_from('<I', result.packet, 0x24)[0], 0xc)

    def test_rebinding_uses_physical_keys_and_keeps_diagonal_grab(self):
        keys = Keys()
        keys.held = {0x2e, 0x1f, 0x1e}  # C + S + A
        keyboard = KeyboardInput(Observer(), keys)
        keyboard.bindings = validate_bindings({'Grab': 6, 'Backward': 22, 'Left': 4})
        result = keyboard.read()
        self.assertEqual(tuple(result.packet[:4]), (128,128,0,255))
        self.assertEqual(struct.unpack_from('<I', result.packet, 0x24)[0], 0x20)
        self.assertEqual(set(keys.polls), keys.held)

    def test_rebinding_rejects_toggle_menu_and_conflicts(self):
        for binding in ({'Grab':13}, {'Grab':65}, {'Grab':83}, {'Grab':88},
                        {'Grab':True}, {'Grab':6,'Flip':6}, {'Invented':6}):
            with self.subTest(binding=binding), self.assertRaises(ValueError):
                validate_bindings(binding)

    def test_skitch_release_consumes_remaining_chord_keys_until_all_released(self):
        keys=Keys();keyboard=KeyboardInput(Observer(),keys)
        keys.held={0x65,0x68,0x39,0x11}
        self.assertTrue(keyboard.read(skating=True).skitch_requested)
        for held in ({0x65,0x68,0x11},{0x65,0x11},{0x11}):
            keys.held=held;result=keyboard.read(skating=True)
            self.assertFalse(result.skitch_requested)
            self.assertEqual(struct.unpack_from('<I',result.packet,0x24)[0],0)
            self.assertEqual(tuple(result.packet[:4]),(128,128,128,0))
        keys.held={0x65}
        self.assertEqual(struct.unpack_from('<I',keyboard.read(skating=True).packet,0x24)[0],0x40)

    def test_shared_physical_binding_keeps_chord_release_latched(self):
        keys=Keys();keyboard=KeyboardInput(Observer(),keys)
        keyboard.bindings={'Ollie':93,'Grind':93,'CameraRecenter':44}
        keys.held={0x65,0x39}
        self.assertTrue(keyboard.read(skating=True).skitch_requested)
        keys.held={0x65}
        result=keyboard.read(skating=True)
        self.assertFalse(result.skitch_requested)
        self.assertEqual(struct.unpack_from('<I',result.packet,0x24)[0],0)


if __name__ == '__main__': unittest.main()
