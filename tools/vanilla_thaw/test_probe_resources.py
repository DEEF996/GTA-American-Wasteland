"""Pure GPU readback validation tests; no OS or game code is executed."""
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import probe_resources as r


class TextureFixture:
    scratch = 0x50000

    def __init__(self, corrupt=False, failed_read=False, bad_format=False):
        self.calls, self.logs = [], []
        self.failed_read = failed_read
        self.width, self.pitch = 8, 40
        self.expected = bytes(range(64))
        pixels = bytearray(self.expected[:32] + bytes(8) + self.expected[32:] + bytes(8))
        if corrupt:
            pixels[43] ^= 1
        self.memory = {0x20000: struct.pack("<6I", 0x695430, 0, 0, 0, 0x21000, 0),
                       0x21000: struct.pack("<11I", 0, 0, 0, 0, 0, 0x30000, 0, 0, 0, 0, 0),
                       0x40000: bytes(pixels)}
        self.format = 0 if bad_format else 0x35545844

    def write(self, address, data):
        self.memory[address] = data

    def read(self, address, size):
        if self.failed_read and address == 0x40000 + self.pitch:
            raise RuntimeError("simulated remote read failure")
        for base, contents in self.memory.items():
            if base <= address and address + size <= base + len(contents):
                return contents[address - base:address - base + size]
        raise RuntimeError("fixture read outside allocation")

    def log(self, phase, **values):
        self.logs.append((phase, values))

    def com(self, name, object_address, slot, arguments=()):
        self.calls.append(slot)
        assert object_address == 0x30000
        if slot == 17:
            self.write(self.scratch, struct.pack("<8I", self.format, 3, 0, 1, 0, 0, 8, 8))
        elif slot == 13:
            return 1
        elif slot == 19:
            self.write(self.scratch, struct.pack("<iI", self.pitch, 0x40000))
        elif slot != 20:
            raise AssertionError("unexpected GPU method")
        return 0


class ResourceTests(unittest.TestCase):
    def test_platform_profile_keeps_abi_and_only_logs_slow_calls(self):
        for enabled, step, slow in ((False, .003, False), (True, .001, False), (True, .003, True)):
            with self.subTest(enabled=enabled, step=step):
                platform = r.Platform.__new__(r.Platform)
                platform.record, platform.deadline, platform.log = 0x30000, 123., Mock()
                code = r.platform_stub(0x10000, (7,), platform.record)
                platform.worker = SimpleNamespace(tid=44, check_call_access=Mock(),
                    prepare_command=Mock(return_value=0x40000), execute=Mock(return_value=(0, 0)))
                platform.write = Mock()
                record = struct.pack('<18I', 6, *([0]*16), 5)
                platform.read = Mock(side_effect=lambda at, size: record if at == platform.record else code)
                clock = iter(index*step for index in range(20))
                with patch.object(r.host, 'PROFILE_RESOURCE_CALLS', enabled), \
                     patch.object(r.time, 'perf_counter', side_effect=lambda: next(clock)) as timer:
                    self.assertEqual(platform.invoke('SetFrequency', 0x10000, (7,)), 5)
                platform.worker.prepare_command.assert_called_once_with(code)
                platform.worker.check_call_access.assert_called_once()
                rows = [call.kwargs for call in platform.log.call_args_list
                        if call.args == ('native_slow_platform_call',)]
                self.assertEqual(bool(rows), slow)
                if enabled:
                    self.assertIsInstance(platform.worker.execute.call_args.kwargs['timings'], dict)
                else:
                    timer.assert_not_called()
                    platform.worker.execute.assert_called_once_with(0x40000, 123.)
                if slow:
                    self.assertEqual(rows[0]['function'], 'SetFrequency')
                    self.assertTrue({'prepare_ms', 'execute_ms', 'post_guard_ms'} <= rows[0].keys())

    def check(self, fixture):
        r.gpu_pixels(fixture, 0x20000, 8, fixture.expected, 0x20000, 0x22000)

    def test_readback_ignores_row_padding_and_always_unlocks(self):
        fixture = TextureFixture()
        self.check(fixture)
        self.assertEqual(fixture.calls, [17, 13, 19, 20])
        self.assertTrue(fixture.logs[0][1]["pixels_bit_identical"])

    def test_changed_pixel_is_rejected_after_unlock(self):
        fixture = TextureFixture(corrupt=True)
        with self.assertRaises(RuntimeError):
            self.check(fixture)
        self.assertEqual(fixture.calls[-1], 20)
        self.assertEqual(fixture.logs, [])

    def test_failed_read_still_unlocks(self):
        fixture = TextureFixture(failed_read=True)
        with self.assertRaisesRegex(RuntimeError, "simulated remote read"):
            self.check(fixture)
        self.assertEqual(fixture.calls[-1], 20)

    def test_wrong_format_rejects_before_lock(self):
        fixture = TextureFixture(bad_format=True)
        with self.assertRaises(RuntimeError):
            self.check(fixture)
        self.assertEqual(fixture.calls, [17, 13])


if __name__ == "__main__":
    unittest.main()
