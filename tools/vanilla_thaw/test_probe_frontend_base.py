"""Synthetic candidate identity checks; no original native code is executed."""
import struct
import unittest

import probe_frontend_base as base


class BaseIdentityTests(unittest.TestCase):
    def setUp(self):
        self.address, self.source, self.barrier = 0x80000, 0x70000, 0x60000
        self.raw = bytearray(0xfe24)
        struct.pack_into("<I", self.raw, 0, 0x6927d8)
        struct.pack_into("<I", self.raw, 0x48, 0x71000)
        self.memory = {0x71000: (0, 0, 0, 0)}
        for index, offset in enumerate(base.TASK_OFFSETS):
            address, node = 0x10000 + index * 32, 0x20000 + index * 20
            struct.pack_into("<I", self.raw, offset, address)
            self.memory[address] = (0x69274c, 0, node, self.barrier if index == 0 else 0,
                                    0, 0, 0x4a1000 + index * 16, self.address)
            self.memory[node] = (0x68e494, address, 0, node, node)

    def words(self, address, count):
        return self.memory[address][:count]

    def validate(self):
        return base.validate_base(self.raw, self.address, self.source, self.words, self.barrier)

    def test_real_shape_has_eleven_distinct_owned_tasks(self):
        copied, tasks = self.validate()
        self.assertEqual(copied, 0x71000)
        self.assertEqual(len(tasks), 11)
        self.assertEqual([row["offset"] for row in tasks if row["registered"]], [8])
        self.assertEqual(tasks[0]["node"], 0x20000)

    def test_reject_wrong_class_predicate_or_source_alias(self):
        for offset, value in ((0, 0), (0x8188, 1), (0x48, self.source)):
            with self.subTest(offset=offset):
                old = self.raw[offset:offset + 4]
                struct.pack_into("<I", self.raw, offset, value)
                with self.assertRaises(RuntimeError):
                    self.validate()
                self.raw[offset:offset + 4] = old

    def test_reject_duplicate_task_wrong_owner_or_non_native_callback(self):
        first = self.memory[0x10000]
        for index, value in ((7, self.address + 4), (6, 0), (3, 0)):
            row = list(first)
            row[index] = value
            self.memory[0x10000] = tuple(row)
            with self.subTest(index=index), self.assertRaises(RuntimeError):
                self.validate()
        self.memory[0x10000] = first
        struct.pack_into("<I", self.raw, 12, 0x10000)
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_queue_cycle_is_bounded(self):
        manager = [0] * (0xcc // 4)
        manager[(4 + 0x34) // 4] = self.barrier
        root, link = self.barrier + 4, 0x50000
        self.memory[0x90000] = tuple(manager)
        self.memory[root] = (0, 0xffffffff, 0, link, link)
        self.memory[link] = (0, 0, 0, link, root)
        with self.assertRaisesRegex(RuntimeError, "cyclic"):
            base.task_queues(self.words, 0x90000)


if __name__ == "__main__":
    unittest.main()
