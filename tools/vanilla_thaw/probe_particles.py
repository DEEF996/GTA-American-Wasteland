"""Original stock flexible-particle startup on the retained gameplay graph.

The complete constructors create the manager and factory's real heap/list state.
Host startup publishes their original aliases and the factory's original derived
vtable, matching the stock inline initializer. Effect scripts remain original;
this module neither draws nor substitutes effects.
"""
import hashlib
import math
import struct

import probe_loader as host
import probe_profile
import probe_screen_effects
import probe_vm_idle
from import_qb import qbkey

GLOBAL, OBJECT_BYTES = 0x7689d8, 0x30
FACTORY_GLOBAL, FACTORY_BYTES = 0x7689dc, 0x24
FACTORY_BASE_VTABLE, FACTORY_VTABLE = 0x69399c, 0x694d3c
PINS = {
    'allocate': probe_screen_effects.PINS['allocate'],
    'construct': ('OriginalParticleManagerCtor', 0x4e07c0, 312,
                  '9c9f004ea34c02636cad4f3646d6a5ad9e99830b737f13122d389b77483290e5'),
    'factory_construct': ('OriginalParticleFactoryBaseCtor', 0x4dae40, 171,
                          '4ec1dc5655cd7bd65164b185f70ecb981c11661150a3a1152b39886a7d788193'),
    'lookup': host.QB_PINS['lookup'],
    'free_bytes': probe_profile.PINS['HeapFreeBytes'],
}
CORROBORATION = (
    (0x4cf109, 56, 'b7c0be6bbc2ccad79e6660551232a0d7c974e4db2a2c64ac64a0f46ac09599f5'),
    (0x693ac0, 21, '88c06f081801296e3af4e668bd1fc38fc63f039644d5504b377000aed28e3429'),
    (0x626f35, 299, '37c1d3eafc6cb1f7045aaa648bf3f5a9d7b004bf45d515e475cd21ad91022958'),
    (0x473b56, 115, '3759f183e056f8656e9d278928c8a24f0423fb5fc0dcabc5a49dcf010a937b35'),
    (FACTORY_VTABLE, 8, '89e2dc8bbe854d3c5d75beabfa0773bb0e549d154fc93c8733a8be5c6dbc8e24'),
    (0x13cbef2, 28, '62ed4ce2362c514d37ba09920bbe9d5cfaa99e6cbe4d53eb46e4329766aecadc'),
    (0x19bd6ca, 96, 'b35a42dd863c48adf55c09013491d323cfed54c073fbafb26b39888bb807ea95'),
    (0x510550, 83, 'bf9278f6179841ec5ad6147da0c0fc181350abbbfc336e799a257fe2274b807f'),
)
SOURCE_KEYS = (0x8ee51d7c, 0x3214c818, 0x54080ff1, 0xb3b8e445)


def parent_extent(address, start, end):
    # Original MemMan constructs the two initial heap headers in image BSS;
    # their managed payload, and later heap headers, use the primary arena.
    return address in (0x8429a8, 0x8436b0) or address % 4 == 0 and start <= address <= end-0x70


def validate_state(state, arena, heap, sentinel, capacity, lod):
    host.require(len(state) == 12 and len(arena) == 8 and len(heap) == 28 and len(sentinel) == 5,
                 'Original particle object extents differ')
    host.require(state[0] == 0x693ac0 and state[3:5] == (0, 0) and
                 state[6:10] == (0x3f800000,)*4 and state[10:12] == lod and
                 sentinel == (0x693ac4, 0xffffffff, 0, state[5], state[5]) and
                 arena[0] == 0x6a2354 and arena[5]-arena[1] == capacity and
                 arena[6] == state[2] and arena[7] == 0 and heap[0] == 0x6a2200 and
                 heap[0x24//4] == state[1] and heap[0x28//4] == 1 and
                 heap[0x2c//4] == arena[1] and heap[0x34//4] == 0x693ac8,
                 'Original particle constructor arena/heap/list/source defaults differ')


def validate_factory(state, sentinel, lod):
    host.require(len(state) == 9 and len(sentinel) == 3 and state[0] == FACTORY_BASE_VTABLE and
                 state[2] == 0 and state[3:7] == (0x3f800000,)*4 and state[7:9] == lod and
                 sentinel[1:] == (state[1], state[1]),
                 'Original particle factory constructor/list/source defaults differ')


def publish_factory(api, process, system, pointer, state):
    """Replay only the pinned original inline derived-vtable and alias publication."""
    host.require(len(state) == 9 and state[0] == FACTORY_BASE_VTABLE,
                 'Particle factory has no verified original constructor state')
    original = struct.pack('<9I', *state)
    host.require(system.word(FACTORY_GLOBAL) == 0 and system.read(pointer, FACTORY_BYTES) == original,
                 'Particle factory changed before original startup publication')
    host.remote_write(api, process, pointer, struct.pack('<I', FACTORY_VTABLE))
    host.require(system.read(pointer, FACTORY_BYTES) == struct.pack('<I', FACTORY_VTABLE)+original[4:] and
                 system.word(FACTORY_GLOBAL) == 0,
                 'Particle factory derived publication changed original constructor fields')
    host.remote_write(api, process, FACTORY_GLOBAL, struct.pack('<I', pointer))
    host.require(system.word(FACTORY_GLOBAL) == pointer, 'Original particle factory alias was not published')


def run(api, process, record, deadline, log, context, system, *, preloaded):
    read, word = system.read, system.word
    start, end = word(0x84376c), word(0x843770)
    allocator = 0x842b18

    def words(address, count):
        host.require(address % 4 == 0 and start <= address <= end-count*4,
                     'Original particle allocation leaves its arena')
        return struct.unpack(f'<{count}I', read(address, count*4))

    def call(name, args=(), this=None):
        return host.native_call(api, process, PINS[name], list(args), record, deadline, log, this)

    def pins():
        for _, address, size, digest in PINS.values():
            host.require(hashlib.sha256(read(address, size)).hexdigest() == digest,
                         'Original particle startup callable differs')
        for address, size, digest in CORROBORATION:
            host.require(hashlib.sha256(read(address, size)).hexdigest() == digest,
                         'Original particle constructor/startup publication differs')

    def retained(address, size):
        owners = [(name, base) for name, (base, count) in preloaded.items()
                  if base <= address and address+size <= base+count]
        host.require(len(owners) == 1 and read(owners[0][1], 1) == b'\1',
                     'Particle parameters are not owned by an original loaded source')
        return owners[0][0]

    def source(key):
        pointer = call('lookup', (key,))
        name = retained(pointer, 20)
        value = struct.unpack('<5I', read(pointer, 20))
        log('particles_source', key=key, address=pointer, source=name, header=value)
        host.require(value[1] == key and value[2] == qbkey(name), 'Particle source key/owner differs')
        return value

    pins()
    idle = probe_vm_idle.require_idle(system, context, preloaded)
    count, depth = word(allocator+0xc0), word(allocator+0x10c)
    log('particles_before', singleton=word(GLOBAL), allocator=word(0x84388c),
        heap_count=count, heap_depth=depth, vm=idle)
    host.require(word(GLOBAL) == word(FACTORY_GLOBAL) == 0 and word(0x84388c) == allocator and
                 0 < count < 48 and 1 <= depth <= 16 and
                 word(allocator+0xc8) == allocator+0xc8+depth*4,
                 'Particle startup requires its unused alias and genuine active heap')
    registry = read(allocator, count*4)
    stack = read(allocator+0xc8, 4+depth*4)
    parent_heap = word(allocator+0xc8+depth*4)
    log('particles_parent_heap', parent_heap=parent_heap, arena_start=start, arena_end=end,
        registered_heaps=struct.unpack(f'<{count}I', registry),
        initial_heaps=[word(allocator+0x114), word(allocator+0x118)])
    host.require(parent_extent(parent_heap, start, end) and
                 parent_heap in struct.unpack(f'<{count}I', registry),
                 'Original particle parent is not a registered owned heap')
    parent_fields = struct.unpack('<28I', read(parent_heap, 0x70))
    log('particles_parent_fields', words=parent_fields)
    host.require(parent_fields[0] == 0x6a2200, 'Original particle parent heap class differs')
    if parent_heap in (0x8429a8, 0x8436b0):
        initial_arena = struct.unpack('<8I', read(0x842ad8, 32))
        log('particles_parent_static_arena', words=initial_arena)
        host.require(word(allocator+0x114) == 0x8429a8 and word(allocator+0x118) == 0x8436b0 and
                     word(allocator+0x110) == parent_fields[9] == 0x842ad8 and
                     initial_arena[1] == start and initial_arena[5] == end,
                     'Original placement-constructed primary heap/arena ownership differs')
    before = tuple(word(p) for p in (0x6dcf84, 0x6dcf8c, 0x6dcf90, 0x6dcf98, 0x6dd4a4))
    sources = [source(key) for key in SOURCE_KEYS]
    heap_source, frame_source, lod_source, factory_lod_source = sources
    host.require(heap_source[0] == 0x10400 and frame_source[0] in (0x10400, 0x20400) and
                 lod_source[0] == factory_lod_source[0] == 0x50400, 'Particle startup parameter types differ')
    capacity = (heap_source[3] or 0x5000) & ~15
    retained(lod_source[3], 12)
    pair = struct.unpack('<3I', read(lod_source[3], 12))
    retained(factory_lod_source[3], 12)
    factory_pair = struct.unpack('<3I', read(factory_lod_source[3], 12))
    lod_floats = struct.unpack('<2f', struct.pack('<2I', *pair[1:]))
    frame_rate = (frame_source[3] if frame_source[0] == 0x10400 else
                  struct.unpack('<f', struct.pack('<I', frame_source[3]))[0])
    available = call('free_bytes', this=parent_heap)
    log('particles_configuration', capacity=capacity, parent_heap=parent_heap, parent_free_bytes=available,
        lod=lod_floats, frame_rate=frame_rate)
    host.require(0x5000 <= capacity <= 0x1000000 and available >= capacity+0x2000 and
                 pair[0] == 0x100 and all(math.isfinite(v) for v in lod_floats) and
                 factory_pair[0] == 0x100 and
                 all(math.isfinite(v) for v in struct.unpack('<2f', struct.pack('<2I', *factory_pair[1:]))) and
                 math.isfinite(frame_rate) and 0 <= frame_rate <= 1000,
                 'Original particle source capacity/LOD/frame rate is invalid')
    pointer = call('allocate', (OBJECT_BYTES, 1, 0), allocator)
    words(pointer, 12)
    returned = call('construct', this=pointer)
    state = words(pointer, 12)
    log('particles_constructed', object=pointer, returned=returned, state=state)
    host.require(returned == pointer, 'Original particle constructor returned a foreign object')
    arena, heap, sentinel = words(state[1], 8), words(state[2], 28), words(state[5], 5)
    log('particles_objects', arena=arena, heap=heap, sentinel=sentinel)
    validate_state(state, arena, heap, sentinel, capacity, pair[1:])
    factor = struct.unpack('<f', read(0x76aa04, 4))[0]
    log('particles_after', singleton=word(GLOBAL), heap_count=word(allocator+0xc0),
        heap_depth=word(allocator+0x10c), frame_factor=factor)
    host.require(word(GLOBAL) == 0 and word(allocator+0xc0) == count+1 and
                 read(allocator, count*4) == registry and word(allocator+count*4) == state[2] and
                 word(allocator+0x10c) == depth and read(allocator+0xc8, 4+depth*4) == stack and
                 before == tuple(word(p) for p in (0x6dcf84, 0x6dcf8c, 0x6dcf90, 0x6dcf98, 0x6dd4a4)) and
                 math.isclose(factor, 60/frame_rate if frame_rate else 60, rel_tol=1e-6),
                 'Original particle constructor changed existing ownership or frame configuration')
    host.require([source(key) for key in SOURCE_KEYS] == sources and
                 struct.unpack('<3I', read(lod_source[3], 12)) == pair,
                 'Original particle construction changed source values')
    probe_vm_idle.require_idle(system, context, preloaded)
    pins()
    host.remote_write(api, process, GLOBAL, struct.pack('<I', pointer))
    host.require(word(GLOBAL) == pointer, 'Original particle startup alias was not published')
    factory = call('allocate', (FACTORY_BYTES, 1, 0), allocator)
    words(factory, 9)
    host.require(factory != pointer and call('factory_construct', this=factory) == factory,
                 'Original particle factory constructor returned a foreign object')
    factory_state = words(factory, 9)
    factory_sentinel = words(factory_state[1], 3)
    host.require(factory_state[1] not in (factory, pointer, state[5]),
                 'Original particle factory list aliases another owned object')
    validate_factory(factory_state, factory_sentinel, factory_pair[1:])
    host.require(word(GLOBAL) == pointer and word(FACTORY_GLOBAL) == 0 and
                 word(allocator+0xc0) == count+1 and read(allocator, count*4) == registry and
                 word(allocator+count*4) == state[2] and word(allocator+0x10c) == depth and
                 read(allocator+0xc8, 4+depth*4) == stack and
                 before == tuple(word(p) for p in (0x6dcf84, 0x6dcf8c, 0x6dcf90, 0x6dcf98, 0x6dd4a4)) and
                 struct.unpack('<f', read(0x76aa04, 4))[0] == factor and
                 [source(key) for key in SOURCE_KEYS] == sources and
                 struct.unpack('<3I', read(factory_lod_source[3], 12)) == factory_pair,
                 'Original particle factory changed existing heap/VM/source/frame ownership')
    probe_vm_idle.require_idle(system, context, preloaded)
    pins()
    publish_factory(api, process, system, factory, factory_state)
    log('particles_factory_pass', factory=factory, sentinel=factory_state[1], live_particles=factory_state[2],
        bytes=FACTORY_BYTES, original_base_constructor=True, original_particle_allocator=0x510550,
        source_values_preserved=True, derived_vtable=FACTORY_VTABLE, borrowed_alias=FACTORY_GLOBAL,
        explicit_startup_vtable_writes=1, explicit_startup_alias_writes=1,
        startup_publication_matches_original=True, host_list_or_particle_fields_written=False)
    log('particles_pass', manager=pointer, arena=state[1], heap=state[2], sentinel=state[5],
        bytes=OBJECT_BYTES, heap_capacity=capacity, original_constructor=True,
        source_values_preserved=True, borrowed_alias=GLOBAL, host_particle_fields_written=False,
        original_effect_scripts_retained=True, particles_created_or_drawn=False,
        lifetime='retained until owned child exit', backend_ready=False)
    return pointer
