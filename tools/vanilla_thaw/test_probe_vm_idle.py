import struct
import unittest
from unittest.mock import patch

import probe_vm_idle as idle
from import_qb import qbkey


class Memory:
    """Read-only system interface backed by explicit synthetic regions."""
    def __init__(self):
        self.regions = {}

    def put(self, address, values):
        self.regions[address] = bytearray(values if isinstance(values, bytes) else struct.pack(f'<{len(values)}I', *values))

    def read(self, address, size):
        for base, data in self.regions.items():
            if base <= address and address + size <= base + len(data):
                return bytes(data[address - base:address - base + size])
        raise OSError('Unmapped synthetic read')

    def word(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def change(self, base, offset, value):
        struct.pack_into('<I', self.regions[base], offset, value)


class IdleTests(unittest.TestCase):
    def setUp(self):
        self.pin_patch = patch.object(idle, 'PINS', ())
        self.pin_patch.start()
        self.addCleanup(self.pin_patch.stop)
        m = self.memory = Memory()
        self.actor, self.script = 0x10000100, 0x10000500
        self.cache, self.mapping, self.buckets, self.descriptor = 0x10000800, 0x10000900, 0x10001000, 0x10001c00
        self.body, self.symbols, self.event = 0x10001d00, 0x10002000, 0x10006100
        self.source, self.key = 0x20000000, 0x12345678
        self.filename = 'scripts/game/example.qb.wpc'
        self.context = dict(actor=self.actor)
        self.sources = {self.filename: (self.source, 128)}
        for address, value in ((0x84376c, 0x10000000), (0x843770, 0x10010000),
                               (0x6dcf84, self.script), (0x6dd6d0, self.event), (0x6dcf98, 0),
                               (0x6dd4a4, 0), (0x6dd6b0, 0x10006200), (0x6dcf90, 1),
                               (0x6dcf8c, self.script), (0x6dcfac, self.cache), (0x6dd0fc, self.symbols)):
            m.put(address, [value])
        m.put(self.event, bytes(40))
        m.put(self.actor, bytes(0x35c))
        for offset, value in ((0, 0x6989e8), (12, 0x10006200), (0xd8, 1)):
            m.change(self.actor, offset, value)
        m.put(self.script, bytes(0xfc))
        for offset, value in ((0x10, self.body + 4), (0xa8, 1), (0xb0, 7), (0xc4, self.actor), (0xd0, self.key)):
            m.change(self.script, offset, value)
        m.put(self.cache, [0x68edac, 0, 0, 0, 0, self.mapping, 0, 0, 0])
        m.put(self.mapping, [8, self.buckets, 1, 0, 0, 0, 0])
        m.put(self.buckets, bytes(256 * 12))
        for offset, value in ((0, self.key), (4, self.descriptor)):
            m.change(self.buckets, (self.key & 255) * 12 + offset, value)
        m.put(self.descriptor, [self.key, self.body, 1, 0, 0])
        m.put(self.body, bytes(15) + b'\x24')
        m.put(self.symbols, bytes(4096 * 4))
        m.change(self.symbols, (self.key & 4095) * 4, self.source + 28)
        m.put(self.source, b'\1' + bytes(127))
        for offset, value in enumerate((0x70400, self.key, qbkey(self.filename), self.source + 48, 0, 0, 16, 16)):
            m.change(self.source, 28 + offset * 4, value)

    def snapshot(self, sources=True):
        return idle.snapshot(self.memory, self.context, self.sources if sources else None)

    def test_waiting_owner_requires_source_and_cache_ownership_without_native_calls(self):
        result = self.snapshot()
        self.assertTrue(result['admissible'])
        self.assertTrue(result['waiting_owner_context'])
        self.assertEqual(result['source_name'], self.filename)
        self.assertEqual(result['native_calls'], 0)
        self.assertFalse(result['host_vm_context_modified'])
        self.assertFalse(self.snapshot(sources=False)['admissible'])

    def test_no_current_script_still_requires_idle_schedulers(self):
        self.memory.change(0x6dcf84, 0, 0)
        self.assertTrue(self.snapshot()['admissible'])
        self.memory.change(0x6dcf98, 0, 1)
        self.assertFalse(self.snapshot()['admissible'])

    def test_reject_active_event_unregistered_owner_and_running_state(self):
        for base, offset, value in ((self.event, 0x20, 1), (self.script, 0xc4, self.actor + 4),
                                    (self.script, 0xa8, 0), (self.script, 0xb4, 1),
                                    (self.script, 0xbc, 1), (self.script, 0xd4, 1)):
            old = self.memory.word(base + offset)
            with self.subTest(offset=offset):
                self.memory.change(base, offset, value)
                self.assertFalse(self.snapshot()['admissible'])
                self.memory.change(base, offset, old)

    def test_reject_missing_cache_reference_wrong_source_owner_and_outside_pc(self):
        for base, offset, value in ((self.descriptor, 8, 0), (self.source, 36, 1),
                                    (self.script, 0x10, self.body + 16), (self.cache, 0, 0)):
            old = self.memory.word(base + offset)
            with self.subTest(base=base, offset=offset):
                self.memory.change(base, offset, value)
                self.assertFalse(self.snapshot()['admissible'])
                self.memory.change(base, offset, old)

    def test_registry_cycles_fail_in_bounded_reads(self):
        self.memory.change(self.script, 4, self.script)
        result = self.snapshot()
        self.assertFalse(result['admissible'])
        self.assertIn('cycles', result['reason'])

    def test_aliasing_source_claims_and_wrong_cached_script_are_rejected(self):
        self.sources[self.filename.replace('/', '\\')] = (self.source, 128)
        self.assertFalse(self.snapshot()['admissible'])
        del self.sources[self.filename.replace('/', '\\')]
        self.memory.change(self.descriptor, 0, self.key + 1)
        self.assertFalse(self.snapshot()['admissible'])

    def test_require_idle_without_source_provenance_rejects_waiting_context(self):
        with self.assertRaises(RuntimeError):
            idle.require_idle(self.memory, self.context)
        self.assertTrue(idle.require_idle(self.memory, self.context, self.sources)['waiting_owner_context'])

    def test_context_changed_during_snapshot_is_rejected(self):
        original, reads = self.memory.word, 0

        def word(address):
            nonlocal reads
            if address == 0x6dcf84:
                reads += 1
                if reads > 1:
                    return 0
            return original(address)
        self.memory.word = word
        result = self.snapshot()
        self.assertFalse(result['admissible'])
        self.assertIn('changed during read', result['reason'])

    def collision_tail(self, empty_head=True):
        tail = 0x10006300
        offset = (self.key & 255) * 12
        self.memory.put(tail, [self.key, self.descriptor, 0])
        for at, value in ((0, 0 if empty_head else self.key + 256),
                          (4, 0 if empty_head else self.descriptor + 32), (8, tail)):
            self.memory.change(self.buckets, offset + at, value)
        if not empty_head:
            self.memory.change(self.mapping, 8, 2)
        return tail, offset

    def test_original_deleted_inline_head_keeps_live_collision_tail(self):
        self.collision_tail()
        result = self.snapshot()
        self.assertTrue(result['admissible'], result.get('reason'))
        self.assertTrue(result['cache_empty_inline_head'])
        self.assertEqual(self.memory.word(self.mapping + 8), 1)
        self.assertEqual(result['cache_descriptor'], self.descriptor)
        self.assertEqual(result['native_calls'], 0)

    def test_nonempty_collision_head_still_obeys_live_entry_count(self):
        self.collision_tail(empty_head=False)
        self.assertTrue(self.snapshot()['admissible'])
        self.memory.change(self.mapping, 8, 1)
        result = self.snapshot()
        self.assertFalse(result['admissible'])
        self.assertIn('live entry count', result['reason'])

    def test_empty_head_does_not_admit_malformed_or_unowned_tail(self):
        tail, offset = self.collision_tail()
        for base, at, value in ((tail, 0, self.key + 1), (tail, 4, 0),
                                (self.buckets, offset, self.key),
                                (self.buckets, offset + 4, self.descriptor),
                                (self.buckets, offset + 8, tail + 1),
                                (self.buckets, offset + 8, 0x20000000),
                                (self.buckets, offset + 8, self.buckets),
                                (self.descriptor, 8, 0)):
            old = self.memory.word(base + at)
            with self.subTest(base=base, offset=at, value=value):
                self.memory.change(base, at, value)
                self.assertFalse(self.snapshot()['admissible'])
                self.memory.change(base, at, old)

    def test_empty_head_chain_cycle_or_missing_tail_is_rejected(self):
        tail, offset = self.collision_tail()
        self.memory.change(tail, 0, self.key + 256)
        for following in (0, tail, self.buckets + offset):
            with self.subTest(following=following):
                self.memory.change(tail, 8, following)
                self.assertFalse(self.snapshot()['admissible'])

    def test_empty_head_chain_changed_during_snapshot_is_rejected(self):
        self.collision_tail()
        original, reads = self.memory.read, 0
        head = self.buckets + (self.key & 255) * 12

        def read(address, size):
            nonlocal reads
            if address == head:
                reads += 1
                if reads > 1:
                    return struct.pack('<3I', 0, 0, 0)
            return original(address, size)
        self.memory.read = read
        result = self.snapshot()
        self.assertFalse(result['admissible'])
        self.assertIn('changed during read', result['reason'])


if __name__ == '__main__':
    unittest.main()
