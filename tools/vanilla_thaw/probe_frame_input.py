"""Declared host packets through original stock decoders and primary poll.

No fabricated devices or CPad writes. The genuine skater and camera own their
Input components; hardware polling remains unbound. The whole pinned original
image retains protected function bodies unchanged.
"""
import hashlib
import math
import struct

import probe_loader as host

PINS = {
    "digital": ("StockPacketDigital", 0x435730, 6, "aad0c5e7e8486044da631d1a2e24a756a64149eb9dd376b63151a18958c13a31"),
    "axes": ("StockPacketAxes", 0x435460, 6, "9b33e0617d3a2e281cc61802310302955e5198b831f4a18b983b7429deb463cf"),
    "dpad": ("StockPacketLeftDpad", 0x4355c0, 6, "1a3a10a816ef5ecbc9497f8a7765b44820abd6287559d002786fa5725e567081"),
    "left": ("StockPacketLeftButtons", 0x4356b0, 6, "8c88249fd10fa0e2db9a93b4ff5df987daccbb9ba83ea2970655615f70efaa37"),
    "right": ("StockPacketRightButtons", 0x435630, 6, "53378ceb9cdaa328940d6054527b41acbffabf978c0c93b6522d764a6684d4c8"),
    "poll": ("StockPrimaryInputPoll", 0x450550, 6, "5ab0223b36ac5928c4a95c1df06b84a908d7125755cba6ae9e274db0b1754ca5"),
    "lookup": ("StockInputComponentLookup", 0x42ea20, 99, "cf1c79f49f941cf12054b05277d20a51c8e8725e7a765c41118345b9dc67634d"),
}
# The unique whole caller establishes decoder order and entry identities.
# The separate registered primary callback establishes poll ABI and identity.
CORROBORATION = (
    (0x43589c, 66, "2649ba8d36f7401b6304920833668d187356d4ec2b5770d98e57878cab972a47"),
    (0x450630, 17, "75df41a601b04ee4cda57e75bc99821f259d22e6989b7f1972ce682c13a0729a"),
)
CANARY = bytes.fromhex("c7315b8da453126fa2731ba048c2719e")
INPUT_KEY = 0x27d7cd28
DIGITAL_MASK = 0x7f7ff  # Table OR0x7F6FF plus native Select branch596F64 bit0x100; no0x800.
BUTTONS = {"X": (0x40, 10, 0x1a0, 0x7323e97c), "TRIANGLE": (0x10, 8, 0x180, 0x20689278)}
ACTION_PINS = (
    (0x596580, 429, "e8aa61a67c35fb6e0b1aaa28c4bc378670c2656b1b9b3bddb8e3085aef6a42b5"),
    (0x596dd0, 1492, "74259ab70f47e61cdfa785a018e9ff73b1a1b503ff542f93fd83857bc6f88e17"),
    (0x593810, 145, "20df2fe3d20da9ba1aff6ac4eee5f5873d3152092dcfb0aa3a864a854beaaa06"),
    (0x593960, 23, "976d733ec8b8b3d8a704adac08e31f66a670078e49177956c9b69ffab367cfcd"),
    (0x6bde00, 144, "66ae925da5f517d7780080de9f300b0b995e5fbe90cd41013efec28aaa38cfe4"),
)


def packet(axes=(128, 128, 128, 128), digital_mask=0):
    host.require(isinstance(axes, (list, tuple)) and len(axes) == 4 and
                 all(type(v) is int and 0 <= v <= 255 for v in axes), "Host axes must contain four byte values")
    host.require(type(digital_mask) is int and 0 <= digital_mask <= DIGITAL_MASK and
                 digital_mask & ~DIGITAL_MASK == 0, "Unsupported original controller digital mask")
    result = bytearray(72)
    result[:4] = result[0x1d:0x21] = bytes(axes)
    struct.pack_into("<I", result, 0x24, digital_mask)
    return bytes(result)


def neutral_packet():
    return packet()


def validate_packet(value):
    host.require(isinstance(value, bytes) and len(value) == 72, "Host packet must contain exactly 72 immutable bytes")
    host.require(value == packet(tuple(value[:4]), struct.unpack_from("<I", value, 0x24)[0]),
                 "Host packet contains inconsistent axes or unsupported transport fields")


def button_state(contents, expected_name):
    host.require(len(contents) == 32, "Original button extent differs")
    held, pressed = contents[0], contents[0x10]
    pressure, released, name = (struct.unpack_from("<I", contents, at)[0] for at in (0xc, 0x14, 0x18))
    host.require(name == expected_name and held <= 1 and pressed <= 1 and released <= 1 and pressure <= 255,
                 "Original named button layout differs")
    return dict(held=bool(held), pressure=pressure, press_edge=bool(pressed), release_edge=bool(released), name_crc=name)


class InputTransport:
    def __init__(self, api, process, record, deadline, log, context):
        self.api, self.process, self.record = api, process, record
        self.deadline, self.log = deadline, log
        self.actor, self.camera = context["actor"], context["camera"]
        self.arena_start, self.arena_end = self.word(0x84376c), self.word(0x843770)
        for name, address, size, digest in PINS.values():
            host.require(hashlib.sha256(self.read(address, size)).hexdigest() == digest,
                         "Original input entry differs: " + name)
        for address, size, digest in CORROBORATION:
            host.require(hashlib.sha256(self.read(address, size)).hexdigest() == digest,
                         "Original input caller evidence differs")
        self.input = self.call("lookup", [INPUT_KEY], self.actor)
        self.camera_input = self.call("lookup", [INPUT_KEY], self.camera)
        host.require(self.input == context["components"][INPUT_KEY] and self.input != self.camera_input,
                     "Input transport must bind the two genuine separate Input components")
        self.frontend = self.word(0x7689e8)
        self.base = host.success(api.VirtualAllocEx(process, None, 4096, 0x3000, 4), "Allocate guarded host input packet")
        host.require(self.base % 16 == 0 and self.base < 0xfffff000, "Host input packet exceeds x86 space")
        self.packet = self.base + 16
        self.preflight()
        log("frame_input_initialized", actor=self.actor, input=self.input, camera=self.camera,
            camera_input=self.camera_input, original_packet_decoders=5, original_primary_poll=True,
            hardware_devices_bound=False, host_writes_only_transport_packet=True,
            stock_keyboard_action_mapping_verified=False)

    def read(self, address, size):
        return host.remote_read(self.api, self.process, address, size)

    def word(self, address):
        return struct.unpack("<I", self.read(address, 4))[0]

    def words(self, address, count):
        host.require(address % 4 == 0 and self.arena_start <= address <= self.arena_end - count * 4,
                     "Input dependency escapes the original arena")
        return struct.unpack(f"<{count}I", self.read(address, count * 4))

    def call(self, key, args=(), this=None):
        return host.native_call(self.api, self.process, PINS[key], list(args), self.record, self.deadline, self.log, this)

    def preflight(self):
        owner = self.words(self.actor, 0x35c // 4)
        host.require(owner[0] == 0x6989e8 and owner[3] == self.word(0x6dd6b0) and
                     owner[0xd8 // 4] & 255 == 1 and owner[0x29c // 4] == self.input,
                     "Original local skater/Input identity changed")
        camera = self.words(self.camera, 0x124 // 4)
        host.require(camera[0x18 // 4] == 0x967c138c and camera[3] == owner[3],
                     "Original local camera owner changed")
        for actor, component in ((self.actor, self.input), (self.camera, self.camera_input)):
            fields = self.words(component, 0x728 // 4)
            host.require(0x68e000 <= fields[0] < 0x6bc000 and fields[1] == INPUT_KEY and
                         fields[3] == actor and fields[0x718 // 4:0x720 // 4] == (0, 0),
                         "Original Input ownership changed or hardware polling became bound")
        # This UI gate belongs to Frontend Base, not the separate Game object.
        host.require(self.frontend >= 0x10000 and self.word(0x7689e8) == self.word(0x83e468) == self.frontend and
                     self.word(self.frontend) == 0x6927d8 and self.word(self.frontend + 0x8188) & 12 == 0 and
                     self.word(self.frontend + 0x818c) == 0, "Original disconnected Frontend input gate changed")

    def feed(self, packet=None):
        submitted = neutral_packet() if packet is None else packet
        validate_packet(submitted)
        self.preflight()
        host.remote_write(self.api, self.process, self.base, CANARY + submitted + CANARY)
        for decoder in ("digital", "axes", "dpad", "left", "right"):
            self.call(decoder, this=self.packet)
        decoded = self.read(self.packet, 72)
        result = []
        for actor, component in ((self.actor, self.input), (self.camera, self.camera_input)):
            suspended = self.word(actor + 0x120) & 1
            before = list(self.read(component + 0x720, 2))
            self.call("poll", [self.packet], component)
            axes = struct.unpack("<4f", self.read(component + 0x370, 16))
            host.require(all(math.isfinite(value) and abs(value) <= 1.01 for value in axes),
                         "Original Input produced invalid controller axes")
            result.append(dict(owner_suspended=bool(suspended), disabled_before=before,
                               disabled_after=list(self.read(component + 0x720, 2)), axes=list(axes)))
        host.require(self.read(self.packet, 72) == decoded and self.read(self.base, 16) == CANARY and
                     self.read(self.packet + 72, 16) == CANARY, "Native input mutated its consumed packet or escaped its extent")
        self.preflight()
        return dict(neutral=submitted == neutral_packet(), original_decoders=True, original_primary_polls=2,
                    host_pad_writes=False, actor=result[0], camera=result[1])

    def prove_buttons(self):
        """Seven bounded feeds before owner frames; no script update or CPad write.

        Requires cold X/Triangle edges in both genuine input components. Edges
        are native latches: repeated held input need not clear a prior edge.
        This proves controller buttons only, not keyboard binds or trick names.
        """
        self.preflight()
        for address, size, digest in ACTION_PINS:
            host.require(hashlib.sha256(self.read(address, size)).hexdigest() == digest,
                         "Original controller action evidence differs")

        def states():
            result = {}
            for label, component in (("actor", self.input), ("camera", self.camera_input)):
                self.words(component+0x20, 0x1c0//4)
                result[label] = {name: button_state(self.read(component+0x20+offset, 32), crc)
                                 for name, (_, _, offset, crc) in BUTTONS.items()}
            return result

        before = states()
        self.log("input_buttons_before", buttons=before,
                 owner_suspended=[bool(self.word(owner+0x120) & 1) for owner in (self.actor, self.camera)])
        host.require(all(not state["held"] and not state["press_edge"] and not state["release_edge"]
                         for owner in before.values() for state in owner.values()),
                     "Original button proof requires cold button edges; it never resets them")
        sequence = (("neutral", 0), ("X_down", 0x40), ("X_held", 0x40), ("X_up", 0),
                    ("TRIANGLE_down", 0x10), ("TRIANGLE_held", 0x10), ("TRIANGLE_up", 0))
        results = []
        for phase, mask in sequence:
            transport = self.feed(packet(digital_mask=mask))
            decoded, current = self.read(self.packet, 72), states()
            self.log("input_buttons_observed", phase_name=phase, submitted_mask=mask,
                     decoded_pressures={name: decoded[byte] for name, (_, byte, _, _) in BUTTONS.items()},
                     buttons=current, transport=transport)
            for name, (bit, byte, _, _) in BUTTONS.items():
                expected = 255 if mask & bit else 0
                host.require(decoded[byte] == expected, "Original digital decoder pressure differs for " + name)
                for owner in current.values():
                    state = owner[name]
                    host.require(state["held"] == bool(expected) and state["pressure"] == expected,
                                 "Original primary poll did not consume " + name)
                    if phase == name+"_down":
                        host.require(state["press_edge"] and not state["release_edge"], "Original press edge missing")
                    elif phase == name+"_up":
                        host.require(state["release_edge"], "Original release edge missing")
            results.append({"phase": phase, "buttons": current})
        self.preflight()
        self.log("input_buttons_pass", feeds=len(sequence), labels=list(BUTTONS),
                 decoded_controller_buttons=True, native_pressed_and_released=True,
                 direct_pad_writes=False, game_frames_executed=False,
                 keyboard_mapping_verified=False, trick_mapping_verified=False)
        return results
