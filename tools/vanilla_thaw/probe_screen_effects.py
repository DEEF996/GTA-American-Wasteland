"""Construct the original PC ScreenEffects consumed by authored skating scripts.

The allocator and complete constructor are called unchanged. Only the proven
startup's borrowed singleton alias is published; no native fields/callbacks are
replaced. This initializes effect state, not a second rendering loop.
"""
import hashlib
import struct

import probe_frontend_base
import probe_loader as host

OBJECT_BYTES, GLOBAL, VTABLE = 0x1ec, 0x7689e4, 0x695240
INTERPOLATORS = ((0xc, 0x693d10), (0x5c, 0x693d10),
                 (0xac, 0x693d14), (0xd4, 0x693d14),
                 (0xfc, 0x693d18), (0x12c, 0x693d18),
                 (0x15c, 0x693d1c), (0x184, 0x693d1c),
                 (0x1ac, 0x693d20), (0x1cc, 0x693d20))
PINS = {
    'allocate': ('OriginalScreenEffectsAllocate', 0x6270e0, 180,
                 '4e5e34667814e3762d6f98460608d093e8ab9ae67a78c6618dc4fd121710dcda'),
    'construct': ('OriginalPCScreenEffectsConstructor', 0x51b270, 6,
                  '80e1b1e07d2b1265c25f839d6c712bce3289ade54f407af5c625c2d968560c13'),
}
# The startup allocates 0x1EC with MemMan(size,1,0), calls the PC ctor via
# push-return, then stores EAX in GLOBAL. The base ctor initializes ten native
# interpolators and invokes only the original millisecond getter.
CORROBORATION = (
    (0x473c50, 134, 'bbdd9de375e0e0861abc749021464fa63fe4be38bf19896aaa875c09593d2309'),
    (0x473cd3, 18, '0930245528a0cac3c0df69a09c4b0239853c97b44725e46eda9b03f11402dd3b'),
    (0x473cfc, 19, 'c46b419975db872c00b56ff998129ec823f9602ff26689f1bd14a65dcd604e4e'),
    (0x473d11, 5, '9739c152cd17098f313958780140ae5c0a18e964afe8c0eb08a4aeacfa27503a'),
    (0x51b290, 11, 'cd82cf86b0967f07166c938a7ba7f10cdc64720a6a895804f60d0898af056ca0'),
    (0x4e4a40, 1193, '8fb021044a664474d91f050ff82dc6dc172d2b0a458b11fb462f427941c6dc14'),
    (0x68e3cc, 4, 'd99e58435243d9fef9c88273b8d553b4fba4d0baf8009d29eae74fa99e0d9f57'),
    (VTABLE, 96, '300a33b97cea3a3bcb525726f6e27f81d71b37878f150ae5f99a330c62cb0f5d'),
)


def validate_object(raw):
    host.require(len(raw) == OBJECT_BYTES, 'Original ScreenEffects object extent differs')
    fields = struct.unpack(f'<{OBJECT_BYTES // 4}I', raw)
    host.require(fields[0] == VTABLE and raw[4] == 1 and fields[2] == 0 and
                 fields[0x70 // 4] == 0 and fields[0x74 // 4:0x7c // 4] == (0x3f000000, 0x3f000000) and
                 all(fields[offset // 4] == table for offset, table in INTERPOLATORS),
                 'Original PC ScreenEffects identity or constructed defaults differ')


def run(api, process, record, deadline, log, system):
    read, word = system.read, system.word
    start, end = word(0x84376c), word(0x843770)

    def words(address, count):
        host.require(address % 4 == 0 and start <= address <= end - count * 4,
                     'ScreenEffects dependency leaves the original arena')
        return struct.unpack(f'<{count}I', read(address, count * 4))

    def check_pins():
        for _, address, size, digest in PINS.values():
            host.require(hashlib.sha256(read(address, size)).hexdigest() == digest,
                         'Original ScreenEffects callable pin differs')
        for address, size, digest in CORROBORATION:
            host.require(hashlib.sha256(read(address, size)).hexdigest() == digest,
                         'Original ScreenEffects startup/constructor corroboration differs')

    manager, task_manager = word(0x84388c), word(0x6dd8d8)
    host.require(manager == 0x842b18 and task_manager != 0 and word(GLOBAL) == 0 and
                 word(0x6dcf84) == 0 and read(0x6dcf98, 2) == bytes(2) and read(0x6dd4a4, 1) == b'\0',
                 'ScreenEffects requires untouched singleton and idle original owner/script phases')
    check_pins()
    before_tasks = probe_frontend_base.task_queues(words, task_manager)
    owner_slots = (0x6dcf8c, 0x6dcf90, 0x6dd6b0, 0x6dd8d8, 0x7689e8, 0x76b868,
                   0x7ecd04, 0x84aa7c, manager + 0xc8, manager + 0x10c)
    before = tuple(word(address) for address in owner_slots)
    pointer = host.native_call(api, process, PINS['allocate'], [OBJECT_BYTES, 1, 0], record, deadline, log, manager)
    words(pointer, OBJECT_BYTES // 4)
    result = host.native_call(api, process, PINS['construct'], [], record, deadline, log, pointer)
    host.require(result == pointer, 'Original PC ScreenEffects constructor returned a foreign allocation')
    raw = read(pointer, OBJECT_BYTES)
    fields = struct.unpack(f'<{OBJECT_BYTES // 4}I', raw)
    log('screen_effects_constructed', object=pointer, vtable=fields[0], enabled=raw[4], state=fields[2],
        blur=fields[0x70 // 4], blur_scale_bits=fields[0x74 // 4:0x7c // 4],
        interpolators=[{'offset':offset, 'vtable':fields[offset // 4]} for offset, _ in INTERPOLATORS])
    validate_object(raw)
    host.require(word(GLOBAL) == 0 and tuple(word(address) for address in owner_slots) == before and
                 probe_frontend_base.task_queues(words, task_manager) == before_tasks,
                 'ScreenEffects construction changed existing script/task/render owners')
    check_pins()
    host.remote_write(api, process, GLOBAL, struct.pack('<I', pointer))
    host.require(word(GLOBAL) == pointer, 'Original ScreenEffects borrowed alias was not published')
    log('screen_effects_pass', object=pointer, bytes=OBJECT_BYTES, vtable=VTABLE,
        whole_original_constructor=True, original_allocator=True, native_interpolators=10,
        borrowed_alias=GLOBAL, host_native_field_writes=False, replacement_callbacks=False,
        rendering_or_window_tasks_registered=False, existing_owner_identities_preserved=True,
        lifetime='original arena retained until bounded child termination', backend_ready=False)
    return pointer
