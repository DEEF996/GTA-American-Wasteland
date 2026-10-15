import unittest

import probe_hud


class OriginalHudTests(unittest.TestCase):
    def test_incomplete_or_synthetic_source_is_rejected(self):
        for source in (bytes(13672), bytes(13668), bytes(13676)):
            with self.assertRaises(RuntimeError):
                probe_hud.validate_source(source)

    def test_whole_original_spawn_abi_and_source_identity(self):
        self.assertEqual(probe_hud.SPAWN[1:3], (0x413380, 441))
        self.assertEqual(probe_hud.probe_native.qbkey(probe_hud.SOURCE_NAME), 0xe00eeae3)
        self.assertEqual(probe_hud.probe_native.qbkey("the_balance_meter"), probe_hud.BALANCE_ID)
        self.assertEqual(probe_hud.probe_native.qbkey("tag_turned_on"), probe_hud.TAG_TURNED_ON)


if __name__ == "__main__":
    unittest.main()
