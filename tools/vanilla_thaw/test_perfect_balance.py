import struct
import unittest

from perfect_balance import PerfectBalance, FLAGS


class OriginalMemory:
    def __init__(self):
        self.memory = bytearray(0x10000)
        self.calls = []
        self.corrupt = False
        self.write(0x10000, struct.pack('<9I', 0x69807c, *([0] * 7), 0x10100))
        self.write(0x10100, struct.pack('<2I', 0x698a50, 0x10200))
        self.write(0x10200, struct.pack('<4I', 0x691d94, 0, 0x10300, 32))
        self.write(0x10300 + 196 // 8, bytes([1 << (196 % 8)]))  # Unrelated skitch flag.

    def write(self, address, raw):
        self.memory[address - 0x10000:address - 0x10000 + len(raw)] = raw

    def read(self, address, size):
        if address == 0x84376c:
            return struct.pack('<2I', 0x10000, 0x20000)
        return bytes(self.memory[address - 0x10000:address - 0x10000 + size])

    def word(self, address):
        return 0x10000 if address == 0x83e81c else struct.unpack('<I', self.read(address, 4))[0]

    def call(self, name, args=(), this=None):
        self.calls.append((name, args, this))
        if name == 'BalanceGlobalInteger':
            return dict(FLAGS)[args[0]]
        assert this == 0x10200
        index = args[0]
        offset, mask = 0x300 + index // 8, 1 << (index % 8)
        if name == 'BalanceBitSet':
            self.memory[offset] |= mask
        elif name == 'BalanceBitClear':
            self.memory[offset] &= ~mask
        else:
            raise AssertionError(name)
        if self.corrupt:
            self.memory[0x300] = 1


class PerfectBalanceTests(unittest.TestCase):
    def create(self):
        s = OriginalMemory()
        return s, PerfectBalance(s, s.call, lambda: b'closed clock')

    def test_toggle_uses_original_calls_only_and_restores_exact_flags(self):
        s, option = self.create()
        original = bytes(s.memory)
        self.assertFalse(option.set_enabled(False)['changed'])
        self.assertEqual(len(s.calls), 2)
        self.assertTrue(option.set_enabled(True)['changed'])
        self.assertEqual(s.calls[-2:], [('BalanceBitSet', (187,), 0x10200), ('BalanceBitSet', (191,), 0x10200)])
        for _, index in FLAGS:
            self.assertTrue(s.memory[0x300 + index // 8] & (1 << (index % 8)))
        self.assertFalse(option.set_enabled(True)['changed'])
        self.assertTrue(option.set_enabled(False)['changed'])
        self.assertEqual(bytes(s.memory), original)
        self.assertEqual(s.calls[-2:], [('BalanceBitClear', (187,), 0x10200), ('BalanceBitClear', (191,), 0x10200)])

    def test_invalid_toggle_and_owner_changes_cannot_write(self):
        for invalid in (0, 1, 'on', None):
            s, option = self.create()
            with self.assertRaises(RuntimeError):
                option.set_enabled(invalid)
            self.assertEqual(len(s.calls), 2)
        for address, value in ((0x10100, 0), (0x10208, 0x30000), (0x1020c, 33)):
            s, option = self.create()
            s.write(address, struct.pack('<I', value))
            with self.assertRaises(RuntimeError):
                option.set_enabled(True)
            self.assertEqual(len(s.calls), 2)

    def test_unexpected_existing_flag_or_alias_rejected(self):
        s = OriginalMemory()
        s.call('BalanceBitSet', (187,), 0x10200)
        with self.assertRaisesRegex(RuntimeError, 'already'):
            PerfectBalance(s, s.call, lambda: b'closed clock')
        s = OriginalMemory()
        with self.assertRaisesRegex(RuntimeError, 'alias'):
            PerfectBalance(s, lambda *args: 999, lambda: b'closed clock')

    def test_native_readback_and_closed_frame_required(self):
        s, option = self.create()
        s.corrupt = True
        with self.assertRaisesRegex(RuntimeError, 'unrelated'):
            option.set_enabled(True)
        self.assertFalse(option.enabled)
        s, option = self.create()
        clock = iter((b'closed1', b'closed2'))
        option.validate = lambda: next(clock)
        with self.assertRaisesRegex(RuntimeError, 'clock'):
            option.set_enabled(True)
        self.assertFalse(option.enabled)


if __name__ == '__main__':
    unittest.main()
