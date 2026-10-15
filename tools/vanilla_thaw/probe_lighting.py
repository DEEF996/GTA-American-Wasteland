"""Original light statics and whole authored startup, before CModel/CSkater."""
import hashlib
import struct

import probe_core
import probe_fonts
import probe_loader as host
import probe_native

SCRIPT, LIGHTS, SETTINGS = 0x7004771d, 0x769190, 0x769164
BODY_BYTES, SECTION = 142, 1804
PINS = {
    'LightGroupsCRT': ('OriginalLightGroupsCRT', 0x688f00, 39, '633cb29487c9b1d55210b2b56bc4cdc782303f96af0d5b04dde0011d4152286e'),
    'LightSettingsCRT': ('OriginalLightSettingsCRT', 0x688f30, 22, '9b0ec1d75bb076fae3114d1adc5cce16c8da59ade90491f9dff0f4c9d8d3e074'),
    'LightCRTEntries': ('OriginalLightCRTEntries59And60', 0x6bc0ec, 8, 'b18119e5c7874032344920b7567e81cb9a0f391e6f382d9183cf5379ab1f42c2'),
    'GroupCtor': ('OriginalLightGroupCtor', 0x4d1ef0, 137, 'f011532050cbfef328683c3afa99bf9af13687c19bfab063afdfa1fba104a4b3'),
    'EmbeddedCtor': ('OriginalEmbeddedLightCtor', 0x4d1e40, 55, '646d9c9ee05eaa734150507a2b3bccf2260d435feb6d0b4a5f18ff126f9fb83b'),
    'SettingsCtor': ('OriginalLightSettingsCtorEntry', 0x4d51f0, 6, '004ce0912c99b9577cb17611ed1f9595e38315935a933312f4d448a71a95a60b'),
    'GroupExit': ('OriginalLightGroupExitCallback', 0x68cf70, 23, 'd8f0a87f0f8720fed894d6d91741088e9213c7583f8825257a24f441864513ab'),
    'SettingsExit': ('OriginalLightSettingsExitCallback', 0x68cfb0, 10, '73c4c690b0fe4df6b3e651a28ad6d4e9faf1136a1ebc896eb8af708bba142e1a'),
    'InitLightManager': ('OriginalInitLightManagerCFn', 0x47d630, 119, '8f46dd309a0439daab4df4fde40bb72125a23ab4c6b15f3cbe688567498321b2'),
    'StorageHeap': ('OriginalLightStorageHeapCFn', 0x47d6b0, 83, '19d1adaa6017c24d276b9632b5f03d6b1674d005704676ad8ad365c0104acf77'),
    'EnableStorage': ('OriginalEnableLightStorageCFn', 0x47d710, 61, '84aab04c7815229a31311eb3fe71c045832575e029090b31c29e82c81a150360'),
    'Initialize': ('OriginalLightManagerInitialize', 0x4d55d0, 1163, 'e00fa2b26e764d87a723a5de774409326f33fd750cf82c42478c8deefcd87d74'),
    'ModelLightCtor': ('OriginalModelLightCtorPrerequisite', 0x50c340, 252, '1be5f596de9e3a9e1be666b13ddec8b4214264ab09a9ff5aa0cd341b34eb7e60'),
}
BINDINGS = ((1, 0x9de6dc32, 'InitLightManager'), (69, 0xff8d89fd, 'StorageHeap'), (105, 0xe479bc80, 'EnableStorage'))


def validate_source(source):
    probe_fonts.validate_source(source)
    host.require(struct.unpack_from('<5I', source, SECTION)[0:2] == (0x70400, SCRIPT) and
                 struct.unpack_from('<2I', source, SECTION + 12) == (SECTION + 20, 0) and
                 struct.unpack_from('<2I', source, SECTION + 24) == (BODY_BYTES, 94),
                 'Whole stock light startup section differs')
    return source


def read_inputs(qb_manifest, image_manifest):
    return validate_source(probe_fonts.read_inputs(qb_manifest, image_manifest))


def constructed_records(read):
    raw = read(LIGHTS, 4 * 0x90)
    host.require(len(raw) == 4 * 0x90, 'Original light groups extent differs')
    for group in range(4):
        for offset in (0, 0x1c, 0x38, 0x54, 0x70):
            fields = struct.unpack_from('<7I', raw, group * 0x90 + offset)
            host.require(fields[:4] == (0x80484848, 0, 0, 0x3f000000) and fields[5:] == (0, 0),
                         'Original light record constructor defaults differ')
        host.require(struct.unpack_from('<I', raw, group * 0x90 + 0x8c)[0] == 0xffffffff,
                     'Original light group constructor index differs')


def validate_arrays(read, word, words):
    """Observe native arrays; stock authored count is 2, not reTHAWed's 30."""
    for group in range(4):
        record = LIGHTS + group * 0x90
        host.require((word(record), word(record + 0xc), word(record + 0x8c)) ==
                     (0x800000ff, 0x3f000000, 0xffffffff), 'Original light group defaults differ')
        host.require(words(word(record + 4), 8) == (0, 0x3f800000, 0, 0x3f800000) * 2 and
                     words(word(record + 8), 2) == (0x80ff0000,) * 2,
                     'Original stock light directions or colors differ')
        for offset in (0x14, 0x18):
            words(word(record + offset), 2)
        for sub in range(4):
            at = record + 0x1c + sub * 0x1c
            host.require((word(at), word(at + 0xc)) == (0x80484848, 0x3f000000),
                         'Original embedded light defaults differ')
            for offset, count in ((4, 8), (8, 2), (0x14, 2), (0x18, 2)):
                host.require(not any(words(word(at + offset), count)), 'Original embedded light arrays differ')
    host.require(words(word(0x768d20), 2) == (0x3f800000,) * 2, 'Original stock light gains differ')
    collection = words(word(0x768d18), 13)
    host.require(collection[12] == 5 and all(collection[at + 2] == 0 and collection[at + 1] != 0
                 for at in (0, 3, 6, 9)), 'Original light collection is not genuinely empty')


def run(api, process, record, deadline, log, source, preloaded, system):
    validate_source(source)
    read, word = system.read, system.word
    pins = {**host.QB_PINS, **host.VM_PINS, **probe_native.PINS, **PINS}
    start, end = word(0x84376c), word(0x843770)

    def call(name, args=(), this=None):
        return host.native_call(api, process, pins[name], list(args), record, deadline, log, this)

    def words(address, count):
        host.require(address % 4 == 0 and start <= address <= end - count * 4,
                     'Original lighting allocation leaves its native arena')
        return struct.unpack(f'<{count}I', read(address, count * 4))

    def idle():
        host.require(all(word(address) == 0 for address in (0x6dcf84, 0x6dcf8c, 0x6dcf90)) and
                     read(0x6dcf98, 2) == bytes(2) and read(0x6dd4a4, 1) == b'\0' and
                     read(0x6dd4b0, 512) == bytes(512),
                     'Whole light startup must precede original actor/model owners')

    def check_pins():
        for name, address, size, digest in PINS.values():
            host.require(hashlib.sha256(read(address, size)).hexdigest() == digest, 'Original light pin differs: ' + name)

    def callbacks():
        begin, current = word(0x8505f0), word(0x8505ec)
        host.require(begin >= 0x10000 and begin % 4 == current % 4 == 0 and 0 <= current - begin <= 1024,
                     'Original retained CRT exit table is unavailable or oversized')
        return struct.unpack(f'<{(current - begin) // 4}I', read(begin, current - begin))

    idle()
    check_pins()
    host.require(all(word(at) == 0 for at in (0x768d10, 0x768d14, 0x768d18, 0x768d20, 0x769148)) and
                 read(SETTINGS, 0x28) == bytes(0x28) and read(LIGHTS, 4 * 0x90) == bytes(4 * 0x90),
                 'Original lighting statics are already initialized')
    manager, cache = 0x842b18, word(0x6dcfac)
    frontend = word(manager + 0x120)
    words(frontend, 11)
    heap_state = (word(manager + 0xc8), word(manager + 0x10c))
    pointer = probe_core.reuse(api, process, preloaded, probe_fonts.SOURCE_NAME, source)
    symbol = call('lookup', (SCRIPT,))
    host.require(symbol == pointer + SECTION and struct.unpack('<4I', read(symbol, 16)) ==
                 (0x70400, SCRIPT, probe_native.qbkey(probe_fonts.SOURCE_NAME), pointer + SECTION + 20),
                 'Whole light source was replaced or has another native owner')
    for _, key, name in BINDINGS:
        registered = words(call('lookup', (key,)), 5)
        host.require((registered[0] >> 16) & 0xff == 8 and registered[1] == key and registered[3] == PINS[name][1],
                     'Original light callback is not genuinely registered')
    before = callbacks()
    exits = (PINS['GroupExit'][1], PINS['SettingsExit'][1])
    host.require(not any(value in before for value in exits), 'Original light CRT was already registered')
    call('LightGroupsCRT')
    call('LightSettingsCRT')
    constructed_records(read)
    host.require(callbacks() == before + exits and word(SETTINGS + 0xc) == 0,
                 'Original light CRT did not preserve prior ownership or construct an empty settings map')
    cached = call('ScriptCacheLoad', (SCRIPT,), cache)
    words(cached, (BODY_BYTES + 3) // 4)
    body = read(cached, BODY_BYTES)
    host.require(all(body[offset] == 0x43 and struct.unpack_from('<I', body, offset + 1)[0] == PINS[name][1]
                     for offset, _, name in BINDINGS), 'Whole light script did not bind all three original callbacks')
    call('HeapContextPush', (word(manager + 0x128),), manager)
    script = call('ScriptAllocate', (0xfc, 1))
    words(script, 63)
    host.require(call('ScriptCtor', this=script) == script, 'Original light Script constructor failed')
    call('ScriptSetSource', (SCRIPT, 0, 0, 0), script)
    call('HeapContextPop', this=manager)
    host.require(word(script + 0xc4) == 0 and word(script + 0xd0) == SCRIPT and cached <= word(script + 0x10) < cached + BODY_BYTES,
                 'Whole authored light startup was not bound')
    log('lighting_startup_begin', script_checksum=SCRIPT, whole_body_bytes=BODY_BYTES,
        original_crt_initializers=2, authored_max_lights=2, authored_max_diffuse_lights=4, authored_max_vertices=4096)
    host.require(call('ScriptUpdate', this=script) == 0 and word(script + 0x10) == 0 and word(0x6dcf84) == 0,
                 'Whole original light startup did not finish in one update')
    call('ScriptDtor', this=script)
    call('ScriptFree', (script,))
    call('ScriptCacheReleaseReference', (SCRIPT,), cache)
    idle()
    state = (word(0x768d10), word(0x768d14), word(0x769158), word(0x769148), read(0x6c0648, 1)[0], word(SETTINGS + 0xc))
    log('lighting_startup_constructed', max_lights=state[0], max_diffuse_lights=state[1], max_vertices=state[2],
        storage_heap=state[3], storage_enabled=state[4], default_settings=state[5])
    host.require(state == (2, 4, 4096, frontend, 1, 3), 'Whole original light startup configuration differs')
    validate_arrays(read, word, words)
    host.require(callbacks() == before + exits and (word(manager + 0xc8), word(manager + 0x10c)) == heap_state,
                 'Original light startup changed prior CRT ownership or heap context')
    check_pins()
    log('lighting_startup_pass', whole_original_script=True, original_updates=1, original_crt_initializers=2,
        native_groups=4, native_lights_per_group=2, native_diffuse_capacity=4, native_vertices_capacity=4096,
        native_arrays_verified=True, native_default_settings=3, original_callbacks=3,
        lifetime='retained child process', before_actor_and_model=True, host_light_field_writes=False, backend_ready=False)
