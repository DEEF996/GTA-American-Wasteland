"""Pure authored-bank and native-state boundary tests; no original execution."""
import struct
import unittest
from unittest.mock import patch

import probe_audio_bank as b


def fixture():
    source, bank, nodes, fields = bytearray(68), bytearray(), [], []
    struct.pack_into('<5I', source, 28, 0xc0400, b.ARRAY_KEY, b.SOURCE_KEY, 48, 0)
    struct.pack_into('<3I', source, 48, 0xa0100, 2, 60)
    for index in range(2):
        nodes.append(len(source))
        source.extend(struct.pack('<2I', 0x10000, len(source) + 8))
        row = [(0x300, 0xa6f5352f, len(bank)), (0x300, 0x083fdb95, 128),
               (0x300, 0x21902065, index + 1), (0x300, 0xa80bea4a, 44100),
               (0x300, 0xf6a36814, 100 + index * 20)]
        if index: row += [(0x1b00, 0, 0xc8d4b9e2), (0x1b00, 0, 0x8b87176f)]
        positions = []
        for j, (kind, key, value) in enumerate(row):
            positions.append(len(source))
            source.extend(struct.pack('<4I', kind, key, value, len(source) + 16 if j + 1 < len(row) else 0))
        fields.append(positions)
        bank.extend(struct.pack('<HHIIHHH', 1, 1, 44100, 88200, 2, 16, 0) + bytes(110) + bytes([index + 1]) * 128)
    struct.pack_into('<2I', source, 60, *nodes)
    source.extend(struct.pack('<5I', 0x10400, b.SIZE_KEY, b.SOURCE_KEY, len(bank), 0))
    struct.pack_into('<I', source, 4, len(source))
    return source, bank, fields


class Memory:
    def __init__(self): self.data = {}
    def put(self, address, *values): self.write(address, struct.pack(f'<{len(values)}I', *values))
    def write(self, address, values): self.data.update((address + i, v) for i, v in enumerate(values))
    def read(self, address, count): return bytes(self.data.get(address + i, 0) for i in range(count))
    def word(self, address): return struct.unpack('<I', self.read(address, 4))[0]


class StreamFixture(Memory):
    def __init__(self):
        super().__init__()
        self.calls, self.status, self.foreign_config = [], 259, False
        self.put(0x7740e0, *b.CONFIGURATION)
        self.put(b.probe_audio.MUTEX, 1); self.put(b.probe_audio.MANAGER, 0x10000); self.put(b.probe_audio.DEVICE, 0x20000)
        self.put(0x6c0584, 0x101); self.put(0x844230, 7)
        self.put(0x6c05a0, 0xffffffff); self.put(0x6c05a4, 0xffffffff)
    def allocate(self, value): self.write(0x50000, value); return 0x50000
    def call(self, module, name, args):
        assert (module, name, args[0]) == ('kernel32.dll', 'GetExitCodeThread', 0x123)
        self.put(args[1], self.status); return 1
    def native(self, name, args=()):
        self.calls.append((name, args))
        if name == 'Configuration': return 0x7740e0
        assert name == 'StreamInit' and args == (3, 3)
        self.put(0x6fa5f0, 3); self.put(0x6fe7f8, 3); self.put(0x764de4, 0x123)
        if self.foreign_config: self.put(0x7740e0, 59)
    def run(self): b.initialize_stream(self, self.native, lambda *args, **kwargs: None)


class HeapFixture(Memory):
    manager, heap, arena = 0x842b18, 0x102000, 0x103000
    def __init__(self):
        super().__init__()
        self.calls, self.existing, self.corrupt = [], 0, None
        self.put(0x84376c, 0x100000, 0x2000000); self.put(0x84388c, self.manager)
        self.write(0x6a22d8, b'Audio\0')
        self.put(self.manager, 0x101000, 0x101100); self.put(self.manager + 0xc0, 2)
        for index in (0, 2):
            self.put(self.manager + 0x18c + index * 0x50 + 8, index + 10)
            self.write(self.manager + 0x18c + index * 0x50 + 0x4c, b'\1')
    def native(self, name, args=(), this=None):
        self.calls.append((name, args, this))
        if name == 'NamedHeapLookup':
            assert args == (0x6153ca47, 0) and this == self.manager
            return self.existing
        if name == 'HeapFreeBytes':
            assert args == () and this == self.heap
            return 0 if self.corrupt == 'capacity' else 0x1000000
        assert name == 'NamedHeapCreate' and args == (0x6153ca47, 0x1000000, 0x6a22d8, 0) and this == self.manager
        self.existing = self.heap
        slot = self.manager + 0x18c + 0x50
        self.put(slot, self.arena, self.heap, 0x6153ca47); self.write(slot + 12, b'Audio\0'); self.write(slot + 0x4c, b'\1')
        self.put(self.manager + 8, self.heap); self.put(self.manager + 0xc0, 3)
        self.put(self.heap + 0x24, self.arena)
        self.put(self.arena, 0x6a2354, 0x200000, 1, 0x1000000, 1, 0x1200000, 0, 0)
        if self.corrupt == 'old_slot': self.put(self.manager + 0x18c + 8, 999)
        if self.corrupt == 'context': self.put(self.manager + 0x10c, 1)
        if self.corrupt == 'name': self.write(slot + 12, b'Wrong\0')
        return self.heap
    def run(self): return b.initialize_audio_heap(self, self.native, lambda *args, **kwargs: None)


class AudioBankTests(unittest.TestCase):
    def file_service(self):
        f = Memory()
        f.put(0x84376c, 0x100000, 0x110000)
        f.put(0x844400, *(0x100000 + i * 0x100 for i in range(16)))
        # Open idle handles are not outstanding requests.
        f.put(0x844440, 2)
        return f

    def test_completed_file_notification_is_not_pending_work(self):
        for notification in (0, 1):
            f = self.file_service()
            f.write(0x844448, bytes([notification, 0xff, 0xff, 0xff]))
            rows = []
            b.files_idle(f, lambda phase, **row: rows.append(row))
            self.assertEqual(rows[0]['completion_notification'], notification)
            self.assertEqual(rows[0]['outstanding_requests'], 0)

    def test_file_service_pending_or_corrupt_states_refused(self):
        for address, value in ((0x844444, 1), (0x100f3c, 1), (0x100f3c, 0xffffffff),
                               (0x84444c, 1), (0x844450, 1), (0x844448, 2),
                               (0x844440, 17), (0x844400, 0), (0x844400, 0x100100),
                               (0x844400, 0x10fffc)):
            f = self.file_service(); f.put(address, value)
            with self.subTest(address=address, value=value), self.assertRaises(RuntimeError):
                b.files_idle(f, lambda *args, **kwargs: None)

    def test_heap_uses_whole_original_factory_and_exact_startup_args(self):
        f = HeapFixture()
        self.assertEqual(f.run(), f.heap)
        self.assertIn(('NamedHeapCreate', (0x6153ca47, 0x1000000, 0x6a22d8, 0), f.manager), f.calls)
        for corrupt in ('old_slot', 'context', 'name', 'capacity'):
            f = HeapFixture(); f.corrupt = corrupt
            with self.subTest(corrupt=corrupt), self.assertRaises(RuntimeError): f.run()

    def test_heap_duplicate_full_registry_or_foreign_name_refuses_before_creation(self):
        for bad in ('duplicate', 'full_registry', 'full_names', 'foreign_name'):
            f = HeapFixture()
            if bad == 'duplicate': f.existing = f.heap
            if bad == 'full_registry': f.put(f.manager + 0xc0, 48)
            if bad == 'full_names':
                for i in range(32): f.write(f.manager + 0x18c + i * 0x50 + 0x4c, b'\1')
            if bad == 'foreign_name': f.write(0x6a22d8, b'wrong\0')
            with self.subTest(bad=bad), self.assertRaises(RuntimeError): f.run()
            self.assertNotIn('NamedHeapCreate', [name for name, _, _ in f.calls])

    def test_authored_mono_pcm_and_two_unnamed_flags(self):
        source, bank, _ = fixture()
        with patch.object(b, 'COUNT', 2): samples = b.parse_samples(source, bank)
        self.assertEqual([s['offset'] for s in samples], [0, 256])
        self.assertEqual([s['flags'] for s in samples], [0, 10])
        self.assertEqual(samples[1]['volume'], 120.)

    def test_cyclic_duplicate_unknown_or_escaped_authored_spans_rejected(self):
        original, bank, fields = fixture()
        edits = [(fields[0][-1] + 12, fields[0][0]), (60 + 4, struct.unpack_from('<I', original, 60)[0]),
                 (fields[1][0] + 8, 0), (fields[1][2] + 8, 1), (fields[1][-1] + 8, 0x123),
                 (fields[0][1] + 8, 0x100000), (fields[0][0] + 12, len(original) - 4)]
        for offset, value in edits:
            with self.subTest(offset=offset, value=value), patch.object(b, 'COUNT', 2):
                changed = bytearray(original); struct.pack_into('<I', changed, offset, value)
                with self.assertRaises(RuntimeError): b.parse_samples(changed, bank)
        changed = bytearray(bank); changed[0] = 3
        with patch.object(b, 'COUNT', 2), self.assertRaises(RuntimeError): b.parse_samples(original, changed)

    def test_stream_uses_original_config_counts_and_genuine_thread(self):
        f = StreamFixture(); f.run()
        self.assertEqual(f.calls, [('Configuration', ()), ('StreamInit', (3, 3))])
        self.assertEqual(f.read(0x8459c8, 8), bytes(8))
        for location in (0x6c05a0, 0x6fe858, 0x6fa5f0, 0x764de4, 0x700d50, 0x764dd8, 0x8459c8):
            f = StreamFixture(); f.put(location, 1)
            with self.subTest(location=location), self.assertRaises(RuntimeError): f.run()
            self.assertNotIn('StreamInit', [name for name, _ in f.calls])
        f = StreamFixture(); f.status = 0
        with self.assertRaisesRegex(RuntimeError, 'remain alive'): f.run()
        f = StreamFixture(); f.foreign_config = True
        with self.assertRaisesRegex(RuntimeError, 'configuration'): f.run()

    def table(self):
        source, bank, _ = fixture()
        samples = b.parse_samples(source, bank)
        f = Memory(); f.put(b.probe_audio.SAMPLES, 2); f.write(0x100000, bank)
        for index, sample in enumerate(samples):
            descriptor, header = 0x102000 + index * 0x80, 0x100000 + sample['offset']
            floats = struct.unpack('<3I', struct.pack('<3f', sample['pitch'], sample['volume'], sample['dropoff']))
            f.put(0x6e0f90 + index * 32, sample['checksum'], sample['flags'] | 3 << 16, *floats, descriptor, 0, header)
            f.put(descriptor + 0x1c, sample['pcm_bytes']); f.put(descriptor + 0x24, header)
            f.put(descriptor + 0x38, sample['pcm_bytes'], 0, header + 128)
        return f, samples, bank

    def test_all_native_bindings_and_whole_bank_bytes_required(self):
        with patch.object(b, 'COUNT', 2):
            f, samples, bank = self.table()
            self.assertEqual(b.validate_table(f, samples, bank, 0x100000, 0x110000), 0x100000)
            for address, value in ((b.probe_audio.SAMPLES, 1), (0x6e0f90, 7), (0x6e0f90 + 4, 0),
                                   (0x6e0f90 + 32 + 28, 0x100200), (0x6e0f90 + 20, 0x200000),
                                   (0x102000 + 0x24, 0), (0x100080, 0x123)):
                f, samples, bank = self.table(); f.put(address, value)
                with self.subTest(address=address), self.assertRaises(RuntimeError):
                    b.validate_table(f, samples, bank, 0x100000, 0x110000)


if __name__ == '__main__': unittest.main()
