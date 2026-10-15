"""Load complete stock animation banks with the original authored loader.

Private original files and the complete stock script remain unchanged. The
original MemInitHeap callback doubles its authored size; this is observed,
not overridden. All resources and parameter storage live until child exit.
"""
import hashlib
import json
from pathlib import Path
import struct

import probe_core
import probe_fonts as fonts
import probe_hud
import probe_loader as host
import probe_native
import probe_profile
import probe_resources
import probe_skater_animation as animation
from import_assets import INPUTS

SOURCE_NAME = 'scripts/game/skutils.qb.wpc'
SOURCE_BYTES = 42376
SOURCE_SHA = 'b594dde4f7b2c26c9114d05f2206d14eb13f460c2fd1d54da9f7ae17fde02493'
SCRIPT_KEY, BODY_BYTES = 0xa49ee0c3, 400
HEAP_KEY, AUTHORED_SIZE, ACTUAL_CAPACITY = 0x0d2000dd, 15728640, 31457280
BANKS = ('data/pak/perm_anims/perm_anims.pak.wpc', 'data/pak/unloadable_anims/unloadable_anims.pak.wpc')
FLAGS = ((0xc375916d, 8208), (0xadcfedf9, 18892), (0x2d1599e0, 18912))
PINS = {
    'MemInitHeap': ('OriginalAnimationMemInitHeap', 0x480fa0, 101, 'd4fc07c43887fbcdedbf32e93e70c25b4c30e011b7871247172356c7d8a94808'),
    'SetReferenceChecksum': ('OriginalAnimationSetReferenceChecksum', 0x47a750, 67, '80c336d3a8eca675e0deaf111f27e85cbb8e6c5a02821b5518e983e14ca9d48a'),
}
PARAMETERS = ((150, 161, '5f6876598a9d02720a6ed430ca68c0f8fb4d025e7fc33dfe739699940d28c7de'),
              (201, 239, '2406ed0059f2f19ea3df239cc987502d47f59c0d2b3ca816084a833533a0a5e3'),
              (256, 261, 'eb1549f3f3d140d8bb1b86656bc7779b750de4214984107d82a5cb0ef764d4e0'))


def validate_source(source):
    host.require(len(source) == SOURCE_BYTES and hashlib.sha256(source).hexdigest() == SOURCE_SHA,
                 'Whole stock skutils source differs')
    host.require(struct.unpack_from('<I', source, 19316)[0] == SCRIPT_KEY and
                 struct.unpack_from('<2I', source, 19336) == (BODY_BYTES, 246), 'Stock whole animation loader header differs')
    return source


def validate_parameters(body):
    host.require(len(body) == fonts.BODY_BYTES, 'Original startup body extent differs')
    for begin, end, digest in PARAMETERS:
        host.require(hashlib.sha256(body[begin:end + 1]).hexdigest() == digest, 'Whole authored animation heap parameters differ')
    for at, name in ((196, 'MemInitHeap'), (251, 'SetReferenceChecksum')):
        host.require(body[at] == 0x43 and struct.unpack_from('<I', body, at + 1)[0] == PINS[name][1],
                     'Original startup animation callback binding differs')


def clips(data, expected_count):
    position, result, source_count = 0, [], 0
    while position + 32 <= len(data):
        kind, relative, size, key, _, _, _, flags = struct.unpack_from('<8I', data, position)
        if kind in (0xb524565f, 0x2cb3ef3b):
            break
        start = position + relative
        host.require(start >= position + 32 and start <= len(data) - size, 'Original animation member leaves its whole PAK')
        if kind == 0x745dcd45:
            host.require(size >= 48 and struct.unpack_from('<I', data, start)[0] == 40,
                         'Original bank contains a different SKA format')
            result.append({'key':key, 'bytes':size, 'header':data[start:start + 20]})
        else:
            host.require(kind == 0x5d796624, 'Original animation bank contains an unknown resource kind')
            source_count += 1
        position += 192 if flags & 32 else 32
    host.require(len(result) == expected_count and len({r['key'] for r in result}) == expected_count and source_count == 1,
                 'Whole stock animation bank clip/source census differs')
    return result


def flag_snapshot(lookup, read, source_pointer):
    result = []
    for key, offset in FLAGS:
        pointer = lookup(key)
        host.require(pointer != 0, 'Original animation load flag disappeared from the native registry')
        header, name, owner, value = struct.unpack('<4I', read(pointer, 16))
        result.append({'key':key, 'symbol':pointer, 'header':header, 'name':name, 'owner':owner,
                       'value':value, 'source_symbol':source_pointer + offset,
                       'source_value':struct.unpack('<I', read(source_pointer + offset + 12, 4))[0]})
    return result


def read_inputs(qb_manifest, resource_manifest, image_manifest):
    startup = fonts.read_inputs(qb_manifest, image_manifest)
    qb = json.loads(qb_manifest.read_text(encoding='utf-8-sig'))
    rows = [r for r in qb['members'] if r['order'] == 27]
    host.require(len(rows) == 1 and rows[0]['canonical_name'] == SOURCE_NAME and
                 rows[0]['bytes'] == SOURCE_BYTES and rows[0]['sha256'] == SOURCE_SHA, 'Stock animation source member differs')
    source_path = (qb_manifest.resolve().parent / rows[0]['cache_file']).resolve()
    host.require(source_path.is_relative_to(qb_manifest.resolve().parent), 'Original animation source escapes private cache')
    source = validate_source(source_path.read_bytes())
    value = json.loads(resource_manifest.read_text(encoding='utf-8-sig'))
    image = json.loads(image_manifest.read_text(encoding='utf-8-sig'))
    directory, original = resource_manifest.resolve().parent, Path(image['source']).resolve()
    host.require(value.get('format') == 'gta-thaw-private-stock-resources-v1' and
                 value.get('sources_revalidated_before_publish') is True and value.get('original_files_modified') is False and
                 Path(value['directory']).resolve() == directory and Path(value['source']).resolve() == original and
                 not directory.is_relative_to(original), 'Animation banks require the selected separate private resource cache')
    banks = []
    for name, count in zip(BANKS, (463, 833)):
        size, digest = INPUTS[name]
        rows = [r for r in value['files'] if r['path'].lower() == name.lower()]
        host.require(len(rows) == 1 and (rows[0]['bytes'], rows[0]['sha256']) == (size, digest), 'Whole animation bank pin differs')
        path, source_path = (directory / name).resolve(), (original / name).resolve()
        host.require(path.is_relative_to(directory) and source_path.is_relative_to(original) and not path.samefile(source_path),
                     'Original animation bank aliases or escapes its private cache')
        data = path.read_bytes()
        host.require(len(data) == size and hashlib.sha256(data).hexdigest() == digest and data == source_path.read_bytes(),
                     'Private animation bank differs from the selected original')
        banks.append({'path':name, 'bytes':size, 'sha256':digest, 'clips':clips(data, count)})
    host.require(len({c['key'] for b in banks for c in b['clips']}) == 1296, 'Stock banks have overlapping animation keys')
    return {'startup':startup, 'source':source, 'directory':directory, 'banks':banks}


def run(api, process, record, deadline, log, inputs, preloaded, system):
    source = validate_source(inputs['source'])
    fonts.validate_source(inputs['startup'])
    pins = {**host.QB_PINS, **host.VM_PINS, **probe_native.PINS, **probe_profile.PINS, **fonts.PINS,
            **animation.PINS, **{r[0]:r for r in probe_resources.read_metadata()['functions']},
            **PINS, 'SpawnScript':probe_hud.SPAWN}
    read, word = system.read, system.word

    def call(name, args=(), this=None):
        return host.native_call(api, process, pins[name], list(args), record, deadline, log, this)

    start, end = word(0x84376c), word(0x843770)

    def words(address, count):
        host.require(address % 4 == 0 and start <= address <= end - count * 4, 'Native animation object leaves original arena')
        return struct.unpack(f'<{count}I', read(address, count * 4))

    def registry():
        count, pointer, previous, result = word(0x6dcf90), word(0x6dcf8c), 0, {}
        host.require(count <= 4096, 'Original Script registry exceeds its bound')
        while pointer:
            host.require(pointer not in result and len(result) < count, 'Original Script registry cycles')
            state = words(pointer, 63)
            host.require(state[2] == previous, 'Original Script backlink differs')
            result[pointer] = (state[0xb0 // 4], state[0xc4 // 4])
            previous, pointer = pointer, state[1]
        host.require(len(result) == count and len({v[0] for v in result.values()}) == count, 'Original Script count or IDs differ')
        return result

    manager, cache, context = 0x842b18, word(0x6dcfac), word(0x6dd3f4)
    host.require(word(0x6dcf84) == 0 and read(0x6dcf98, 2) == bytes(2) and read(0x6dd4a4, 1) == b'\0',
                 'Original animation banks must load between gameplay phases')
    cwd = system.allocate(bytes(2048))
    length = system.call('kernel32.dll', 'GetCurrentDirectoryW', (1024, cwd))
    host.require(0 < length < 1024 and Path(read(cwd, length * 2).decode('utf-16le')).resolve() == inputs['directory'],
                 'Animation bank file access leaves the verified private working directory')
    source_pointer = probe_core.reuse(api, process, preloaded, SOURCE_NAME, source)
    startup_pointer = probe_core.reuse(api, process, preloaded, fonts.SOURCE_NAME, inputs['startup'])
    for key, offset in FLAGS:
        host.require(call('lookup', (key,)) == source_pointer + offset and
                     struct.unpack('<4I', read(source_pointer + offset, 16)) ==
                     (0x10400, key, probe_native.qbkey(SOURCE_NAME), 0), 'Original animation load flags are not untouched stock defaults')
    for key, offset, pointer, owner in ((SCRIPT_KEY, 19312, source_pointer, SOURCE_NAME),
                                      (fonts.SCRIPT_KEY, 740, startup_pointer, fonts.SOURCE_NAME)):
        host.require(call('lookup', (key,)) == pointer + offset and
                     struct.unpack('<4I', read(pointer + offset, 16)) ==
                     (0x70400, key, probe_native.qbkey(owner), pointer + offset + 20), 'Whole animation script provenance differs')
    host.require(call('NamedHeapLookup', (HEAP_KEY, 0), manager) == 0, 'Original unloadable animation heap already exists')
    baseline = registry()
    startup = call('ScriptCacheLoad', (fonts.SCRIPT_KEY,), cache)
    words(startup, (fonts.BODY_BYTES + 3) // 4)
    validate_parameters(read(startup, fonts.BODY_BYTES))
    heap_context = (word(manager + 0xc8), word(manager + 0x10c))
    call('HeapContextPush', (word(manager + 0x128),), manager)
    helper = call('ScriptAllocate', (0xfc, 1))
    words(helper, 63)
    host.require(call('ScriptCtor', this=helper) == helper, 'Original animation helper constructor failed')
    call('ScriptSetSource', (259952684, 0, 0, 0), helper)
    call('HeapContextPop', this=manager)
    locals_ = word(helper + 0x1c)
    words(locals_, 2)
    params = system.allocate(bytes(48))
    scratch, reference = params + 16, params + 32
    host.require(call('StructCtor', this=params) == params and call('StructCtor', this=reference) == reference,
                 'Original animation parameter headers were not constructed')
    begin, finish, _ = PARAMETERS[0]
    host.require(call('ParseParameters', (locals_, startup + begin, 0, scratch)) == startup + finish and word(scratch) == 0 and
                 call('StructGetInteger', (probe_native.qbkey('heap_size'), scratch, 1), locals_) & 0xff and word(scratch) == AUTHORED_SIZE,
                 'Original PC assignment did not produce its authored heap_size')
    host.remote_write(api, process, scratch, bytes(4))
    begin, finish, _ = PARAMETERS[1]
    host.require(call('ParseParameters', (params, startup + begin, locals_, scratch)) == startup + finish and word(scratch) == 0 and
                 call('StructGetInteger', (0x083fdb95, scratch, 1), params) & 0xff and word(scratch) == AUTHORED_SIZE,
                 'Original parser did not resolve the complete authored MemInitHeap parameters')
    host.require(call('MemInitHeap', (params, helper)) & 0xff, 'Original animation MemInitHeap failed')
    heap = call('NamedHeapLookup', (HEAP_KEY, 0), manager)
    heap_words = words(heap, 15)
    arena = words(heap_words[0x24 // 4], 8)
    host.require(arena[0] == 0x6a2354 and arena[3] == ACTUAL_CAPACITY and arena[5] - arena[1] == ACTUAL_CAPACITY,
                 'Original MemInitHeap did not retain its actual doubled capacity')
    host.remote_write(api, process, scratch, bytes(4))
    begin, finish, _ = PARAMETERS[2]
    host.require(call('ParseParameters', (reference, startup + begin, 0, scratch)) == startup + finish and word(scratch) == 0 and
                 call('SetReferenceChecksum', (reference, helper)) & 0xff, 'Original authored reference checksum setup failed')
    # NamedHeapCreate copies the name into its own registry slot before the
    # heap constructor uses it (original 0x626E0C..0x626E1E).
    call('StructClear', this=params)
    call('StructClear', this=reference)
    call('ScriptDtor', this=helper)
    call('ScriptFree', (helper,))
    call('ScriptCacheReleaseReference', (fonts.SCRIPT_KEY,), cache)
    host.require(registry() == baseline and (word(manager + 0xc8), word(manager + 0x10c)) == heap_context,
                 'Original animation parameter context changed existing Scripts or heap stack')
    cached = call('ScriptCacheLoad', (SCRIPT_KEY,), cache)
    words(cached, BODY_BYTES // 4)
    witnesses = tuple((0x39937d56 + probe_native.qbkey(name)) & 0xffffffff
                      for name in ('WStand', 'Idle', 'Ollie', 'BrakeIdle'))
    for key in witnesses:
        host.require(call('get_resource', (key, 0), context) == 0, 'Selected original animation was already loaded')
    script = call('SpawnScript', (SCRIPT_KEY,) + (0,) * 9)
    state = words(script, 63)
    host.require(state[0xc4 // 4] == 0 and state[0xd0 // 4] == SCRIPT_KEY and state[0xd4 // 4] == 1 and
                 cached <= state[4] < cached + BODY_BYTES, 'Original complete animation loader was not bound')
    log('animation_banks_begin', script_checksum=SCRIPT_KEY, whole_body_bytes=BODY_BYTES, stored_bytes=246,
        authored_heap_size=AUTHORED_SIZE, original_heap_capacity=ACTUAL_CAPACITY, original_heap=heap,
        source_sha256=SOURCE_SHA, host_script_source_writes=False, host_load_flags_written=False)
    host.require(call('ScriptUpdate', this=script) == 0 and word(script + 0x10) == 0 and word(0x6dcf84) == 0,
                 'Whole original animation loader did not complete in its single update')
    call('async_file_wait_all', (1,))
    call('ScriptDtor', this=script)
    call('ScriptFree', (script,))
    call('ScriptCacheReleaseReference', (SCRIPT_KEY,), cache)
    host.require(registry() == baseline and (word(manager + 0xc8), word(manager + 0x10c)) == heap_context,
                 'Whole animation loader did not restore the actual Script registry and heap context')
    # Original Change removes and reallocates a symbol (0x485CCD..0x485CEB).
    # Its source-buffer record is no longer authoritative after script execution.
    flags = flag_snapshot(lambda key:call('lookup', (key,)), read, source_pointer)
    witness_state = [{'key':key, 'resource':call('get_resource', (key, 0), context)} for key in witnesses]
    log('animation_banks_flags', flags=flags, witnesses=witness_state)
    host.require(all((row['header'] >> 16) & 0xff == 1 and row['name'] == row['key'] and
                     row['owner'] == probe_native.qbkey(SOURCE_NAME) for row in flags) and
                 [row['value'] for row in flags] == [0, 1, 1],
                 'Whole original animation loader did not publish its native standard-bank flags')
    verified = []
    for bank in inputs['banks']:
        for clip in bank['clips']:
            data = call('get_data', (clip['key'], 0), context)
            clip_words = words(data, 8)
            host.require(clip_words[0] == 0x6933b4 and read(clip_words[2], 20) == clip['header'],
                         'Whole original animation bank did not publish the expected native SKA')
        verified.append({key:bank[key] for key in ('path','bytes','sha256')})
        verified[-1]['native_clips_verified'] = len(bank['clips'])
    log('animation_banks_pass', whole_original_script=True, original_updates=1, native_clips_verified=1296,
        banks=verified, authored_heap_size=AUTHORED_SIZE, original_heap_capacity=ACTUAL_CAPACITY,
        load_bike=0, have_loaded_permanent=1, have_loaded_unloadable=1, script_registry_restored=True,
        original_parameter_fields_destroyed=True, host_script_source_writes=False,
        host_load_flags_written=False, replacement_animation_data=False, backend_ready=False)
