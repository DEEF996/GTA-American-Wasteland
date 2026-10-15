import struct
import unittest

import probe_lighting as lighting


def constructor_fixture():
    raw = bytearray(4 * 0x90)
    for group in range(4):
        for offset in (0, 0x1c, 0x38, 0x54, 0x70):
            struct.pack_into('<7I', raw, group * 0x90 + offset, 0x80484848, 0, 0, 0x3f000000, 0, 0, 0)
        struct.pack_into('<I', raw, group * 0x90 + 0x8c, 0xffffffff)
    return raw


class LightingTests(unittest.TestCase):
    def test_all_twenty_constructor_records_and_indices_required(self):
        raw = constructor_fixture()
        lighting.constructed_records(lambda address, size: bytes(raw))
        for group in range(4):
            for offset in (0, 4, 8, 0xc, 0x14, 0x18, 0x1c, 0x38, 0x54, 0x70, 0x8c):
                changed = bytearray(raw)
                changed[group * 0x90 + offset] ^= 1
                with self.assertRaises(RuntimeError):
                    lighting.constructed_records(lambda address, size: bytes(changed))

    def test_complete_stock_two_light_arrays_and_null_rejection(self):
        globals_, allocations = {}, {}

        def allocate(values):
            pointer = 0x20000000 + len(allocations) * 64
            allocations[pointer] = tuple(values)
            return pointer

        def words(pointer, count):
            self.assertIn(pointer, allocations)
            self.assertEqual(len(allocations[pointer]), count)
            return allocations[pointer]

        for group in range(4):
            record = lighting.LIGHTS + group * 0x90
            globals_.update({record:0x800000ff, record + 0xc:0x3f000000, record + 0x8c:0xffffffff,
                             record + 4:allocate((0, 0x3f800000, 0, 0x3f800000) * 2),
                             record + 8:allocate((0x80ff0000,) * 2),
                             record + 0x14:allocate((0, 0)), record + 0x18:allocate((0, 0))})
            for sub in range(4):
                at = record + 0x1c + sub * 0x1c
                globals_.update({at:0x80484848, at + 0xc:0x3f000000})
                for offset, count in ((4, 8), (8, 2), (0x14, 2), (0x18, 2)):
                    globals_[at + offset] = allocate((0,) * count)
        globals_[0x768d20] = allocate((0x3f800000,) * 2)
        globals_[0x768d18] = allocate((0, 0x20010000, 0) * 4 + (5,))
        lighting.validate_arrays(None, globals_.__getitem__, words)
        direction = globals_[lighting.LIGHTS + 4]
        allocations[direction] = (0,) * 8
        with self.assertRaises(RuntimeError):
            lighting.validate_arrays(None, globals_.__getitem__, words)
        allocations[direction] = (0, 0x3f800000, 0, 0x3f800000) * 2
        globals_[lighting.LIGHTS + 4] = 0
        with self.assertRaises(AssertionError):
            lighting.validate_arrays(None, globals_.__getitem__, words)

    def test_changed_or_reconstructed_whole_source_rejected(self):
        for size in (0, 5955, 5956):
            with self.assertRaises(RuntimeError):
                lighting.validate_source(bytes(size))


if __name__ == '__main__':
    unittest.main()
