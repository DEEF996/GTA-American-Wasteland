"""Initialize the original level-node registry before original gameplay updates.

Stock level loading calls the lazy factory at 0x56E283. Skater callbacks use
the registry even when a private scene has no interactive level nodes. Calling
the same factory provides the genuine empty table; no lookup or script is
patched, and no registry fields are written by the host.
"""
import hashlib
import struct

import probe_loader as host

REGISTRY = 0x83E874
PINS = {
    'factory': ('OriginalLevelNodeRegistryFactory', 0x583130, 130,
        '3927458312fe2fb5df8975966a0c98119eec5aeb14106df815877f6134808f53'),
    'constructor': ('OriginalLevelNodeRegistryCtor', 0x583060, 203,
        'eb9e9d02ab44dc5885ff13424a5e75a0cd41a4f87451df50bcbc2ee9fdd14ca4'),
    'caller': ('OriginalLevelNodeRegistryLevelLoadCaller', 0x56e275, 19,
        '2dfb98a1b8995296877b698e61acedaef0d0b0e6786c4920247beb3e4e83b4e0'),
}


def run(api, process, record, deadline, log, system):
    for name, address, size, digest in PINS.values():
        host.require(hashlib.sha256(system.read(address, size)).hexdigest() == digest,
                     'Pinned original level registry function differs: ' + name)
    host.require(system.word(0x84388c) == 0x842b18 and
                 system.read(0x6dd4a4, 1) == b'\0' and
                 system.read(0x6dcf98, 2) == bytes(2),
                 'Level registry initialization requires idle original owner phases')
    before = system.word(REGISTRY)
    heap_before = system.read(0x842b18 + 0xc8, 0x48)
    if before == 0:
        host.native_call(api, process, PINS['factory'], [], record, deadline, log)
    pointer = system.word(REGISTRY)
    start, end = system.word(0x84376c), system.word(0x843770)
    host.require(pointer % 4 == 0 and start <= pointer <= end - 0x1c,
                 'Original level registry factory did not publish its native owner')
    raw = system.read(pointer, 0x1c)
    bits, buckets, count = struct.unpack_from('<3I', raw)
    host.require(bits == 4 and buckets % 4 == 0 and start <= buckets <= end - 16*12
                 and raw[0x14:0x17] == bytes(3) and
                 system.read(0x842b18 + 0xc8, 0x48) == heap_before,
                 'Original level registry constructor or heap context differs')
    if before == 0:
        host.require(count == 0 and system.read(buckets, 16*12) == bytes(16*12),
                     'New original level registry is not the genuine empty hash table')
    log('level_node_registry_ready', registry=pointer, buckets=buckets,
        native_factory=PINS['factory'][1], stock_level_load_caller=0x56e283,
        initialized=before == 0, host_registry_writes=False)
    return pointer
