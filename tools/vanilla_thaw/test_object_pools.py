import unittest
import struct
from probe_object_pools import RELAYS, validate_default, validate_relay_targets


class DefaultPoolTest(unittest.TestCase):
    def test_exact_native_relay_transition_order_and_targets(self):
        memory = {slot: struct.pack('<I', cold) for slot, cold, _ in RELAYS.values()}
        read = lambda address, count: memory[address][:count]
        validate_relay_targets(read, 'before')
        for stage in ('factory', 'lookup'):
            with self.assertRaises(RuntimeError): validate_relay_targets(read, stage)
        slot, _, resolved = RELAYS['constructor']
        memory[slot] = struct.pack('<I', resolved)
        validate_relay_targets(read, 'factory')
        for stage in ('before', 'lookup'):
            with self.assertRaises(RuntimeError): validate_relay_targets(read, stage)
        slot, _, resolved = RELAYS['lookup']
        memory[slot] = struct.pack('<I', resolved)
        validate_relay_targets(read, 'lookup')
        for stage in ('before', 'factory', 'unknown'):
            with self.assertRaises(RuntimeError): validate_relay_targets(read, stage)
        for slot, _, resolved in RELAYS.values():
            for invalid in (0, resolved+1, 0x12345678):
                memory[slot] = struct.pack('<I', invalid)
                with self.subTest(slot=slot, target=invalid), self.assertRaises(RuntimeError):
                    validate_relay_targets(read, 'lookup')
            memory[slot] = struct.pack('<I', resolved)

    def test_constructed_defaults_and_uninitialized_name(self):
        values = [0]*28
        for offset, value in {0:0x48d610, 0x20:2, 0x28:5, 0x2c:5,
            0x38:1, 0x3c:0x3dcccccd, 0x48:0x80, 0x50:0xf4240, 0x6c:0xffffffff}.items():
            values[offset//4] = value
        values[1], values[3] = 0x100000, 0x100100
        values[0x68//4] = 0xdeadbeef
        validate_default(values)
        for offset in (0, 0x20, 0x44, 0x50):
            changed = values.copy()
            changed[offset//4] ^= 1
            with self.assertRaises(RuntimeError):
                validate_default(changed)
        with self.assertRaises(RuntimeError):
            validate_default(values[:-1])


if __name__ == '__main__':
    unittest.main()
