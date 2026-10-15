"""Synthetic bounds/coherence tests; no GTA, assets or Windows handles required."""
import math
import struct
import unittest

import inspect_rig as rig
from test_probe_world import FakeObserver, snapshot


def matrix(position=(0., 0., 0.)):
    # Padding can contain non-float bit patterns; it must not be decoded as floats.
    return struct.pack("<3fI3fI3fI3fI", 1., 0., 0., 3,
                       0., 1., 0., 0x7fc00000, 0., 0., 1., 0x7fc00000,
                       *position, 0x7fc00000)


class FakeMemory:
    def __init__(self):
        self.blocks, self.total, self.hits, self.change_on_second = {}, 0, {}, None

    def add(self, address, data):
        self.blocks[address] = bytearray(data)

    def read(self, address, size):
        rig.address_range(address, size)
        self.total += size
        rig.require(self.total <= rig.MAX_TOTAL, "read_budget_exceeded")
        key = address, size
        self.hits[key] = self.hits.get(key, 0)+1
        for start, block in self.blocks.items():
            if start <= address and address+size <= start+len(block):
                data = bytes(block[address-start:address-start+size])
                if address == self.change_on_second and self.hits[key] > 1:
                    return bytes([data[0] ^ 1]) + data[1:]
                return data
        raise OSError("unmapped fixture range")


def fixture():
    memory = FakeMemory()
    ped, clump, frame, atomic, geometry = 0x123400, 0x200000, 0x201000, 0x202000, 0x203000
    hierarchy, skin, nodes, matrices, inverse = 0x204000, 0x205000, 0x206000, 0x207000, 0x208000
    for address, data in rig.GUARDS.items():
        memory.add(address, bytes.fromhex(data))
    memory.add(rig.FRAME_COUNTER, struct.pack("<I", 25))
    memory.add(0xB7CD74, b"\0")
    memory.add(0xB7CD98, struct.pack("<I", ped))
    memory.add(ped+0x18, struct.pack("<I", clump))
    memory.add(0xC978A4, struct.pack("<ii", 0x70, 0x60))
    memory.add(clump, struct.pack("<4I", 2, frame, atomic+0x40, atomic+0x40))
    memory.add(frame, bytes(16)+matrix()+matrix((17., 18., -4.)))
    data = bytearray(0x74)
    for at, value in ((0, 1), (0x18, geometry), (0x3C, clump), (0x40, clump+8),
                      (0x44, clump+8), (0x70, hierarchy)):
        struct.pack_into("<I", data, at, value)
    memory.add(atomic, data)
    data = bytearray(0x64)
    data[0] = 8
    struct.pack_into("<4i", data, 16, 1, 3, 1, 1)
    struct.pack_into("<I", data, 0x60, skin)
    memory.add(geometry, data)
    memory.add(hierarchy, struct.pack("<9I", 0, 2, matrices, matrices, nodes, frame, 0, 0, 0))
    memory.add(nodes, struct.pack("<4i4i", 0, 0, 2, 0, 1, 1, 1, 0))
    memory.add(matrices, matrix((17., 18., -4.))+matrix((17., 18., -3.)))
    memory.add(skin, struct.pack("<4I", 2, 0, 0, inverse))
    memory.add(inverse, matrix()+matrix((0., 0., -1.)))
    return memory


class RigTests(unittest.TestCase):
    def test_coherent_skin_report_has_ids_invariants_and_no_calibration_claim(self):
        result = rig.inspect_once(FakeObserver(snapshot(), snapshot()), fixture())
        self.assertTrue(result["accepted"])
        self.assertEqual(result["rig"]["hierarchies"][0]["count"], 2)
        nodes = result["rig"]["hierarchies"][0]["nodes"]
        self.assertEqual([item["bone_id"] for item in nodes], [0, 1])
        self.assertEqual(nodes[1]["observed_transform"]["position"], [17, 18, -3])
        affine = nodes[1]["observed_transform"]["affine_row_major"]
        self.assertEqual(affine, [1, 0, 0, 0, 0, 1, 0, 0,
                                  0, 0, 1, 0, 17, 18, -3, 1])
        self.assertEqual(nodes[1]["observed_transform"]["flags"], 3)
        self.assertEqual(result["rig"]["skins"][0]["inverse_bind_invariants"][1]["determinant"], 1)
        self.assertIsNone(result["rig"]["calibrated_scale"])
        self.assertFalse(result["rig"]["retargeting_verified"])

    def test_rejects_changed_scene_and_frame(self):
        for change, reason in ((dict(epoch=4), "scene_changed"),
                               (dict(sequence=101), "frame_advanced"),
                               (dict(frame_counter=26), "frame_advanced")):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, reason):
                rig.inspect_once(FakeObserver(snapshot(), snapshot(**change)), fixture())

    def test_rejects_mid_capture_matrix_and_pointer_changes(self):
        for address in (0x207000, 0x123418):
            memory = fixture()
            memory.change_on_second = address
            with self.subTest(address=address), self.assertRaisesRegex(ValueError, "rig_changed"):
                rig.inspect_once(FakeObserver(snapshot(), snapshot()), memory)

    def test_rejects_atomic_cycle_and_count_overflow(self):
        for address, offset, value, reason in ((0x202000, 0x40, 0x202040, "cyclic"),
                                               (0x204000, 4, 129, "hierarchy_count"),
                                               (0x205000, 0, 3, "count_mismatch")):
            memory = fixture()
            struct.pack_into("<I", memory.blocks[address], offset, value)
            if address == 0x205000:
                memory.blocks[0x208000].extend(matrix())
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                rig.inspect_once(FakeObserver(snapshot(), snapshot()), memory)

    def test_rejects_missing_guards_and_invalid_plugin_offsets(self):
        memory = fixture()
        memory.blocks[0x7C7540][0] ^= 1
        with self.assertRaisesRegex(ValueError, "getter_guard"):
            rig.inspect(FakeObserver(snapshot(), snapshot()), memory, 1)
        for value in (0, 0x71, 4097):
            memory = fixture()
            struct.pack_into("<I", memory.blocks[0xC978A4], 0, value)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "plugin_offsets"):
                rig.inspect_once(FakeObserver(snapshot(), snapshot()), memory)

    def test_node_ids_indices_flags_and_lengths_are_bounded(self):
        good = struct.pack("<4i4i", 1, 0, 2, 0, 5, 1, 1, 0)
        for offset, value in ((16, 1), (20, 0), (20, 2), (8, 4), (0, -1)):
            data = bytearray(good)
            struct.pack_into("<i", data, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                rig.decode_nodes(data, 2)
        for data, count in ((good[:-1], 2), (b"", 0), (b"", 129)):
            with self.assertRaises(ValueError):
                rig.decode_nodes(data, count)

    def test_matrix_rejects_nonfinite_singular_and_truncated_data(self):
        for offset, value in ((0, math.nan), (48, math.inf), (0, 0.), (48, 100001.)):
            data = bytearray(matrix())
            struct.pack_into("<f", data, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                rig.matrix_summary(data)
        with self.assertRaises(ValueError):
            rig.matrix_summary(matrix()[:-1])

    def test_addresses_and_attempts_are_bounded(self):
        for address, size in ((0, 4), (0x10000, 0), (0x10000, 65537), (0x7fffffff, 4)):
            with self.assertRaises(ValueError):
                rig.address_range(address, size)
        for count in (0, 121):
            with self.assertRaisesRegex(ValueError, "attempt_limit"):
                rig.inspect(FakeObserver(snapshot()), fixture(), count)


if __name__ == "__main__":
    unittest.main()
