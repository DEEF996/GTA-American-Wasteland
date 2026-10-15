"""A worker fault must remain diagnosable after Windows releases its stack."""
import struct
import unittest
from unittest.mock import patch

import probe_loader as host


class FaultStackTests(unittest.TestCase):
    def test_reads_owned_capture_without_accessing_the_terminated_thread_stack(self):
        record = 0x21000000
        captured = tuple(range(16))

        def read(api, process, address, count):
            self.assertEqual((api, process), ("api", 42))
            if (address, count) == (record + 452, 8):
                return struct.pack("<II", 64, 1)
            self.assertEqual((address, count), (record + 256, 64))
            return struct.pack("<16I", *captured)

        with patch.object(host, "remote_read", side_effect=read):
            result = host.read_fault_stack("api", 42, record)
        self.assertTrue(result["fault_stack_captured"])
        self.assertEqual(result["fault_stack_words"], list(captured))

    def test_failed_or_partial_capture_never_returns_stale_words(self):
        for count, copied in ((0, 0), (32, 0), (32, 1), (64, 0), (128, 1)):
            with self.subTest(count=count, copied=copied), patch.object(
                host, "remote_read", return_value=struct.pack("<II", count, copied)
            ) as read:
                result = host.read_fault_stack("api", 42, 0x21000000)
                self.assertFalse(result["fault_stack_captured"])
                self.assertNotIn("fault_stack_words", result)
                read.assert_called_once_with("api", 42, 0x21000000 + 452, 8)


if __name__ == "__main__":
    unittest.main()
