import unittest

import probe_local_player as player


class LocalPlayerTests(unittest.TestCase):
    def test_local_client_requires_native_zero_field_and_all_ownership_guards(self):
        fields = [0] * (0x2018 // 4)
        fields[0], fields[0x1e9c // 4], fields[0x1458 // 4] = 0x68fb10, 0x29, 0x300000
        player.validate_client(fields, 0x200000, 0x300000, 1, 0x200000, 1)
        player.validate_client(fields, 0x200000, 0x300000, 1, 0x200000, 0x10000001)
        for offset, value in ((0, 0), (0x1428, 0xffffffff), (0x1458, 0), (0x1e9c, 0x21)):
            with self.subTest(offset=offset):
                changed = fields.copy()
                changed[offset // 4] = value
                with self.assertRaises(RuntimeError):
                    player.validate_client(changed, 0x200000, 0x300000, 1, 0x200000, 1)
        for count, base_client, flag in ((0, 0x200000, 1), (1, 0, 1), (1, 0x200000, 0)):
            with self.subTest(count=count, base_client=base_client, flag=flag):
                with self.assertRaises(RuntimeError):
                    player.validate_client(fields, 0x200000, 0x300000, count, base_client, flag)

    def fixture(self):
        fields = [0] * (0x1f0 // 4)
        fields[:5] = [0x6974a0, 0x100000, 0, 0x100000, 0x100000]
        fields[0x34 // 4], fields[0x44 // 4] = 1, 0x200000
        return fields

    def test_original_local_player_identity_owner_and_empty_connection(self):
        fields = self.fixture()
        player.validate_player(tuple(fields), 0x100000, 0x200000, (0x100000, 0x100000))
        for offset in (0, 4, 8, 12, 16, 0x14, 0x34, 0x44, 0x1ec):
            with self.subTest(offset=offset):
                changed = fields.copy()
                changed[offset // 4] ^= 1
                with self.assertRaises(RuntimeError):
                    player.validate_player(tuple(changed), 0x100000, 0x200000, (0x100000, 0x100000))

    def test_registered_player_requires_both_links_to_actual_base_sentinel(self):
        fields = self.fixture()
        fields[3:5] = [0x30004c, 0x30004c]
        player.validate_player(tuple(fields), 0x100000, 0x200000, (0x30004c, 0x30004c))
        with self.assertRaises(RuntimeError):
            player.validate_player(tuple(fields), 0x100000, 0x200000, (0x100000, 0x100000))

    def test_only_explicit_native_base_ownership_slots_may_change(self):
        before = (0,) * 12
        after = list(before)
        after[2] = 1
        player.unchanged_except(before, after, {8})
        with self.assertRaises(RuntimeError):
            player.unchanged_except(before, after, {12})
        with self.assertRaises(RuntimeError):
            player.unchanged_except(before, after + [0], {8})


if __name__ == '__main__':
    unittest.main()
