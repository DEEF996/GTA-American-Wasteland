"""A genuine disconnected stock Device for bounded neutral-input owner frames.

The original allocator/constructor/binder own every native field. PlatformInit
would acquire hardware (including an exclusive mouse), so it is never called.
The original Vibration component option is disabled after binding: its normal
enabled Update would call Device::ReadData even with zero motor capabilities.
No SIO probe, hardware task, Poll, GetDeviceState or force-feedback call runs.
The parent terminates the child; borrowed device/component pointers live until
then. This is not a controller bootstrap or a replacement input provider.
"""
import hashlib
import struct

import probe_loader as host

DEVICE_BYTES = 0xf4
VIBRATION_KEY = 0x404eb936
DEVICE_VTABLE, NODE_VTABLE, VIBRATION_VTABLE = 0x6a2574, 0x6a2578, 0x690ad0
HARDWARE_SLOTS = (0x84d870, 0x84d874, 0x84d878, 0x84cd18, 0x84cd1c,
                  0x84cd20, 0x84cd24, 0x845850, 0x845854, 0x6dd704, 0x6dd708)
PINS = {
    "Allocate": ("OriginalDeviceAllocate", 0x401bc0, 6, "005a9a620caae09f61f548ba2cc43c442d689e588fcfdd0a2b4af04600418bc3"),
    "DeviceCtor": ("OriginalDisconnectedDeviceCtor", 0x62ae70, 243, "34dd6c172e1a52e6ba29f809bc649d5411df852053826a6d393c5e1e51d719e6"),
    "DeviceCtorCaller": ("OriginalDeviceCtorCaller", 0x62b7d0, 38, "b1b7ef2ea61dfa6b0593f32f98550320daf69953a9009f6f7b166a6f6d5376ed"),
    "DeviceRandom": ("OriginalDeviceRandom", 0x66c2af, 35, "639e553c7c7c4730a1bb9d86227a7282801041cd82ad683c29a00d1800d23005"),
    "VibrationBind": ("OriginalDisconnectedVibrationBind", 0x475d80, 6, "485f079ccca7ec127701984db3343fb7318f36a24d627d0f8cdf864207b4cf0e"),
    "VibrationBody": ("OriginalVibrationBindBody", 0x4e78a0, 63, "135baafddcdd446596703c1235dfdd2380a8e6bea53734b68fa236f24f4b09dd"),
    "VibrationBindCaller": ("OriginalVibrationBindCaller", 0x13ed919, 27, "9acb2a570c4eb6479ff00a92ec4bb8ec813539cd7efa38195b469a3170909938"),
    "Lookup": ("OriginalDeviceComponentLookup", 0x42ea20, 99, "cf1c79f49f941cf12054b05277d20a51c8e8725e7a765c41118345b9dc67634d"),
    "ResetVibration": ("OriginalDisconnectedResetVibration", 0x62b1b0, 88, "52d84e08c6e74223c34a99db67be7b687b18cb54af7acabe210316269ed6fbe7"),
    "DisableVibration": ("OriginalDisconnectedDisableVibration", 0x62b020, 88, "8711bf867f3c2a6ccc1467f7c6326a98187861c581dba57e88dedaee6b63771a"),
    "EnableVibration": ("OriginalDisconnectedEnableVibration", 0x62a690, 8, "084001e8d37f2ed6d24f1e6e5773c520870fcaa8d4005c44d1dd7368552aa116"),
    "VibrationSetEnabled": ("OriginalVibrationComponentSetEnabled", 0x475d20, 92, "f4f5b31b10b5cf1a2ebe8f439addcb7d0b6b17fc941661360c4ae8ff603929f6"),
    "VibrationUpdate": ("OriginalVibrationComponentUpdate", 0x475c70, 100, "ac2e5078ef9f303f885cc619753d7bc887e3a4108b289993cc26e1b913c0baf4"),
    "ComponentSuspend": ("OriginalVibrationComponentSuspend", 0x42e560, 29, "2ca14ed3a0047dc2c2dd243ea9fd669ce3bf4f9bf9c9add92a764a6863d26161"),
    "DeviceVtable": ("OriginalDeviceVtable", DEVICE_VTABLE, 4, "ad8125db01077caefdf2bb5f79b2e202c7e07d9237f6e30e7bac7af9a1ef7e3a"),
    "NodeVtable": ("OriginalDeviceNodeVtable", NODE_VTABLE, 20, "3499e8e3ec95205dedbe4d27dfd57e9a3bcfae3050915b2b030aca11b57354d0"),
    "VibrationVtable": ("OriginalVibrationVtableWithSuspend", VIBRATION_VTABLE, 32, "8504b07a290b4b70d7ff6ce2c6de7f29b8485e93b971bc0039e813ba40f35ff6"),
}


def validate_disconnected(words, device):
    state = words(device, DEVICE_BYTES // 4)
    host.require(state[0] == DEVICE_VTABLE and state[3:5] == (0, 0) and
                 all(state[offset // 4] == 0 for offset in (0xc0, 0xc8, 0xd0, 0xd8, 0xdc, 0xe0)),
                 "Native Device is not the original disconnected zero-capability port zero")
    node = state[1]
    host.require(words(node, 5) == (NODE_VTABLE, device, 0, node, node),
                 "Native Device node is foreign or has been registered for hardware polling")
    return node


def disabled_component(before):
    """Expected fields changed by the original SetEnabled(false) method only."""
    expected = list(before)
    if expected[0x18 // 4] & 0xff:
        expected[0x18 // 4] &= 0xffffff00
        expected[2] |= 1
    return tuple(expected)


class FrameDevice:
    def __init__(self, system, call, context, log):
        self.system, self.call = system, call
        self.start, self.end = system.word(0x84376c), system.word(0x843770)
        host.require(0x10000 <= self.start < self.end <= 0xffffffff, "Original Device arena is absent")
        self.bindings = []
        self.device = self.node = 0
        self.hardware_idle()
        manager = system.word(0x6dd6b0)
        host.require(system.word(0x6dcf84) == 0 and system.read(0x6dcf98, 2) == bytes(2) and
                     system.read(0x6dd4a4, 1) == b"\0", "Device binding requires idle original owner/script phases")
        snapshots = []
        for label, owner, size in (("skater", context["actor"], 0x35c), ("camera", context["camera"], 0x124)):
            state = self.words(owner, size // 4)
            host.require(state[3] == manager and 0x68e000 <= state[0] < 0x6bc000,
                         "Device owner is not registered with the actual ComponentManager")
            if label == "skater": host.require(state[0] == 0x6989e8, "Device owner is not the original CSkater")
            component = call("Lookup", (VIBRATION_KEY,), owner)
            if label == "skater":
                host.require(component != 0 and component == context["components"].get(VIBRATION_KEY),
                             "Skater's original Vibration lookup differs")
            snapshots.append((owner, state))
            if component:
                values = self.words(component, 0x38 // 4)
                host.require(values[0] == VIBRATION_VTABLE and values[1] == VIBRATION_KEY and
                             values[3] == owner and values[0x34 // 4] == 0,
                             "Original Vibration component already owns a device or has another owner")
                self.bindings.append((label, owner, component, values))
        host.require(len({row[2] for row in self.bindings}) == len(self.bindings), "Owners share a Vibration component")
        self.device = call("Allocate", (DEVICE_BYTES,))
        self.words(self.device, DEVICE_BYTES // 4)
        host.require(call("DeviceCtor", (0, 0, 0), self.device) == self.device,
                     "Original Device constructor returned another allocation")
        self.node = validate_disconnected(self.words, self.device)
        for label, owner, component, before in self.bindings:
            call("VibrationBind", (self.device,), component)
            host.require(self.words(component, 0x38 // 4) == before[:-1] + (self.device,),
                         "Original Vibration binder changed fields outside its device pointer")
            validate_disconnected(self.words, self.device)
        for _, _, component, _ in self.bindings:
            before = self.words(component, 0x38 // 4)
            call("VibrationSetEnabled", (0,), component)
            host.require(self.words(component, 0x38 // 4) == disabled_component(before),
                         "Original vibration option changed fields outside enable/suspend")
            host.require(self.system.read(self.device + 0xc4, 1) == b"\1",
                         "Original vibration option did not disable the native Device motor path")
        host.require(all(self.words(owner, len(before)) == before for owner, before in snapshots),
                     "Original Device binding changed the native skater/camera owner")
        self.validate()
        log("frame_device_pass", device=self.device, node=self.node, original_device_state=0,
            original_capabilities=0, original_motor_count=0,
            owners=[row[0] for row in self.bindings], camera_vibration_present=len(self.bindings) == 2,
            whole_original_device_constructor=True, original_vibration_binder=True,
            input_source="existing neutral host packet through stock input decoder",
            platform_initialized=False, hardware_devices_enumerated=False, hardware_polled=False,
            hardware_tasks_registered=False, force_feedback_sent=False, host_device_field_writes=False,
            original_vibration_option_enabled=False, whole_original_vibration_setter=True,
            lifetime="native arena allocation retained until bounded child termination", backend_ready=False)

    def words(self, address, count):
        host.require(type(address) is int and address % 4 == 0 and 0 < count <= DEVICE_BYTES and
                     self.start <= address <= self.end - count * 4, "Device or owner leaves its original arena")
        return struct.unpack(f"<{count}I", self.system.read(address, count * 4))

    def hardware_idle(self):
        host.require(all(self.system.word(address) == 0 for address in HARDWARE_SLOTS),
                     "Disconnected proof refuses an initialized hardware/SIO/InputManager path")

    def validate(self):
        """Call between native phases, before allowing another bounded frame."""
        self.hardware_idle()
        host.require((self.system.word(0x84376c), self.system.word(0x843770)) == (self.start, self.end) and
                     validate_disconnected(self.words, self.device) == self.node and
                     self.system.read(self.device + 0xc4, 1) == b"\1",
                     "Original disconnected Device allocation or node changed")
        for _, owner, component, _ in self.bindings:
            state = self.words(component, 0x38 // 4)
            host.require(self.call("Lookup", (VIBRATION_KEY,), owner) == component and
                         state[0] == VIBRATION_VTABLE and state[1] == VIBRATION_KEY and
                         state[3] == owner and state[0x34 // 4] == self.device and
                         state[0x18 // 4] & 0xff == 0 and state[2] & 1 == 1,
                         "Original Vibration lookup, owner or disconnected Device binding changed")


def run(api, process, record, deadline, log, context, system):
    for name, address, size, digest in PINS.values():
        host.require(hashlib.sha256(system.read(address, size)).hexdigest() == digest,
                     "Original disconnected-device provenance differs: " + name)

    def call(name, args=(), this=None):
        return host.native_call(api, process, PINS[name], list(args), record, deadline, log, this)

    return FrameDevice(system, call, context, log)
