"""Execute complete stock Career startup before any native skater or frame.

The 24 level records and 194 chapter atoms are created by the original scripts
and callbacks. Source parsing only supplies independent expected metadata.
"""
import hashlib
import json
from pathlib import Path
import struct

import probe_core
import probe_loader as host
import probe_native
import probe_profile
from assets_export import region
from carentan_qb import node_fields

SETUP, CHAPTER, PARAMS = 0x4910c852, 0x5ff1a089, 0x4394171d
SOURCES = (
    (168, 'scripts/game/menu/chapter_info.qb.wpc', 14096, 'daf1568bc126057aac50bb7561c9bb6358b191eaa7545b617779bf753d7b2774', CHAPTER, 4004, 7, 28189, 9579),
    (171, 'scripts/game/levels.qb.wpc', 30112, 'aa33604adb41edee74590de1a0f50ab07a64a0e9115165e81411f8271c06f053', PARAMS, 2464, 10, 0, 0),
    (218, 'scripts/game/career_scripts.qb.wpc', 6944, '8eaecc606c072f61dccd536ddc2110b18f7f5606ccca5153bda93b06254c6959', SETUP, 28, 7, 94, 65),
)
PINS = {
    'CareerFunc': ('OriginalCareerFunction', 0x59a5b0, 99, '5c4be2a36721fbbe0182d8dda3b6cba36eb5dd587408e048bf0dbff07c8b816d'),
    'CareerDispatch': ('OriginalCareerDispatchRegion', 0x595820, 2262, 'da60d3306b934bb338ec6c8fd1ec4c6b245027eb211e538a78fab63fa188b0f4'),
    'InitLevelTables': ('OriginalCareerInitLevelTables', 0x5955b0, 621, '36e1e17b565f834b914b69d82b6b6ee7256db5b2a6f30938335c424f6917c8a1'),
    'CareerReset': ('OriginalCareerReset', 0x595550, 90, '051edba295401d46074e9cf9d85d9335b2c28780fcc6c93b4528599cbaa93978'),
    'AtomicRegister': ('OriginalCareerAtomicRegister', 0x586330, 497, '8b6ad93cdfe1a9f1816c165ac22f3693ac1a234572644e16139f226320f4c969'),
    'HashLookup': ('OriginalCareerHashLookupRegion', 0x433a60, 72, 'ad6d761176b5488c13ec5a70c350f7252f98d6b973449c1fe26afc80ece94b6e'),
}


def level_record(source, pointer, index):
    fields = node_fields(source, pointer)
    kind, array = fields[0x0dccb6bf]
    host.require(kind == 0x1900, 'Career names field is not an original array')
    kind, count, table = struct.unpack('<3I', region(source, array, 12, 'Career names array'))
    host.require(kind == 0x30100 and 0 < count < 16, 'Career names array type/count differs')
    pointers = [table] if count == 1 else struct.unpack(f'<{count}I', region(source, table, count * 4, 'Career names pointers'))
    names = []
    for name in pointers:
        host.require(0 <= name < len(source), 'Career name leaves original source')
        end = source.find(b'\0', name, min(name + 128, len(source)))
        host.require(end > name, 'Career name is unterminated or empty')
        names.append(source[name:end].decode('ascii'))
    include, frontend = fields[0x97a3a64b], fields[0xa6479767]
    host.require(include[0] == frontend[0] == 0x300 and frontend[1] in (0, 1), 'Career level flags differ')
    collectors = int((0x1b00, 0x0f87f60b) in fields.values())
    return {'index':index, 'words':(probe_native.qbkey('level_' + names[0]), index, collectors, frontend[1]),
            'authored_names':count, 'names':names}


def validate_source(source, row):
    _, _, size, digest, key, section, kind, unpacked, packed = row
    host.require(len(source) == size and hashlib.sha256(source).hexdigest() == digest,
                 'Whole selected Career source differs')
    header = struct.unpack_from('<5I', source, section)
    host.require(header[0] == (kind << 16) | 0x400 and header[1] == key and header[3] == section + 20 and header[4] == 0,
                 'Original Career source section differs')
    if kind == 7:
        host.require(struct.unpack_from('<2I', source, section + 24) == (unpacked, packed),
                     'Whole original Career script extent differs')
    return source


def read_inputs(qb_manifest, image_manifest):
    qb = json.loads(qb_manifest.read_text(encoding='utf-8-sig'))
    image = json.loads(image_manifest.read_text(encoding='utf-8-sig'))
    directory = qb_manifest.resolve().parent
    host.require(qb.get('format') == 'stock-thaw-core-qb-cache-v1' and qb.get('sources_revalidated_before_publish') is True and
                 Path(qb['source_directory']).resolve() == Path(image['source']).resolve() and
                 not directory.is_relative_to(Path(image['source']).resolve()), 'Career requires selected separate stock source cache')
    result = []
    for row in SOURCES:
        index, name, size, digest = row[:4]
        matches = [item for item in qb['members'] if item['order'] == index]
        host.require(len(matches) == 1 and (matches[0]['canonical_name'], matches[0]['bytes'], matches[0]['sha256']) ==
                     (name, size, digest), 'Original Career source manifest differs')
        path = (directory / matches[0]['cache_file']).resolve()
        host.require(path.is_relative_to(directory), 'Career source escapes private cache')
        result.append(validate_source(path.read_bytes(), row))
    kind, array = node_fields(result[1], 2484)[0x630f75b0]
    host.require(kind == 0x1900 and struct.unpack_from('<3I', result[1], array) == (0xa0100, 24, 2520),
                 'Original stock Career level table differs')
    levels = [level_record(result[1], pointer, index) for index, pointer in enumerate(struct.unpack_from('<24I', result[1], 2520))]
    host.require(sum(row['authored_names'] for row in levels) == 46, 'Original stock Career alias census differs')
    return {'sources':result, 'levels':levels}


def run(api, process, record, deadline, log, inputs, preloaded, system, game_context):
    read, word = system.read, system.word
    start, end = word(0x84376c), word(0x843770)
    pins = {**host.QB_PINS, **host.VM_PINS, **probe_native.PINS, **probe_profile.PINS, **PINS}

    def call(name, args=(), this=None):
        return host.native_call(api, process, pins[name], list(args), record, deadline, log, this)

    def words(address, count):
        host.require(address % 4 == 0 and start <= address <= end - count * 4, 'Career object leaves original arena')
        return struct.unpack(f'<{count}I', read(address, count * 4))

    def idle():
        host.require(all(word(address) == 0 for address in (0x6dcf84, 0x6dcf8c, 0x6dcf90)) and
                     read(0x6dcf98, 2) == bytes(2) and read(0x6dd4a4, 1) == b'\0',
                     'Whole Career startup must precede original script owners and updates')

    def check_pins():
        for _, address, size, digest in PINS.values():
            host.require(hashlib.sha256(read(address, size)).hexdigest() == digest, 'Original Career native pin differs')

    idle()
    check_pins()
    game = words(game_context['game'], 0x9c // 4)
    career = game[0x20 // 4]
    state = words(career, 0x264 // 4)
    mapping, collection, level = state[0xa8 // 4], state[0x240 // 4], state[0xb0 // 4]
    map_state, atoms = words(mapping, 7), words(collection, 6)
    host.require(game[0] == 0x69807c and state[0] == 0x698a50 and map_state[0] == 10 and map_state[2] == 0 and
                 atoms[0] == 0x69876c and atoms[3] == 0 and level < 24, 'Whole Career startup requires untouched native tables')
    words(map_state[1], 1024 * 3)
    host.require(words(atoms[2], 3)[1:] == (atoms[2], atoms[2]), 'Original Career atom list is not empty')
    for row, source in zip(SOURCES, inputs['sources']):
        validate_source(source, row)
        _, name, _, _, key, offset, kind, _, _ = row
        pointer = probe_core.reuse(api, process, preloaded, name, source)
        symbol = call('lookup', (key,))
        host.require(symbol == pointer + offset and struct.unpack('<4I', read(symbol, 16)) ==
                     ((kind << 16) | 0x400, key, probe_native.qbkey(name), pointer + offset + 20),
                     'Whole Career source was replaced or has another native owner')
    manager, cache = 0x842b18, word(0x6dcfac)
    heap_state = (word(manager + 0xc8), word(manager + 0x10c))
    chapter_cache = call('ScriptCacheLoad', (CHAPTER,), cache)
    words(chapter_cache, (28189 + 3) // 4)
    cached = call('ScriptCacheLoad', (SETUP,), cache)
    body = read(cached, 94)
    host.require(all(body[offset] == 0x43 and struct.unpack_from('<I', body, offset + 1)[0] == PINS['CareerFunc'][1]
                     for offset in (1, 59)), 'Whole Career setup cache did not bind both original callbacks')
    call('HeapContextPush', (word(manager + 0x128),), manager)
    script = call('ScriptAllocate', (0xfc, 1))
    words(script, 63)
    host.require(call('ScriptCtor', this=script) == script, 'Original Career Script constructor failed')
    call('ScriptSetSource', (SETUP, 0, 0, 0), script)
    call('HeapContextPop', this=manager)
    script_state = words(script, 63)
    host.require(script_state[0xc4 // 4] == 0 and script_state[0xd0 // 4] == SETUP and
                 cached <= script_state[4] < cached + 94, 'Whole authored Career setup was not bound')
    log('career_startup_begin', career=career, script_checksum=SETUP, whole_body_bytes=94,
        nested_script_checksum=CHAPTER, nested_whole_body_bytes=28189, source_level_count=24,
        source_alias_count=46, expected_native_insertions=184, expected_chapter_atoms=194)
    host.require(call('ScriptUpdate', this=script) == 0 and word(script + 0x10) == 0 and word(0x6dcf84) == 0,
                 'Whole authored Career setup did not finish in one original update')
    call('ScriptDtor', this=script)
    call('ScriptFree', (script,))
    call('ScriptCacheReleaseReference', (SETUP,), cache)
    call('ScriptCacheReleaseReference', (CHAPTER,), cache)
    idle()
    state, after_map, after_atoms = words(career, 0x264 // 4), words(mapping, 7), words(collection, 6)
    log('career_startup_constructed', native_hash_insertions=after_map[2], native_atoms=after_atoms[3], current_level=state[0xb0 // 4])
    host.require(state[0xa8 // 4] == mapping and state[0x240 // 4] == collection and state[0xb0 // 4] == level and
                 after_map[0:2] == map_state[0:2] and after_map[2] == 184 and after_atoms[0] == atoms[0] and
                 after_atoms[2] == atoms[2] and after_atoms[3] == 194, 'Whole original Career table counts or owners differ')
    scratch = system.allocate(bytes(16))
    actual, seen = [], set()
    for row in inputs['levels']:
        host.remote_write(api, process, scratch, struct.pack('<I', row['index']))
        pointer = call('HashLookup', (scratch, 1), mapping)
        values = words(pointer, 4)
        host.require(pointer not in seen and values == row['words'], 'Native Career level record differs from its complete original source')
        seen.add(pointer)
        actual.append({'index':row['index'], 'record_words':values})
    # Career Reset precedes the user's selected maxima. CSkater bootstrap applies
    # the corresponding ten native Career setters after this stage.
    profile = game_context['profile']
    for key in probe_profile.STATS:
        host.require(call('SetStat', (key, 10), profile) & 0xff, 'Original profile rejected its post-Career maximum statistic')
    host.require([call('GetStat', (key,), profile) for key in probe_profile.STATS] == [10] * 10 and
                 (word(manager + 0xc8), word(manager + 0x10c)) == heap_state,
                 'Career startup changed profile maxima or the original heap context')
    check_pins()
    log('career_startup_pass', whole_original_setup=True, whole_original_nested_chapter=True,
        original_updates=1, native_level_records=actual, native_level_count=24, authored_alias_count=46,
        native_hash_insertions=184, native_chapter_atoms=194, current_level_preserved=level,
        profile_stats=[10] * 10, scripts_destroyed=True, host_career_field_writes=False,
        host_hash_or_atom_writes=False, missions_or_cutscenes_executed=False, backend_ready=False)
    return career
