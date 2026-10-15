import struct
import unittest

import probe_animation_banks as banks


def package():
    data = bytearray(172)
    struct.pack_into('<8I', data, 0, 0x745dcd45, 96, 48, 7, 0, 0, 0, 0)
    struct.pack_into('<8I', data, 32, 0x5d796624, 112, 28, 8, 0, 0, 0, 0)
    struct.pack_into('<I', data, 64, 0xb524565f)
    struct.pack_into('<I', data, 96, 40)
    return data


class OriginalAnimationBanksTests(unittest.TestCase):
    def test_changed_flags_are_read_from_native_registry_not_source_records(self):
        memory, pointers, source = {}, {}, 0x10000
        for index, (key, offset) in enumerate(banks.FLAGS):
            pointer = source + offset if index == 0 else 0x30000 + index * 32
            pointers[key] = pointer
            memory[source + offset + 12, 4] = struct.pack('<I', 0)
            memory[pointer, 16] = struct.pack('<4I', 0x10400, key, 77, int(index != 0))
        result = banks.flag_snapshot(pointers.__getitem__, lambda address,size:memory[address,size], source)
        self.assertEqual([row['value'] for row in result], [0, 1, 1])
        self.assertEqual([row['source_value'] for row in result], [0, 0, 0])
        self.assertNotEqual(result[1]['symbol'], result[1]['source_symbol'])
        with self.assertRaises(RuntimeError):
            banks.flag_snapshot(lambda key:0, lambda address,size:bytes(size), source)

    def test_complete_member_census_and_immutable_header(self):
        data = package()
        self.assertEqual(banks.clips(data, 1), [{'key':7, 'bytes':48, 'header':data[96:116]}])
        with self.assertRaises(RuntimeError):
            banks.clips(data, 2)

    def test_truncated_unknown_or_escaping_members_rejected(self):
        cases = []
        for offset, value in ((4, 160), (8, 1000), (0, 123), (96, 41)):
            bad = package()
            struct.pack_into('<I', bad, offset, value)
            cases.append(bad)
        cases.append(package()[:150])
        for data in cases:
            with self.assertRaises(RuntimeError):
                banks.clips(data, 1)

    def test_reconstructed_or_legacy_script_is_rejected(self):
        for source in (bytes(311), bytes(banks.SOURCE_BYTES), bytes(banks.SOURCE_BYTES - 4)):
            with self.assertRaises(RuntimeError):
                banks.validate_source(source)
        for body in (bytes(1409), bytes(1408)):
            with self.assertRaises(RuntimeError):
                banks.validate_parameters(body)


if __name__ == '__main__':
    unittest.main()
