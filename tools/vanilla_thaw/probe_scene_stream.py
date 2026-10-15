"""Idle collision-region replacement through original stock scene/cache routines.

Preparation may advance one stage between gameplay frames. Commit changes the
world list and rebuilds caches. Retirement preserves referenced old scenes.
Rail groups have a separate lifetime and are deliberately not touched here.
"""
import ctypes
import hashlib
import math
import struct
import time

import probe_loader as host
import probe_gta_scene as scene
from native_worker import retained_worker, STATE, TID, COUNT, IDLE

PINS = {
    'destroy': ('PCSceneNondeletingDestructor', 0x51aaf0, 81, 'a187247271329947ffa0ff76e06fde324c76e55359c96a22f0570d837a086188'),
    'release': ('PCSceneReleaseEntry', 0x51aa00, 6, 'be3309cc68000265d8adc2814ef1ac5724f1d3ca92f2227017543a8f35e7c678'),
    'base_destroy': ('SceneDestructor', 0x4e42f0, 230, 'ebbe76aee3b492f6d35936df637fac800170af26762ba5c30fbb533a17841272'),
    'release_col': ('SceneReleaseCOL', 0x4e3060, 113, '2159013c1a7f014c77bb6e9629d4fca65dec00e8e0296ac166f3d2ef237a7a6d'),
    'cache_rebuild': ('CollisionCacheRebuild', 0x41cca0, 792, 'c2f04943871f971faa7f2da970a76a9c89d622b535162b38c1fa60e622fa5991'),
    'cache_ctor': ('CollisionCacheConstructor', 0x41cc10, 138, 'c05c9c7b323c58681e9f584d0515c66b5235fa15e6adccdb84e5d09a2ffadb91'),
    'cache_register': ('CollisionCacheRegister', 0x41cb00, 56, '7fc8fda78872ed871c59cde891d9f1a8f5eb8011d323d720d56e92b4bb1fcec8'),
    'array_destroy': ('CollisionArrayDestructor', 0x4e2a70, 88, 'ba9c502e32196f97ae4fef2079eade58d69dea1aab698a83bab817af754bebe5'),
    'object_destroy': ('CollisionObjectDestructorEntry', 0x425060, 6, '8d4fbd41e493c7c853b9a1a7eb37c4731d0939d7818e6682dfda39980d7a735c'),
    'sector_factory': ('PCSectorFactory', 0x51a970, 107, '96bb663ca05126136542aca76fec664b0c3585b79df3d42b5b6d7f9a43583292'),
    'sector_ctor': ('PCSectorConstructor', 0x51b550, 49, 'f2084d9be7e95c0fa64b3806947bc8babb42678e0cd829f8ffef81c989e885c0'),
    'world_query': ('OriginalUncachedWorldQuery', 0x423280, 1075, '82f3f1752fefc5107a9faac62cde4243cdcabd3d23b33f10abc45f0a27d9863e'),
    **scene.PINS,
}
CACHE_SLOTS, CACHE_MASK = 0x6dd400, 0x6dd424
DEFAULT_CACHE = 0x8405c0


def cache_entries(raw):
    """Validate the complete original 500-entry cache before reading references."""
    host.require(len(raw) == 0xfcc, 'Collision cache extent differs')
    count, static, moving = struct.unpack_from('<3I', raw, 0xfc0)
    host.require(count <= 500 and static + moving == count,
                 'Collision cache counts exceed the original layout')
    bounds = struct.unpack_from('<8f', raw)
    host.require(all(math.isfinite(v) for v in bounds), 'Collision cache bounds are nonfinite')
    if count:
        host.require(all(bounds[i] <= bounds[i+4] for i in range(3)), 'Populated collision cache has inverted bounds')
    return count, static, moving, tuple(struct.unpack_from('<2I', raw, 0x20+i*8) for i in range(count))


def in_range(pointer, extent):
    return extent[0] <= pointer < extent[1]


class SceneStream:
    """One published scene plus at most one staged successor; failures poison it."""
    def __init__(self, api, process, record, deadline, log, current, initial_inputs, *, context=None, global_data=None):
        self.api, self.process, self.record, self.deadline, self.log = api, process, record, deadline, log
        self.worker = retained_worker(api, process)
        self.current, self.origin = current, tuple(initial_inputs['coordinates']['origin_gta'])
        self.current['col_bytes'] = len(initial_inputs['col'])
        self.context = context
        self.pending, self.stage, self.poisoned, self.swaps = None, None, False, 0
        self.retained = []
        self.arena = (self.word(0x84376c), self.word(0x843770))
        host.require(self.arena[1]-self.arena[0] == 256*1024*1024, 'Scene streaming native arena differs')
        self.free = api.VirtualFreeEx
        self.free.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
        self.free.restype = ctypes.c_int
        self.idle()
        self.pins()
        self.scratch = self.allocate(bytes(4096))
        if context is not None:
            from probe_scene_references import SceneReferences
            self.references = SceneReferences(self.read, self.word, self.arena, context)
        if global_data is not None:
            self.detach_global(global_data)

    def detach_global(self, global_data):
        """Detach only the already proven original Global COL; retain its assets."""
        import probe_gta_rails as rails
        entries = rails.scene_chain(self.read, self.current, self.log)
        extras = [entry for entry in entries if entry['node'] != self.current['node']]
        if not extras:
            return
        host.require(len(extras) == 1 and self.context is not None, 'Unexpected additional native world scenes')
        extra, source = extras[0], global_data['col']
        header, address = extra['scene_words'], extra['words'][1]
        host.require(extra['words'][0] == 0x6936a4 and header[0x10//4] == 1 and
                     header[0x54//4:0x5c//4] == (1, 1) and header[0x68//4] == 1 and
                     header[0x60//4] and header[0x6c//4] and header[0x94//4], 'Extra scene is not original Global')
        host.require(hashlib.sha256(source).hexdigest() == rails.GLOBAL_COL_SHA, 'Prepared Global COL identity differs')
        base = header[0x70//4]-80
        contents = self.read(base, len(source))
        rails.verify_global_col(contents, source, base)
        host.require(self.read(address+0x34, 32) == source[48:80], 'Global scene bounds differ')
        before = self.read(address, 0xa0), self.read(self.context['actor'], 0x35c)
        host.native_call(self.api, self.process, rails.PINS['SceneNodeUnlink'], [], self.record,
                         self.deadline, self.log, extra['node'])
        host.require(before == (self.read(address, 0xa0), self.read(self.context['actor'], 0x35c)) and
                     self.read(base, len(source)) == contents and struct.unpack('<5I', self.read(extra['node'], 20)) ==
                     (0x6936a4, address, 0, extra['node'], extra['node']), 'Original Global detach changed its resources/actor')
        self.published(self.current)
        self.log('scene_stream_global_detached', scene=address, source_sha256=rails.GLOBAL_COL_SHA, resources_retained=True)

    def read(self, address, size):
        return host.remote_read(self.api, self.process, address, size)

    def word(self, address):
        return struct.unpack('<I', self.read(address, 4))[0]

    def write(self, address, data):
        host.remote_write(self.api, self.process, address, data)

    def allocate(self, data):
        address = host.success(self.api.VirtualAllocEx(self.process, None, max(4096, len(data)), 0x3000, 4), 'Allocate streamed scene input')
        host.require(address % 16 == 0 and 0x10000 <= address < 0x100000000-len(data), 'Streamed scene allocation exceeds x86 range')
        self.write(address, data)
        return address

    def call(self, name, args=(), this=None):
        return host.native_call(self.api, self.process, PINS[name], list(args), self.record, self.deadline, self.log, this)

    def pins(self):
        for name, address, size, digest in PINS.values():
            host.require(hashlib.sha256(self.read(address, size)).hexdigest() == digest, 'Stream scene original pin differs: '+name)

    def idle(self):
        state = self.worker.snapshot()
        thread_wait = self.api.WaitForSingleObject(self.worker.thread, 0)
        collision_cache = self.word(DEFAULT_CACHE)
        # This is the default cache selected by FeelerQuery, not a busy flag.
        # Original Walk paths can leave their registered cache selected after
        # returning. Keep it unchanged; commit rebuilds registered caches.
        registered = not collision_cache or collision_cache in self.caches()[1]
        valid = (state[STATE//4] == IDLE and state[TID//4] == self.worker.tid and
                 state[COUNT//4] == self.worker.count and thread_wait == 258 and registered)
        if not valid:
            self.log('scene_stream_idle_failed', stage=self.stage, worker_state=state[STATE//4],
                     expected_state=IDLE, worker_tid=state[TID//4], expected_tid=self.worker.tid,
                     worker_count=state[COUNT//4], expected_count=self.worker.count,
                     thread_wait=thread_wait, collision_cache=collision_cache, registered_cache=registered)
        host.require(valid, 'Scene replacement requires idle worker and a null or registered default cache')
        return collision_cache

    def published(self, item):
        node, address = item['node'], item['scene']
        host.require(struct.unpack('<5I', self.read(scene.WORLD, 20)) ==
                     (scene.LIST_VTABLE, 0xffffffff, 0, node, node) and
                     struct.unpack('<5I', self.read(node, 20)) ==
                     (scene.LIST_VTABLE, address, 0, scene.WORLD, scene.WORLD), 'Stream scene sole-world ownership changed')

    def publish(self, replacement, previous):
        self.published(previous)
        node = replacement['node']
        self.write(node+12, struct.pack('<2I', scene.WORLD, scene.WORLD))
        self.write(scene.WORLD+12, struct.pack('<2I', node, node))
        self.write(previous['node']+12, struct.pack('<2I', previous['node'], previous['node']))
        self.published(replacement)

    def begin(self, inputs):
        host.require(not self.poisoned and self.pending is None, 'Scene stream already staging or poisoned')
        host.require(inputs.get('format') == 'gta-thaw-scene-probe-v1' and inputs.get('seal') == scene._seal(inputs) and
                     hashlib.sha256(inputs['col']).hexdigest() == inputs['col_sha256'] and
                     tuple(inputs['coordinates']['origin_gta']) == self.origin,
                     'Streamed COL provenance changed or physical origin moved')
        self.idle()
        self.published(self.current)
        self.memory_budget(incoming_bytes=len(inputs['col']))
        self.pending = {'inputs': inputs, 'source': self.current['source'], 'col_sha256': inputs['col_sha256'], 'case': 0}
        self.stage = 'allocate'

    def memory_budget(self, incoming_bytes=0):
        staged = len(self.pending['inputs']['col']) if self.pending is not None and self.stage != 'retire' else 0
        total = self.current['col_bytes'] + staged + incoming_bytes + sum(item.get('col_bytes', 236) for item in self.retained)
        host.require(len(self.retained) <= 32 and total <= 128*1024*1024,
                     'Persistent native collision references exceed the bounded scene retention budget')
        return total

    def caches(self):
        mask = self.word(CACHE_MASK)
        host.require(mask & ~255 == 0, 'Original collision cache registration mask differs')
        result = {}
        for slot in range(8):
            if mask & (1 << slot):
                pointer = self.word(CACHE_SLOTS+4*slot)
                host.require(pointer % 4 == 0 and self.arena[0] <= pointer <= self.arena[1]-0xfcc and
                             pointer not in result, 'Collision cache registration leaves original arena or repeats')
                raw = self.read(pointer, 0xfcc)
                cache_entries(raw)
                result[pointer] = raw
        return mask, result

    def advance(self):
        """Run one stage; True means a successor was fully published and retired."""
        host.require(self.pending is not None and not self.poisoned, 'No available streamed scene stage')
        started, stage = time.monotonic(), self.stage
        try:
            default_cache = self.idle()
            self.published(self.current)
            value, inputs = self.pending, self.pending['inputs']
            if stage == 'allocate':
                value.update(scene=self.allocate(bytes(0xa0)), col=self.allocate(inputs['col']), node=self.allocate(bytes(20)))
                host.require(self.call('scene', (8,), value['scene']) == value['scene'], 'Streamed PCScene constructor failed')
                host.require(self.word(value['scene']) == 0x69520c and self.word(value['scene']+0x94) == 0,
                             'Streamed collision-only scene layout differs')
                self.write(value['scene']+0x10, struct.pack('<I', 1))
                self.stage = 'load'
            elif stage == 'load':
                host.require(self.call('load_col', (value['col'], 0), value['scene']) & 255, 'Streamed original COL load failed')
                count, objects, sectors = self.word(value['scene']+0x68), self.word(value['scene']+0x6c), self.word(value['scene']+0x60)
                host.require(count == inputs['stats']['objects'] and objects % 4 == sectors % 4 == 0 and
                             self.arena[0] <= objects <= self.arena[1]-count*24 and in_range(sectors, self.arena),
                             'Streamed COL object allocation or count differs')
                words = struct.unpack('<'+str(count*6)+'I', self.read(objects, count*24))
                host.require(all(words[i+2] & 0xc000 == 0 for i in range(0, len(words), 6)), 'Streamed COL buffer unexpectedly owned by native objects')
                value['objects_extent'] = (objects, objects+count*24)
                host.require(self.call('node', this=value['node']) == value['node'], 'Stream scene node construction failed')
                self.write(value['node']+4, struct.pack('<I', value['scene']))
                self.stage = 'query'
            elif stage == 'query':
                case = inputs['cases'][value['case']]
                self.write(self.scratch, bytes(0xc0))
                host.require(self.call('feeler', this=self.scratch) == self.scratch, 'Stream scene feeler construction failed')
                self.write(self.scratch, struct.pack('<8f', *case['start'], 1., *case['end'], 1.))
                # Pinned FeelerQuery uses this+24 as CollData, writes its owner
                # at +64, and calls WorldQuery(ray, CollData, callback, cache)
                # at 5AE067. Its zero callback needs no contact script state.
                # Explicit null cache tests the staged world without changing
                # the source's global default selector or any gameplay cache.
                self.write(self.scratch+0x64, struct.pack('<I', self.scratch))
                self.publish(value, self.current)
                try:
                    hit = bool(self.call('world_query', (self.scratch, self.scratch+0x24, 0, 0)) & 255)
                    scene.query_result(self.read(self.scratch, 0xc0), hit, case)
                finally:
                    self.publish(self.current, value)
                value['case'] += 1
                if value['case'] == len(inputs['cases']):
                    self.stage = 'commit'
            elif stage == 'commit':
                self._commit()
            elif stage == 'retire':
                self._retire()
            else:
                raise RuntimeError('Unknown scene streaming stage')
            host.require(self.idle() == default_cache, 'Scene operation changed the original default collision cache')
            self.published(self.current)
            self.log('scene_stream_stage', stage=stage, seconds=time.monotonic()-started, swaps=self.swaps)
            return stage == 'retire'
        except BaseException:
            self.poisoned = True
            raise

    def _commit(self):
        old, value = self.current, self.pending
        mask, before = self.caches()
        old_count, old_objects = self.word(old['scene']+0x68), self.word(old['scene']+0x6c)
        old_extents = ((old_objects, old_objects+old_count*24),
                       (old['col'], old['col']+old.get('col_bytes', 236)))
        self.publish(value, old)
        # Original cache fill clears all counts first, then re-queries the newly
        # published world. Destruction itself does not invalidate static caches.
        for pointer, raw in before.items():
            bounds = struct.unpack_from('<8f', raw)
            if all(bounds[i] <= bounds[i+4] for i in range(3)):
                self.write(self.scratch+0x100, raw[:32])
                self.call('cache_rebuild', (self.scratch+0x100,), pointer)
        after_mask, after = self.caches()
        host.require(after_mask == mask and before.keys() == after.keys(), 'Streaming changed cache registration')
        for raw in after.values():
            host.require(not any(in_range(pointer, extent) for pair in cache_entries(raw)[3]
                                 for pointer in pair for extent in old_extents),
                         'Rebuilt original collision cache still references retired COL objects')
        self.retired = old
        value['col_bytes'] = len(value['inputs']['col'])
        self.current = {key: value[key] for key in ('scene', 'col', 'node', 'source', 'col_sha256', 'objects_extent', 'col_bytes')}
        self.stage = 'retire'
        self.log('scene_stream_published', next_swap=self.swaps+1, cache_count=len(after), col_sha256=value['col_sha256'])

    def actor_references(self, retired, blocks=None):
        """Preserve scenes referenced by registered original owners/components."""
        if self.context is None:
            return []
        from probe_scene_references import reference_words
        return reference_words(self.references.blocks() if blocks is None else blocks,
                               self.scene_allocations(retired))

    def scene_allocations(self, item):
        """Owned PCSector/hash ranges, from complete original COL loader layout."""
        if 'reference_extents' in item:
            return item['reference_extents']
        # The native worker stays idle throughout this read-only census. Share
        # nearby sector/hash reads only here, never across a native call.
        pages = {}
        def arena_unpack(address, size, fmt):
            host.require(self.arena[0] <= address <= self.arena[1]-size,
                         'Scene allocation snapshot leaves native arena')
            page = max(self.arena[0], address & ~4095)
            if page not in pages:
                pages[page] = self.read(page, min(4096, self.arena[1]-page))
            offset = address-page
            # Most sector/hash fields fit in one already captured page. Decode
            # there directly; retain the bounded copy for cross-page records.
            if offset+size <= len(pages[page]):
                return struct.unpack_from(fmt, pages[page], offset)
            result = bytearray()
            while size:
                page = max(self.arena[0], address & ~4095)
                if page not in pages:
                    pages[page] = self.read(page, min(4096, self.arena[1]-page))
                offset = address-page
                count = min(size, len(pages[page])-offset)
                host.require(count > 0, 'Incomplete scene allocation snapshot')
                result.extend(pages[page][offset:offset+count])
                address, size = address+count, size-count
            return struct.unpack(fmt, result)
        regions = [(item['col'], item['col']+item.get('col_bytes', 236)), (item['scene'], item['scene']+0xa0)]
        objects, count = self.word(item['scene']+0x6c), self.word(item['scene']+0x68)
        regions.append((objects, objects+count*24))
        table = self.word(item['scene']+0x5c)
        host.require(self.arena[0] <= table <= self.arena[1]-24, 'Streamed scene sector hash leaves native arena')
        exponent, buckets = struct.unpack('<2I', self.read(table, 8))
        host.require(exponent == 8 and self.arena[0] <= buckets <= self.arena[1]-256*12,
                     'Streamed scene sector hash differs from PCScene(8)')
        regions += [(table, table+24), (buckets, buckets+256*12)]
        raw = self.read(buckets, 256*12)
        records = {buckets+12*i: struct.unpack_from('<3I', raw, 12*i) for i in range(256)}
        objects_raw = self.read(objects, count*24)
        sectors = set()
        for index in range(count):
            key = struct.unpack_from('<I', objects_raw, index*24+4)[0]
            pointer, seen = buckets+(key & 255)*12, set()
            while pointer:
                host.require(pointer not in seen and len(seen) <= count, 'Streamed scene sector hash chain cycles')
                seen.add(pointer)
                if pointer not in records:
                    host.require(self.arena[0] <= pointer <= self.arena[1]-12, 'Scene sector hash link leaves arena')
                    records[pointer] = arena_unpack(pointer, 12, '<3I')
                    regions.append((pointer, pointer+12))
                name, sector, next_pointer = records[pointer]
                if name == key:
                    host.require(sector not in sectors and self.arena[0] <= sector <= self.arena[1]-0x30 and
                                 arena_unpack(sector, 4, '<I')[0] == 0x694c1c,
                                 'Original PCSector allocation/identity differs')
                    sectors.add(sector)
                    regions.append((sector, sector+0x30))
                    break
                pointer = next_pointer
            else:
                raise RuntimeError('Original COL object is missing its PCSector')
        supersector = self.word(item['scene']+0x60)
        host.require(self.arena[0] <= supersector <= self.arena[1]-0x34, 'Scene supersector leaves arena')
        regions.append((supersector, supersector+0x34))
        item['reference_extents'] = regions
        self.log('scene_stream_reference_extents', scene=item['scene'], sectors=len(sectors), ranges=len(regions))
        return regions

    def _retire(self):
        retired, released = self.retired, 0
        while retired is not None:
            self.retained.append(retired)
            retired = retired.get('retired')
        keep = []
        # Classify every predecessor before any destructor runs, using one
        # coherent idle-owner census rather than rereading it for each scene.
        blocks = self.references.blocks() if self.context is not None else None
        classified = [(item, self.actor_references(item, blocks)) for item in self.retained]
        for retired, references in classified:
            self.log('scene_stream_retire_references', references=references, scene=retired['scene'])
            if references:
                keep.append(retired)
                continue
            host.require(struct.unpack('<2I', self.read(retired['node']+12, 8)) == (retired['node'], retired['node']) and
                         self.word(retired['scene']+0x94) == 0, 'Retired scene is still linked or renderer-owned')
            self.call('destroy', this=retired['scene'])
            host.require(self.word(retired['scene']+0x60) == self.word(retired['scene']+0x68) ==
                         self.word(retired['scene']+0x6c) == 0, 'Original scene destruction retained COL/supersector ownership')
            for name in ('scene', 'col', 'node'):
                host.success(self.free(self.process, retired[name], 0, 0x8000), 'Release detached streamed '+name)
            released += 1
        self.retained = keep
        # Core has four persistent Feelers and Walk has nine. Scene pointers and
        # geometry pointers can refer to different old generations. Never clear
        # their state merely to retire geometry; keep those generations alive.
        resident_bytes = self.memory_budget()
        self.pending, self.stage = None, None
        self.swaps += 1
        self.pins()
        self.log('scene_stream_commit', swaps=self.swaps, scenes_destroyed=released,
                 referenced_scenes_retained=len(keep), retained_col_bytes=sum(item.get('col_bytes', 236) for item in keep),
                 resident_col_bytes=resident_bytes,
                 col_sha256=self.current['col_sha256'], original_destructor=True,
                 actor_or_physics_writes=False)

    def discard_pending(self):
        """Cancel an unpublished successor after the GTA selection becomes stale."""
        host.require(not self.poisoned and self.stage != 'retire', 'Cannot discard a published or failed successor')
        if self.pending is None:
            return
        default_cache = self.idle()
        self.published(self.current)
        try:
            if 'scene' in self.pending:
                self.call('destroy', this=self.pending['scene'])
                for name in ('scene', 'col', 'node'):
                    host.success(self.free(self.process, self.pending[name], 0, 0x8000), 'Release discarded streamed '+name)
            host.require(self.idle() == default_cache, 'Discard changed the original default collision cache')
            self.log('scene_stream_discard', stage=self.stage)
            self.pending, self.stage = None, None
        except BaseException:
            self.poisoned = True
            raise
