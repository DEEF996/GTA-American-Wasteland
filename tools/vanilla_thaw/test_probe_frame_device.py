"""Synthetic ownership/state gates; never launch or call an original executable."""
import struct
import unittest
from unittest.mock import patch

import probe_frame_device as d


class Fixture:
    actor, camera, vibration, camera_vibration = 0x100100, 0x100500, 0x100800, 0x100900
    device, node, manager = 0x101000, 0x101200, 0x110000

    def __init__(self, camera=True, corrupt_bind=False):
        self.memory, self.calls, self.events = {}, [], []
        self.has_camera, self.corrupt_bind = camera, corrupt_bind
        self.put(0x84376c, 0x100000); self.put(0x843770, 0x120000); self.put(0x6dd6b0, self.manager)
        self.put(self.actor, 0x6989e8, 0, 0, self.manager)
        self.put(self.camera, 0x690000, 0, 0, self.manager)
        self.put(self.vibration, d.VIBRATION_VTABLE, d.VIBRATION_KEY, 0, self.actor)
        self.put(self.camera_vibration, d.VIBRATION_VTABLE, d.VIBRATION_KEY, 0, self.camera)
        self.put(self.vibration + 0x18, 1); self.put(self.camera_vibration + 0x18, 1)
        self.context = dict(actor=self.actor, camera=self.camera, components={d.VIBRATION_KEY: self.vibration})

    def put(self, address, *values):
        for i, value in enumerate(struct.pack(f"<{len(values)}I", *values)): self.memory[address + i] = value

    def read(self, address, size): return bytes(self.memory.get(address + i, 0) for i in range(size))
    def word(self, address): return struct.unpack("<I", self.read(address, 4))[0]

    def native(self, name, args=(), this=None):
        self.calls.append((name, args, this))
        if name == "Lookup":
            assert args == (d.VIBRATION_KEY,)
            if this == self.actor: return self.vibration
            assert this == self.camera
            return self.camera_vibration if self.has_camera else 0
        if name == "Allocate":
            assert args == (d.DEVICE_BYTES,) and this is None
            return self.device
        if name == "DeviceCtor":
            assert args == (0, 0, 0) and this == self.device
            self.put(self.device, d.DEVICE_VTABLE, self.node)
            self.put(self.node, d.NODE_VTABLE, self.device, 0, self.node, self.node)
            return self.device
        if name == "VibrationBind":
            assert args == (self.device,) and this in (self.vibration, self.camera_vibration)
            self.put(this + 0x34, self.device)
            if self.corrupt_bind: self.put(this + 8, 1)
            return 0
        if name == "VibrationSetEnabled":
            assert args == (0,) and this in (self.vibration, self.camera_vibration)
            self.put(this + 0x18, 0)
            self.put(this + 8, self.word(this + 8) | 1)
            self.memory[self.device + 0xc4] = 1
            return 0
        raise AssertionError("Unexpected original/native/hardware call: " + name)

    def start(self):
        return d.FrameDevice(self, self.native, self.context, lambda *args, **kwargs: self.events.append((args, kwargs)))


class FrameDeviceTests(unittest.TestCase):
    def test_actual_constructor_and_binder_contract_without_hardware_calls(self):
        for camera in (False, True):
            with self.subTest(camera=camera):
                f = Fixture(camera)
                device = f.start()
                device.validate()
                names = [row[0] for row in f.calls]
                self.assertEqual(names.count("Allocate"), 1)
                self.assertEqual(names.count("DeviceCtor"), 1)
                self.assertEqual(names.count("VibrationBind"), 1 + int(camera))
                self.assertEqual(names.count("VibrationSetEnabled"), 1 + int(camera))
                self.assertEqual(set(names), {"Lookup", "Allocate", "DeviceCtor", "VibrationBind", "VibrationSetEnabled"})
                self.assertEqual(device.node, f.node)
                self.assertFalse(f.events[0][1]["hardware_polled"])
                self.assertFalse(f.events[0][1]["original_vibration_option_enabled"])
                self.assertEqual(f.events[0][1]["camera_vibration_present"], camera)

    def test_ambient_hardware_and_foreign_component_refused_before_allocation(self):
        for address in (*d.HARDWARE_SLOTS, Fixture.vibration + 0x34, Fixture.vibration + 0xc):
            with self.subTest(address=address):
                f = Fixture(); f.put(address, 1)
                with self.assertRaises(RuntimeError): f.start()
                self.assertNotIn("Allocate", [row[0] for row in f.calls])
        f = Fixture(corrupt_bind=True)
        with self.assertRaisesRegex(RuntimeError, "outside its device pointer"): f.start()

    def test_connected_state_capabilities_motor_or_polled_node_abort_next_phase(self):
        for address in (Fixture.device + 0xd8, Fixture.device + 0xc8, Fixture.device + 0xd0,
                        Fixture.device + 0xc, Fixture.device + 0xe0, Fixture.node + 0xc,
                        Fixture.vibration + 0x34, Fixture.camera_vibration + 0xc, d.HARDWARE_SLOTS[0]):
            with self.subTest(address=address):
                f = Fixture(); device = f.start(); f.put(address, 1)
                with self.assertRaises(RuntimeError): device.validate()

    def test_replaced_owner_lookup_and_arena_escape_rejected(self):
        f = Fixture(); device = f.start()
        f.vibration += 0x40
        with self.assertRaises(RuntimeError): device.validate()
        for pointer, count in ((0, 1), (0x100001, 1), (0x11fffc, 2), (0x100000, 0)):
            with self.subTest(pointer=pointer, count=count), self.assertRaises(RuntimeError):
                device.words(pointer, count)

    def test_reenabled_vibration_or_unsuspended_component_aborts_next_phase(self):
        for address, value in ((Fixture.device + 0xc4, 0), (Fixture.vibration + 0x18, 1),
                               (Fixture.vibration + 8, 0), (Fixture.camera_vibration + 0x18, 1)):
            with self.subTest(address=address):
                f = Fixture(); device = f.start(); f.put(address, value)
                with self.assertRaises(RuntimeError): device.validate()

    def test_disabled_state_preserves_adjacent_native_flags(self):
        before = [0] * 14
        before[2], before[6] = 0x40, 0xabcd0001
        expected = d.disabled_component(before)
        self.assertEqual(expected[2], 0x41)
        self.assertEqual(expected[6], 0xabcd0000)
        self.assertEqual(d.disabled_component(expected), expected)

    def test_pin_mismatch_refuses_before_any_native_call(self):
        f = Fixture()
        with patch.object(d.host, "native_call") as call, self.assertRaisesRegex(RuntimeError, "provenance"):
            d.run(None, None, None, None, None, f.context, f)
        call.assert_not_called()


if __name__ == "__main__": unittest.main()
