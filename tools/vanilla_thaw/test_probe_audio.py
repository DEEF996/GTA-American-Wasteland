"""Synthetic audio ownership/ABI gates; no device, child or game is launched."""
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import probe_audio as a


class Fixture:
    window, manager, device = 0x777, 0x101000, 0x301000

    def __init__(self):
        self.memory, self.calls, self.events = {}, [], []
        self.worker = SimpleNamespace(tid=123)
        self.next_buffer, self.get_caps_result, self.hardware_caps = 0x401000, 0, 0
        self.owner_tid, self.corrupt_owner = 123, False
        self.put(0x84376c, 0x100000, 0x120000)
        self.put(0x844230, 7)
        self.put(0x6c0584, 0x101)
        self.put(0x8505f0, 0x201000)
        self.put(0x8505ec, 0x201004)
        self.put(0x201000, 0x400123)

    def put(self, address, *values):
        self.write(address, struct.pack(f"<{len(values)}I", *values))

    def write(self, address, contents):
        for index, value in enumerate(contents): self.memory[address + index] = value

    def read(self, address, size): return bytes(self.memory.get(address + i, 0) for i in range(size))
    def word(self, address): return struct.unpack("<I", self.read(address, 4))[0]
    def allocate(self, contents):
        address = self.next_buffer; self.next_buffer += 4096
        self.write(address, contents)
        return address

    def call(self, module, name, args):
        assert module == "user32.dll" and args[0] == self.window
        return {"IsWindow": 1, "IsWindowVisible": 0, "GetWindowThreadProcessId": self.owner_tid}[name]

    def com(self, label, device, slot, args):
        assert (label, device, slot) == ("IDirectSound8::GetCaps", self.device, 4)
        assert self.word(args[0]) == 96
        self.put(args[0] + 8 * 4, 32)
        self.put(args[0] + 14 * 4, self.hardware_caps)
        return self.get_caps_result

    def native(self, name, args=()):
        self.calls.append((name, args))
        if name == "MutexCRT":
            assert args == ()
            self.put(a.MUTEX, 0x888)
            self.put(0x201004, a.MUTEX_EXIT)
            self.put(0x8505ec, 0x201008)
        elif name == "DeviceInit":
            assert args == (self.window,)
            self.put(a.DEVICE, self.device)
        elif name == "Manager":
            assert args == (1,)
            self.put(a.MANAGER, self.manager)
            self.put(a.REFERENCES, 1)
            self.put(a.VOICES, 27)
            if self.corrupt_owner: self.put(0x6dcf8c, 0x100123)
            return self.manager
        else:
            raise AssertionError("Unexpected native or playback call: " + name)
        return 0

    def start(self):
        return a.initialize(self, self.native, lambda *args, **kwargs: self.events.append((args, kwargs)))


class AudioTests(unittest.TestCase):
    def test_original_calls_and_window_preserved_without_bank_or_field_writes(self):
        f = Fixture(); result = f.start()
        self.assertEqual(f.calls, [("MutexCRT", ()), ("DeviceInit", (f.window,)), ("Manager", (1,))])
        self.assertEqual(result, dict(manager=f.manager, device=f.device, voice_capacity=27))
        self.assertEqual(f.word(0x84aa7c), 0)
        self.assertEqual(a.callbacks(f), (0x400123, a.MUTEX_EXIT))
        self.assertFalse(f.events[-1][1]["worker_handle_liveness_verified"])
        self.assertFalse(f.events[-1][1]["global_bank_loaded"])

    def test_existing_service_busy_vm_or_foreign_window_refuses_before_native(self):
        for address in (a.MUTEX, a.DEVICE, a.MANAGER, a.REFERENCES, a.SAMPLES, 0x6dcf84, 0x6dcf98, 0x6dd4a4):
            with self.subTest(address=address):
                f = Fixture(); f.put(address, 1)
                with self.assertRaises(RuntimeError): f.start()
                self.assertEqual(f.calls, [])
        f = Fixture(); f.owner_tid = 456
        with self.assertRaisesRegex(RuntimeError, "hidden window"): f.start()
        self.assertEqual(f.calls, [])

    def test_failed_or_hardware_caps_refuse_before_original_manager(self):
        for attribute, value in (("get_caps_result", 0x80004005), ("hardware_caps", 1)):
            f = Fixture(); setattr(f, attribute, value)
            with self.assertRaisesRegex(RuntimeError, "GetCaps|software-only"): f.start()
            self.assertNotIn("Manager", [name for name, _ in f.calls])

    def test_original_cap_branch_boundaries_and_invalid_struct_size(self):
        for actual, expected in ((0, 48), (16, 48), (17, 12), (53, 48), (54, 48), (0x7fffffff, 48)):
            caps = [0] * 24; caps[0], caps[8] = 96, actual
            self.assertEqual(a.software_voice_capacity(0, struct.pack("<24I", *caps)), expected)
        with self.assertRaises(RuntimeError): a.software_voice_capacity(0, bytes(96))
        with self.assertRaises(RuntimeError): a.software_voice_capacity(0, bytes(92))
        caps[8] = 0xffffffff
        with self.assertRaises(RuntimeError): a.software_voice_capacity(0, struct.pack("<24I", *caps))

    def test_foreign_owner_mutation_or_unbounded_crt_refused(self):
        f = Fixture(); f.corrupt_owner = True
        with self.assertRaisesRegex(RuntimeError, "owner identity"): f.start()
        for end in (0x201003, 0x202000, 0x200ffc):
            f = Fixture(); f.put(0x8505ec, end)
            with self.assertRaisesRegex(RuntimeError, "CRT exit table"): f.start()
            self.assertEqual(f.calls, [])

    def test_pin_mismatch_refuses_before_native(self):
        f = Fixture()
        with patch.object(a.host, "native_call") as call, self.assertRaisesRegex(RuntimeError, "provenance"):
            a.run(None, None, None, None, None, f)
        call.assert_not_called()


if __name__ == "__main__": unittest.main()
