import struct
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from probe_scene_stream import SceneStream, cache_entries, in_range
from probe_scene_references import live_cache_bytes, reference_words
from native_worker import STATE, TID, COUNT, IDLE


class CacheTests(unittest.TestCase):
    def crossing_sector_fixture(self):
        stream=SceneStream.__new__(SceneStream)
        base=0x100000;arena=bytearray(0x8000);header=bytearray(0xa0)
        table,buckets,objects=base+0x100,base+0x200,base+0x3000
        link,first,second,supersector=base+0x1ffc,base+0x4ffe,base+0x6000,base+0x7000
        struct.pack_into('<I',header,0x5c,table)
        struct.pack_into('<I',header,0x60,supersector)
        struct.pack_into('<2I',header,0x68,2,objects)
        struct.pack_into('<2I',arena,table-base,8,buckets)
        struct.pack_into('<I',arena,objects-base+4,0)
        struct.pack_into('<I',arena,objects-base+28,256)
        struct.pack_into('<3I',arena,buckets-base,0,first,link)
        struct.pack_into('<3I',arena,link-base,256,second,0)
        for address in (first,second):struct.pack_into('<I',arena,address-base,0x694c1c)
        reads=[]
        def read(address,size):
            reads.append((address,size))
            if 0x50000<=address<=0x500a0-size:
                return bytes(header[address-0x50000:address-0x50000+size])
            self.assertTrue(base<=address<=base+len(arena)-size)
            return bytes(arena[address-base:address-base+size])
        stream.arena=(base,base+len(arena));stream.read=read
        stream.word=lambda address:struct.unpack('<I',read(address,4))[0]
        stream.log=Mock()
        item=dict(scene=0x50000,col=0x70000,col_bytes=100)
        return stream,item,arena,reads,dict(base=base,table=table,buckets=buckets,
            objects=objects,link=link,first=first,second=second,supersector=supersector)

    def test_sector_census_decodes_cross_page_records_and_keeps_exact_extent_order(self):
        stream,item,arena,reads,a=self.crossing_sector_fixture()
        expected=[(0x70000,0x70064),(0x50000,0x500a0),(a['objects'],a['objects']+48),
                  (a['table'],a['table']+24),(a['buckets'],a['buckets']+256*12),
                  (a['first'],a['first']+0x30),(a['link'],a['link']+12),
                  (a['second'],a['second']+0x30),(a['supersector'],a['supersector']+0x34)]
        self.assertEqual(stream.scene_allocations(item),expected)
        pages=[address for address,size in reads if size==4096]
        self.assertEqual(pages,[a['base']+offset for offset in (0x4000,0x5000,0x1000,0x2000,0x6000)])
        self.assertEqual(len(pages),len(set(pages)))

    def test_sector_census_cross_page_and_hash_guards_still_reject(self):
        cases=(('bad_crossing_vtable','first',0,0xdeadbeef,'PCSector'),
               ('foreign_hash_link','buckets',8,0x107ff8,'hash link'),
               ('duplicate_sector','link',4,0x104ffe,'PCSector'),
               ('duplicate_object_key','objects',28,0,'PCSector'),
               ('missing_key','link',0,512,'missing'))
        for name,key,offset,value,reason in cases:
            with self.subTest(name=name):
                stream,item,arena,reads,a=self.crossing_sector_fixture()
                struct.pack_into('<I',arena,a[key]-a['base']+offset,value)
                with self.assertRaisesRegex(RuntimeError,reason):stream.scene_allocations(item)
                self.assertNotIn('reference_extents',item)
        stream,item,arena,reads,a=self.crossing_sector_fixture()
        struct.pack_into('<3I',arena,a['link']-a['base'],512,a['second'],a['buckets'])
        with self.assertRaisesRegex(RuntimeError,'chain cycles'):stream.scene_allocations(item)
        self.assertNotIn('reference_extents',item)

    def test_sector_census_reads_shared_pages_and_preserves_every_extent(self):
        stream = SceneStream.__new__(SceneStream)
        base = 0x100000
        arena = bytearray(0x20000)
        header = bytearray(0xa0)
        table, buckets, objects = base+0x100, base+0x200, base+0x1000
        sectors, links, supersector = base+0x5000, base+0xd000, base+0xf000
        struct.pack_into('<I', header, 0x5c, table)
        struct.pack_into('<I', header, 0x60, supersector)
        struct.pack_into('<2I', header, 0x68, 512, objects)
        struct.pack_into('<2I', arena, table-base, 8, buckets)
        for index in range(512):
            struct.pack_into('<I', arena, objects-base+index*24+4, index)
            struct.pack_into('<I', arena, sectors-base+index*0x30, 0x694c1c)
            address = buckets+index*12 if index < 256 else links+(index-256)*12
            following = links+index*12 if index < 256 else 0
            struct.pack_into('<3I', arena, address-base, index, sectors+index*0x30, following)
        reads = []
        def read(address, size):
            reads.append((address, size))
            if 0x50000 <= address <= 0x500a0-size:
                return bytes(header[address-0x50000:address-0x50000+size])
            self.assertTrue(base <= address <= base+len(arena)-size)
            return bytes(arena[address-base:address-base+size])
        stream.arena = (base, base+len(arena))
        stream.read, stream.word = read, lambda address: struct.unpack('<I', read(address, 4))[0]
        stream.log = lambda *args, **kwargs: None
        item = dict(scene=0x50000, col=0x70000, col_bytes=100)
        found = stream.scene_allocations(item)
        expected = {(0x70000, 0x70064), (0x50000, 0x500a0), (objects, objects+512*24),
                    (table, table+24), (buckets, buckets+256*12), (supersector, supersector+0x34)}
        expected.update((sectors+i*0x30, sectors+(i+1)*0x30) for i in range(512))
        expected.update((links+i*12, links+(i+1)*12) for i in range(256))
        self.assertEqual(set(found), expected)
        self.assertLess(len(reads), 30)  # Previously 512 sector + 256 link RPM calls.
        struct.pack_into('<I', arena, sectors-base+300*0x30, 0xdeadbeef)
        with self.assertRaisesRegex(RuntimeError, 'PCSector'):
            stream.scene_allocations(dict(scene=0x50000, col=0x70000, col_bytes=100))

    def test_retirement_classifies_all_scenes_before_destructors_with_one_census(self):
        stream = SceneStream.__new__(SceneStream)
        first = dict(scene=100, col=1000, node=2000, col_bytes=20)
        second = dict(scene=200, col=1100, node=2100, col_bytes=20)
        stream.retained, stream.retired = [first], second
        stream.context = {'actor': 300}
        stream.references = SimpleNamespace(blocks=Mock(return_value=[('actor', struct.pack('<I', 204))]))
        inspected = []
        stream.scene_allocations = lambda item: inspected.append(item['scene']) or [(item['scene'], item['scene']+20)]
        stream.read = lambda address, size: struct.pack('<2I', 2000, 2000)
        stream.word = lambda address: 0
        def destroy(name, *, this):
            self.assertEqual(inspected, [100, 200])
            self.assertEqual((name, this), ('destroy', 100))
        stream.call = destroy
        stream.process, stream.free = 1, Mock(return_value=1)
        stream.memory_budget, stream.pins, stream.log = lambda: 40, lambda: None, lambda *a, **k: None
        stream.current, stream.swaps = {'col_sha256': 'unchanged'}, 0
        stream._retire()
        stream.references.blocks.assert_called_once_with()
        self.assertEqual(stream.retained, [second])
        self.assertEqual(stream.free.call_count, 3)
        self.assertIsNone(stream.stage)

    def test_idle_preserves_registered_default_cache_and_rejects_unknown_pointer(self):
        stream = SceneStream.__new__(SceneStream)
        state = [0]*16
        state[STATE//4], state[TID//4], state[COUNT//4] = IDLE, 17, 31
        stream.worker = SimpleNamespace(snapshot=lambda: state, tid=17, count=31, thread=23)
        stream.api = SimpleNamespace(WaitForSingleObject=lambda thread, delay: 258)
        stream.stage = 'query'
        events = []
        stream.log = lambda phase, **values: events.append((phase, values))
        stream.caches = lambda: (1, {0x20000: bytes(0xfcc)})
        for selector in (0, 0x20000):
            stream.word = lambda address: selector
            self.assertEqual(stream.idle(), selector)
        self.assertEqual(events, [])
        stream.word = lambda address: 0x30000
        with self.assertRaises(RuntimeError):
            stream.idle()
        self.assertEqual(events[-1][1]['collision_cache'], 0x30000)
        self.assertFalse(events[-1][1]['registered_cache'])

    def test_populated_original_cache(self):
        raw = bytearray(0xfcc)
        struct.pack_into('<8f', raw, 0, -1, -2, -3, 1, 4, 5, 6, 1)
        struct.pack_into('<4I', raw, 0x20, 0x10000, 0x20000, 0x30000, 0x40000)
        struct.pack_into('<3I', raw, 0xfc0, 2, 1, 1)
        self.assertEqual(cache_entries(raw), (2, 1, 1, ((0x10000, 0x20000), (0x30000, 0x40000))))
        for counts in ((501, 501, 0), (2, 2, 1), (1, 0, 0)):
            struct.pack_into('<3I', raw, 0xfc0, *counts)
            with self.assertRaises(RuntimeError):
                cache_entries(raw)

    def test_empty_unqueried_bounds_and_bad_populated_bounds(self):
        raw = bytearray(0xfcc)
        struct.pack_into('<8f', raw, 0, 1e30, 1e30, 1e30, 1, -1e30, -1e30, -1e30, 1)
        self.assertEqual(cache_entries(raw)[:3], (0, 0, 0))
        struct.pack_into('<3I', raw, 0xfc0, 1, 1, 0)
        with self.assertRaises(RuntimeError):
            cache_entries(raw)
        struct.pack_into('<3I', raw, 0xfc0, 0, 0, 0)
        struct.pack_into('<f', raw, 0, float('nan'))
        with self.assertRaises(RuntimeError):
            cache_entries(raw)

    def test_retired_range_is_half_open(self):
        self.assertTrue(in_range(100, (100, 124)))
        self.assertTrue(in_range(123, (100, 124)))
        self.assertFalse(in_range(99, (100, 124)))
        self.assertFalse(in_range(124, (100, 124)))

    def test_retired_ranges_include_nested_and_disjoint_allocations(self):
        raw = struct.pack('<6I', 99, 100, 104, 127, 128, 300)
        found = reference_words([('component', raw)], [(104, 112), (100, 108), (112, 128), (256, 300)])
        self.assertEqual([(row['offset'], row['pointer']) for row in found], [(4, 100), (8, 104), (12, 127)])

    def test_dead_cache_slots_are_not_persistent_native_references(self):
        native = struct.pack('<6I', 100, 101, 102, 103, 104, 105)
        view = live_cache_bytes(0x1000, native, [(0x1008, 0x1010)])
        self.assertEqual(struct.unpack('<6I', view), (100, 101, 0, 0, 104, 105))
        self.assertEqual(struct.unpack('<6I', native), (100, 101, 102, 103, 104, 105))
        self.assertEqual(live_cache_bytes(0x1000, native, [(0x2000, 0x2010)]), native)

    def test_memory_budget_includes_current_staged_and_retained(self):
        value = SceneStream.__new__(SceneStream)
        value.current, value.retained = {'col_bytes': 100}, [{'col_bytes': 200}]
        value.pending, value.stage = {'inputs': {'col': bytes(300)}}, 'load'
        self.assertEqual(value.memory_budget(), 600)
        value.stage = 'retire'  # Pending is now the current scene, not another copy.
        self.assertEqual(value.memory_budget(), 300)
        with self.assertRaises(RuntimeError):
            value.memory_budget(incoming_bytes=128*1024*1024)


if __name__ == '__main__':
    unittest.main()
