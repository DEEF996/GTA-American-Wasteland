"""Host input boundaries and decoded button layout; no native execution."""
import struct
import unittest
from unittest.mock import patch

import probe_frame_input as p


class FrameInputTests(unittest.TestCase):
    def test_neutral_default_is_byte_identical_to_previous_transport(self):
        previous = bytearray(72)
        previous[:4] = previous[0x1d:0x21] = bytes([128]*4)
        self.assertEqual(p.neutral_packet(), bytes(previous))
        self.assertEqual(p.packet(), bytes(previous))
        p.validate_packet(p.neutral_packet())

    def test_axes_and_controller_masks_touch_only_declared_host_fields(self):
        raw = p.packet((0, 255, 64, 192), 0x50)
        self.assertEqual(raw[:4], raw[0x1d:0x21])
        self.assertEqual(struct.unpack_from("<I", raw, 0x24)[0], 0x50)
        self.assertTrue(all(raw[i] == 0 for i in range(72) if i not in (*range(4), *range(29,33), *range(36,40))))
        p.validate_packet(raw)
        for axes in ((0,0,0), (0,0,0,256), (0,0,0,-1), (0,0,0,True), (0,0,0,.5)):
            with self.assertRaises(RuntimeError):p.packet(axes)
        for mask in (-1,0x800,0x80000,True,1.5):
            with self.assertRaises(RuntimeError):p.packet(digital_mask=mask)

    def test_malformed_packet_fails_before_any_process_access(self):
        altered = bytearray(p.neutral_packet()); altered[4] = 1
        bad_axes = bytearray(p.neutral_packet()); bad_axes[29] = 1
        transport = p.InputTransport.__new__(p.InputTransport)
        with patch.object(p.host, "remote_write") as write:
            for value in (b"", bytes(71), bytes(altered), bytes(bad_axes), bytearray(p.neutral_packet())):
                with self.assertRaises(RuntimeError):transport.feed(value)
            write.assert_not_called()

    def test_button_layout_uses_native_name_pressure_and_latched_edges(self):
        raw = bytearray(32)
        raw[0] = raw[0x10] = 1
        struct.pack_into("<I",raw,0xc,255)
        struct.pack_into("<I",raw,0x18,0x7323e97c)
        state = p.button_state(raw,0x7323e97c)
        self.assertTrue(state["held"] and state["press_edge"])
        self.assertFalse(state["release_edge"])
        raw[0] = 0
        struct.pack_into("<I",raw,0xc,0)
        struct.pack_into("<I",raw,0x14,1)
        state = p.button_state(raw,0x7323e97c)
        self.assertTrue(state["release_edge"] and state["press_edge"])
        self.assertFalse(state["held"])
        for invalid, name in ((raw[:31],0x7323e97c),(raw,0x20689278)):
            with self.assertRaises(RuntimeError):p.button_state(invalid,name)


if __name__ == "__main__":
    unittest.main()
