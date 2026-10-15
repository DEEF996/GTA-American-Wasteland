"""Original object-pool factory required by CreateBailBoard.

The original factory publishes its singleton, constructs its default pool and
owns every list. No replacement game-object factory or pool fields are written.
"""
import hashlib
import struct

import probe_loader as host
import probe_vm_idle

GLOBAL, REFERENCES = 0x6e8818, 0x6e881c
PINS = {
    'factory': ('OriginalObjectPoolRegistryFactory', 0x48e6f0, 77,
                '95ea839a524b3b3078f71dc672a0be07a07ae89672bfc22ba4fc9c38d42610b9'),
    'lookup': ('OriginalObjectPoolRegistryLookup', 0x48ced0, 6,
               '9a81a46c490153c4a84403e47d1f96707509eb44d203509a7e4185805da07510'),
}
CORROBORATION = (
    (0x5435b5, 18, '4b3ddd6af9d12641b6adc8f1454d20d5ad1df7123da6ec8a8c1451717f92f261'),
    (0x48e2d0, 6, '1ac629e36b2085774eae439b12b6297e1f721886b3dea447a86eb6b54484e56a'),
    (0x48dee0, 267, '5d7c47864781f361550bc0e9523d557f588ebc534914ed2c7292d3e7538220a3'),
    (0x4a1220, 71, 'b6cb1914e5b12cf4d0466227880a7e3d5e350711867aaede4fce99d49ecd9869'),
    (0x14008f0, 289, '5634891aaefdad80c0831d656fa2d078985aa120e9b59ec09a83643bc4f748c9'),
    (0x1431560, 14, '0401b166b45f7c571f28de0871bc9a6e8c420bac363cc17c3688ec70069bb9a0'),
    (0x142ccf0, 14, 'ffd3ca3eb4cd8d0dd805e47bc4a158cfa08d9e22546501318a77915369153f7e'),
    (0x41ad40, 99, '95aa141e3c8e58dc90af1c70fe74d2da30f77e7a4638aaa088d53bd950205610'),
    (0x46b3b0, 1102, 'a43a29a481e9d9967ab457646afab7a0338dd23b42c5afe7ae1966701bd3cd5a'),
)
# These two original import relays resolve on their first native invocation.
# Their entry stubs, cold code and complete resolved bodies remain pinned above.
RELAYS = {
    'constructor': (0x13d2660, 0x142ccf0, 0x14008f0),
    'lookup': (0x13d2030, 0x1431560, 0x4a1220),
}


def validate_relay_targets(read, stage):
    host.require(stage in ('before', 'factory', 'lookup'), 'Unknown object-pool relay stage')
    for name, (slot, cold, resolved) in RELAYS.items():
        expected = resolved if stage == 'lookup' or (stage == 'factory' and name == 'constructor') else cold
        host.require(read(slot, 4) == struct.pack('<I', expected),
                     f'Original object-pool {name} relay differs after {stage}')


def validate_default(fields):
    expected = {0: 0x48d610, 0x08: 0, 0x10: 0, 0x14: 0, 0x18: 0, 0x1c: 0,
        0x20: 2, 0x24: 0, 0x28: 5, 0x2c: 5, 0x30: 0, 0x34: 0, 0x38: 1,
        0x3c: 0x3dcccccd, 0x40: 0, 0x48: 0x80, 0x4c: 0, 0x50: 0xf4240,
        0x54: 0, 0x58: 0, 0x5c: 0, 0x60: 0, 0x64: 0, 0x6c: 0xffffffff}
    host.require(len(fields) == 28 and fields[0x44//4] & 255 == 0 and
        all(fields[offset//4] == value for offset, value in expected.items()),
        'Original default object-pool constructor fields differ')


def run(api, process, record, deadline, log, context, system, *, preloaded):
    read, word = system.read, system.word
    start, end = word(0x84376c), word(0x843770)

    def words(address, count):
        host.require(address % 4 == 0 and start <= address <= end-count*4,
                     'Object-pool allocation leaves the original arena')
        return struct.unpack(f'<{count}I', read(address, count*4))

    def pins(stage):
        for address, count, digest in [value[1:] for value in PINS.values()] + list(CORROBORATION):
            raw = read(address, count)
            actual = hashlib.sha256(raw).hexdigest()
            if actual != digest:
                log('object_pools_pin_mismatch', address=address, size=count,
                    expected=digest, actual=actual, observed=raw.hex())
            host.require(actual == digest,
                         f'Original object-pool entry or constructor changed at {address:#x}')
        validate_relay_targets(read, stage)

    pins('before')
    probe_vm_idle.require_idle(system, context, preloaded)
    allocator, depth = word(0x84388c), word(0x842b18+0x10c)
    host.require(word(GLOBAL) == word(REFERENCES) == 0 and allocator == 0x842b18 and
                 word(0x83e294) & 255 == 1 and 1 <= depth <= 16,
                 'Object pools require pristine singleton and initialized memory')
    stack = read(allocator+0xc8, 4+depth*4)
    host.require(word(allocator+0xc8) == allocator+0xc8+depth*4, 'Native heap stack differs')
    registry = host.native_call(api, process, PINS['factory'], [1], record, deadline, log)
    pins('factory')
    host.require(word(GLOBAL) == registry and word(REFERENCES) == 1,
                 'Original factory did not publish its singleton')
    header = words(registry, 2)
    host.require(header[1] == 1, 'Default pool was not registered')
    sentinel = words(header[0], 3)
    node = sentinel[1]
    host.require(node != header[0] and sentinel[2] == node, 'Object-pool list sentinel differs')
    link = words(node, 3)
    host.require(link[1:] == (header[0], header[0]), 'Default pool ownership differs')
    pool = link[0]
    fields = words(pool, 28)
    validate_default(fields)
    owners = [registry, header[0], node, pool]
    for address in (fields[1], fields[3]):
        host.require(address not in owners, 'Object-pool list aliases another owner')
        owners.append(address)
        host.require(words(address, 3)[1:] == (address, address), 'Default pool list is not empty')
    # +68 is deliberately not initialized by the original constructor. Use the
    # exact consumer lookup; don't assume or write a convenient checksum there.
    gameobj = host.native_call(api, process, PINS['lookup'], [0x3b5737a6, 0],
                              record, deadline, log, registry)
    host.require(gameobj == 0 and words(registry, 2) == header and
                 word(GLOBAL) == registry and word(REFERENCES) == 1 and
                 read(allocator+0xc8, 4+depth*4) == stack and word(allocator+0x10c) == depth,
                 'Object-pool lookup changed native ownership')
    probe_vm_idle.require_idle(system, context, preloaded)
    log('object_pools_after_targets', constructor_target=word(0x13d2660), lookup_target=word(0x13d2030))
    pins('lookup')
    log('object_pools_pass', registry=registry, default_pool=pool, sentinel=header[0],
        original_gameobj_lookup=gameobj, original_factory=True, host_pool_writes=False,
        lifetime='retained until owned child exit')
    return registry
