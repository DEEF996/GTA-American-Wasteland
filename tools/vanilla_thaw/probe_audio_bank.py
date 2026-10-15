"""Whole stock GlobalBankSetup with its original stream service; no playback.

Requires the existing native Scene engine configuration, core QB, FileService
and probe_audio stage1. Native buffers/workers live until bounded child exit.
Only private source caches are read; no replacement sample registration runs.
"""
import hashlib
import json
from pathlib import Path
import struct

import import_resources
import probe_audio
import probe_core
import probe_loader as host
import probe_profile
import probe_resources

BANK = 'data/sounds/pak/global_sfx.pak.wpc'
BANK_BYTES, BANK_SHA = 7591808, 'c589ebb2049056ffdc87d5ede096cc42e1bd85d2a0cdd422c674573f6388916f'
SOURCE_NAME = 'scripts/game/sounds/global_sfx_dat_wpc.qb.wpc'
SOURCE_BYTES, SOURCE_SHA = 10220, '48c02923dad2aba144c0d64584dd505888ac3f1dccde7d350a9bade8eb83b0bc'
COUNT, ARRAY_KEY, SIZE_KEY, SOURCE_KEY = 113, 0x31b7b0f0, 0xdae81d97, 0x6d13821f
OPTIONAL = tuple('data/streams/bik/' + name for name in ('bik.wad', 'music_bik.wad', 'bik.dat', 'music_bik.dat'))
CONFIGURATION = (60, 350, 3, 3, 0x447a0000, 40, 100)
PINS = {
    'StreamInit': ('OriginalBankStreamInitialize', 0x4b4f00, 400, 'acc6e5c68db345bae9fea5531e55ec711f1707dcdaaf7baad39bb69b29af4e01'),
    'StreamPlatform': ('OriginalBankStreamPlatform', 0x4b8180, 636, '7c4e4711c4b3473b1285e4e6f59d35d3b5bcb459595b3fed878d2395bbbac7b3'),
    'StreamWorker': ('OriginalBankStreamWorker', 0x4b80a0, 211, '5599dc5a951a971caf21f58d69d193e62025d6521c06109df6d1cc0f449745d0'),
    'StreamCaller': ('OriginalBankStreamCaller', 0x56e2bd, 18, 'ff45c32155d80632135fe59e303ac0eec8b59f34d9414842b9d81336e6113d44'),
    'Configuration': ('OriginalBankConfiguration', 0x4cf030, 5, 'ac2a6fc67fc3381f3cec9b4d802733dc69ff159bfdbf56d9d0592caaf1188a8f'),
    'ConfigurationBody': ('OriginalBankConfigurationBody', 0x5202f0, 6, '7c97349e39d83aa118aac9a9f733ddce9b7e2bc1bac2ff29321e11c42984430b'),
    'GlobalBank': ('OriginalGlobalBankSetup', 0x56d970, 673, '7042e21c4f1588ce8c66147969359ef7daa16e72d572c3bc073b22d6b3f35fd5'),
    'GlobalBankCaller': ('OriginalGlobalBankCaller', 0x56e2fa, 7, '514a07011daca78225ea4e513fef263abc002cb097119ff3aab6abfe48dd28d9'),
    'SampleRegister': ('OriginalBankSampleRegister', 0x440a40, 243, 'fc0d7c366292bc4bae7dbfd76b0a7eaee95841e5324759738e8eab072fb91de1'),
    'LoadDispatch': ('OriginalBankFileLoadDispatch', 0x4b61b0, 5, '35d2f334aabfa7c8d4f956f9e7c706af8f7956ae399430a38f49407c8dfdf322'),
    'WaitDispatch': ('OriginalBankWaitDispatch', 0x4b7850, 6, 'b190fd0e57364404fb7069221c1f5d2b69e40461d00125bab6c0c95a5655c80c'),
    'TickGetter': ('OriginalBankTickGetter', 0x62c3f0, 12, '7ddb9b18921d6e02e2ee8a5b13d8b580665f71a5c37a6c82619df6e3c718305e'),
    'NamedHeapLookup': probe_profile.PINS['NamedHeapLookup'],
    'NamedHeapCreate': probe_profile.PINS['NamedHeapCreate'],
    'HeapFreeBytes': probe_profile.PINS['HeapFreeBytes'],
    'AudioHeapCaller': ('OriginalAudioHeapStartup', 0x62746b, 30, '409889a8e97563cbe491839b29a92cf7bcb0b229b5b3654b7084e48abf307cc3'),
    'FileComplete': ('OriginalBankFileComplete', 0x628ab0, 82, '97d1b5457c0e2414846142fd2eed92ab5120d82e04b5ed030b9dd970dcdf54b3'),
    'FileIsBusy': ('OriginalBankFileIsBusy', 0x628680, 9, '6b72c7f94a5f66e9b0a87fe90dc0f3d4f5585a7d9e3f3eff1214c9d668506e8b'),
    'FileCompletionSwap': ('OriginalBankFileCompletionSwap', 0x629ea0, 21, 'f55b4737d996a0fd616447815e02c21ddd83b3d4e71a02460260756330dcb7fa'),
}


def parse_samples(source, bank, *, array_key=ARRAY_KEY, size_key=SIZE_KEY, source_key=SOURCE_KEY, count=None,
                  allow_alignment_padding=False):
    """Inspect authored scalar/flag fields; never construct native CStructs."""
    count = COUNT if count is None else count
    def words(at, count):
        host.require(at % 4 == 0 and 0 <= at <= len(source) - count * 4, 'Sound QB field escapes its whole source')
        return struct.unpack_from(f'<{count}I', source, at)
    host.require(words(4, 1) == (len(source),) and
                 1 <= count <= 256 and
                 words(28, 5) == (0xc0400, array_key, source_key, 48, 0) and
                 words(48, 3) == (0xa0100, count, 60) and
                 words(len(source) - 20, 5) == (0x10400, size_key, source_key, len(bank), 0),
                 'Original global sound array/size sections differ')
    samples, nodes, keys, end = [], set(), set(), 0
    allowed = {0xa6f5352f, 0x083fdb95, 0x21902065, 0xa80bea4a, 0xf6a36814, 0xff2020ec}
    for node in words(60, count):
        host.require(node not in nodes, 'Original sound array repeats a structure')
        nodes.add(node)
        marker, field = words(node, 2)
        host.require(marker == 0x10000, 'Original sound structure marker differs')
        values, flags, visited = {}, set(), set()
        while field:
            host.require(field not in visited and len(visited) < 12, 'Sound parameter chain cycles or exceeds its bound')
            visited.add(field)
            kind, key, value, field = words(field, 4)
            if kind == 0x1b00 and key == 0:
                host.require(value in (0x8b87176f, 0xc8d4b9e2, 0xcf15c120) and value not in flags,
                             'Unknown or repeated original sound flag')
                flags.add(value)
            else:
                host.require(kind == 0x300 and key in allowed and key not in values,
                             'Unknown or duplicate original sound scalar')
                values[key] = value
        host.require({0xa6f5352f, 0x083fdb95, 0x21902065} <= values.keys(), 'Sound source lacks offset, size or checksum')
        offset, size, key = (values[k] for k in (0xa6f5352f, 0x083fdb95, 0x21902065))
        frequency = values.get(0xa80bea4a, 22050)
        host.require(key not in keys and offset == (end + 127) & ~127 and offset % 128 == 0 and
                     size > 0 and size % 2 == 0 and offset <= len(bank) - 128 - size,
                     'Original sound span overlaps, escapes or has duplicate identity')
        keys.add(key)
        header = struct.unpack_from('<HHIIHHH', bank, offset)
        host.require(header == (1, 1, frequency, frequency * 2, 2, 16, 0), 'Original WAVEFORMATEX/PCM layout differs')
        volume, dropoff = values.get(0xf6a36814, 100), values.get(0xff2020ec, 0)
        host.require(volume <= 120 and dropoff <= 150, 'Original sound scalar is outside the audited range')
        bits = (1 if 0xcf15c120 in flags else 2 if 0x8b87176f in flags else 0) | (8 if 0xc8d4b9e2 in flags else 0)
        samples.append(dict(checksum=key, offset=offset, pcm_bytes=size, frequency=frequency, flags=bits,
                            pitch=100., volume=float(volume), dropoff=float(dropoff) * 12.))
        end = offset + 128 + size
    host.require(end == len(bank) or (allow_alignment_padding and len(bank) == (end+127)&~127 and
                                    not any(bank[end:])),
                 'Original sound bank has an unaccounted trailing span')
    return samples


def read_inputs(qb_manifest, resource_manifest, image_manifest):
    # Reuse the full existing original archive/member verifier, including its
    # original-file hashes; the private QB manifest alone is not provenance.
    core = probe_core.read_inputs(qb_manifest, image_manifest)
    sources = [r for r in core if r['name'] == SOURCE_NAME]
    host.require(len(sources) == 1 and (len(sources[0]['source']), sources[0]['sha256']) == (SOURCE_BYTES, SOURCE_SHA),
                 'Stock sound QB member differs')
    manifest = json.loads(import_resources.plain_path(resource_manifest).read_text(encoding='utf-8-sig'))
    image = json.loads(image_manifest.read_text(encoding='utf-8-sig'))
    directory, original = resource_manifest.resolve().parent, Path(image['source']).resolve()
    host.require(resource_manifest.is_absolute() and manifest.get('format') == import_resources.FORMAT and
                 manifest.get('sources_revalidated_before_publish') is True and manifest.get('original_files_modified') is False and
                 Path(manifest['directory']).resolve() == directory and Path(manifest['source']).resolve() == original and
                 not directory.is_relative_to(original), 'Sound bank must use the selected separate private resource cache')
    rows = [r for r in manifest['files'] if r['path'].casefold() == BANK]
    host.require(len(rows) == 1 and (rows[0]['bytes'], rows[0]['sha256']) == (BANK_BYTES, BANK_SHA), 'Whole sound bank pin differs')
    path = import_resources.plain_path(directory / BANK)
    source_path = import_resources.plain_path(original / BANK)
    host.require(not path.samefile(source_path), 'Private sound bank aliases the original')
    bank = path.read_bytes()
    host.require((len(bank), hashlib.sha256(bank).hexdigest()) == (BANK_BYTES, BANK_SHA) and bank == source_path.read_bytes(),
                 'Whole private sound bank changed or differs from original')
    host.require(all(not import_resources.plain_path(directory / name, missing=True).exists() for name in OPTIONAL),
                 'Bounded loose-bank proof requires absent optional BIK archives')
    source = sources[0]['source']
    import terrain_audio
    extra = terrain_audio.ensure(original, directory.parent/'stock-terrain-audio')
    return dict(source=source, bank=bank, samples=parse_samples(source, bank), directory=directory,
                source_path=source_path, path=path, terrain_audio=extra)


def stream_idle(system):
    host.require(system.word(0x6c05a0) == 0xffffffff and system.word(0x6fe854) == system.word(0x6fe858),
                 'Original bank stream job or queue is not idle')


def files_idle(system, log):
    """Check pending work, not the original completion notification latch.

    FileComplete sets byte 844448 to one even after decrementing the last
    outstanding request. WaitAll returns immediately when 844444 is zero,
    so that latch may remain set. Its legacy metadata name `file_pending_count`
    is misleading. Original WaitAll/IsBusy inspect each handler's +3C instead.
    """
    start, end = system.word(0x84376c), system.word(0x843770)
    handlers = struct.unpack('<16I', system.read(0x844400, 64))
    host.require(len(set(handlers)) == 16 and all(p % 4 == 0 and start <= p <= end - 0xa4 for p in handlers),
                 'Original bank FileService handler ownership differs')
    pending = [system.word(p + 0x3c) for p in handlers]
    active, opened = system.word(0x844444), system.word(0x844440)
    completion = system.read(0x844448, 1)[0]
    callbacks = struct.unpack('<2I', system.read(0x84444c, 8))
    log('audio_bank_file_service', outstanding_requests=active, handler_outstanding_requests=pending,
        open_handler_count=opened, completion_notification=completion, queued_callbacks=list(callbacks))
    host.require(active == 0 and all(n == 0 for n in pending) and callbacks == (0, 0) and
                 0 <= opened <= 16 and completion in (0, 1), 'Original bank FileService remains busy or invalid')


def initialize_audio_heap(system, call, log):
    """Use the complete existing factory with its exact original startup args."""
    read, word, manager = system.read, system.word, 0x842b18
    start, end = word(0x84376c), word(0x843770)
    host.require(word(0x84388c) == manager and read(0x6a22d8, 6) == b'Audio\0' and
                 call('NamedHeapLookup', (0x6153ca47, 0), manager) == 0,
                 'AudioHeap startup requires the original allocator/name and an absent named heap')
    count = word(manager + 0xc0)
    slots = read(manager + 0x18c, 32 * 0x50)
    unused = [i for i in range(32) if slots[i * 0x50 + 0x4c] == 0]
    host.require(count < 48 and unused, 'Original AudioHeap registry has no free slot')
    index = unused[0]
    heap_array = read(manager, count * 4)
    depth, current = word(manager + 0x10c), word(manager + 0xc8)
    host.require(depth < 16 and current == (manager + 0xc8 + depth * 4 if depth else 0), 'Original AudioHeap context is invalid')
    stack = read(manager + 0xc8, (depth + 1) * 4)
    heap = call('NamedHeapCreate', (0x6153ca47, 0x1000000, 0x6a22d8, 0), manager)
    host.require(start <= heap <= end - 0x98 and heap % 4 == 0 and
                 call('NamedHeapLookup', (0x6153ca47, 0), manager) == heap,
                 'Original AudioHeap factory did not publish its own heap')
    after = read(manager + 0x18c, 32 * 0x50)
    slot = after[index * 0x50:(index + 1) * 0x50]
    arena, registered_heap, key = struct.unpack_from('<3I', slot)
    host.require(registered_heap == heap and key == 0x6153ca47 and slot[12:18] == b'Audio\0' and slot[0x4c] == 1 and
                 all(after[i * 0x50:(i + 1) * 0x50] == slots[i * 0x50:(i + 1) * 0x50] for i in range(32) if i != index) and
                 word(manager + 0xc0) == count + 1 and read(manager, count * 4) == heap_array and word(manager + count * 4) == heap and
                 (read(manager + 0xc8, (depth + 1) * 4), word(manager + 0x10c)) == (stack, depth),
                 'Original AudioHeap creation changed existing heap membership/context or published another name')
    host.require(start <= arena <= end - 32 and arena % 4 == 0 and word(heap + 0x24) == arena,
                 'Original AudioHeap arena descriptor is invalid')
    fields = struct.unpack('<8I', read(arena, 32))
    host.require(fields[0] == 0x6a2354 and fields[3] == 0x1000000 and fields[5] - fields[1] == 0x1000000 and
                 start <= fields[1] < fields[5] <= end and call('HeapFreeBytes', this=heap) == 0x1000000,
                 'Original AudioHeap does not retain its authored 16MiB capacity')
    log('audio_heap_pass', original_heap=heap, original_arena=arena, original_capacity=0x1000000,
        original_factory=0x626dc0, original_caller=0x62746b, new_named_slot=index,
        existing_heap_slots_preserved=True, host_heap_field_writes=False)
    return heap


def initialize_stream(system, call, log):
    read, word = system.read, system.word
    config = call('Configuration')
    host.require(config == 0x7740e0 and struct.unpack('<7I', read(config, 28)) == CONFIGURATION,
                 'Original bank stream configuration differs from native Scene setup')
    host.require(word(probe_audio.MUTEX) and word(probe_audio.MANAGER) and word(probe_audio.DEVICE) and
                 read(0x6c0584, 2) == b'\1\1' and word(0x844230) == 7 and read(0x8459c8, 8) == bytes(8),
                 'Original bank stream requires stage1 audio and the idle original startup clock')
    stream_idle(system)
    host.require(all(word(p) == 0 for p in (0x6fa5f0, 0x6fe7f8, 0x764de4, 0x6fe854, 0x6fe858)) and
                 read(0x764de8, 1) == b'\0' and read(0x700d50, 24) == bytes(24) and
                 read(0x714d68, 96) == bytes(96) and read(0x764dd4, 16) == bytes(16) and word(0x6c05a4) == 0xffffffff,
                 'Original stream service is not cold or contains movie/music resources')
    call('StreamInit', (word(config + 8), word(config + 12)))
    thread = word(0x764de4)
    status = system.allocate(bytes(4))
    host.require(thread != 0 and system.call('kernel32.dll', 'GetExitCodeThread', (thread, status)) != 0 and word(status) == 259,
                 'Original bank stream worker did not remain alive')
    host.require(word(0x6fa5f0) == word(0x6fe7f8) == 3 and read(config, 28) == struct.pack('<7I', *CONFIGURATION) and
                 read(0x764de8, 1) == b'\0' and read(0x700d50, 24) == bytes(24) and
                 read(0x714d68, 96) == bytes(96) and read(0x764dd4, 16) == bytes(16) and word(0x6c05a4) == 0xffffffff,
                 'Original stream configuration, empty wrappers or missing-archive branches differ')
    stream_idle(system)
    log('audio_bank_stream_pass', original_slots=3, original_reserved_slots=3, original_worker_handle=thread,
        native_worker_alive=True, original_clock_advanced=False, optional_BIK_archives_loaded=False)


def validate_table(system, samples, bank, arena_start, arena_end):
    """Validate actual source bindings; no descriptor layout is manufactured."""
    def bounded(pointer, size):
        host.require(pointer % 4 == 0 and arena_start <= pointer <= arena_end - size, 'Native sound pointer escapes original arena')
        return system.read(pointer, size)
    host.require(system.word(probe_audio.SAMPLES) == len(samples) == COUNT, 'Whole native sound sample census differs')
    raw = system.read(0x6e0f90, COUNT * 32)
    base, descriptors = None, set()
    for index, sample in enumerate(samples):
        row = struct.unpack_from('<8I', raw, index * 32)
        expected_float = struct.unpack('<3I', struct.pack('<3f', sample['pitch'], sample['volume'], sample['dropoff']))
        host.require(row[0] == sample['checksum'] and row[1] == sample['flags'] | (3 << 16) and row[2:5] == expected_float,
                     'Native sound record changed original checksum/flags/pitch/volume/dropoff')
        candidate = row[7] - sample['offset']
        host.require(candidate % 128 == 0 and (base is None or candidate == base), 'Native sound sources do not share the original aligned whole bank')
        base = candidate
        host.require(row[5] not in descriptors, 'Native sound records share an unexpected descriptor')
        descriptors.add(row[5])
        desc = struct.unpack('<18I', bounded(row[5], 0x48))
        host.require(desc[7] == desc[14] == sample['pcm_bytes'] and desc[9] == row[7] and desc[15] == 0 and desc[16] == row[7] + 128,
                     'Native sample descriptor did not retain its original header/PCM span')
    host.require(bounded(base, len(bank)) == bank, 'Native full sound bank bytes differ from the pinned original')
    return base


def run(api, process, record, deadline, log, inputs, preloaded, system):
    source, bank = inputs['source'], inputs['bank']
    host.require((len(source), hashlib.sha256(source).hexdigest()) == (SOURCE_BYTES, SOURCE_SHA) and
                 (len(bank), hashlib.sha256(bank).hexdigest()) == (BANK_BYTES, BANK_SHA), 'Original sound inputs changed after validation')
    samples = parse_samples(source, bank)
    host.require(import_resources.plain_path(inputs['path']).read_bytes() == bank == import_resources.plain_path(inputs['source_path']).read_bytes() and
                 all(not import_resources.plain_path(inputs['directory'] / name, missing=True).exists() for name in OPTIONAL),
                 'Original/private sound files or optional archive state changed before native access')
    metadata = probe_resources.read_metadata()
    pins = {**PINS, 'lookup': host.QB_PINS['lookup'],
            **{row[0]: row for row in metadata['functions'] if row[0] == 'async_file_wait_all'}}
    for name, address, size, digest in pins.values():
        host.require(hashlib.sha256(system.read(address, size)).hexdigest() == digest, 'Original bank entry differs: ' + name)
    def call(name, args=(), this=None):
        return host.native_call(api, process, pins[name], list(args), record, deadline, log, this)
    read, word = system.read, system.word
    start, end = word(0x84376c), word(0x843770)
    host.require(0x10000 <= start < end <= 0xffffffff and word(probe_audio.SAMPLES) == 0 and word(probe_audio.REFERENCES) == 1,
                 'Original bank requires an existing arena and empty original SoundManager')
    pointer = probe_core.reuse(api, process, preloaded, SOURCE_NAME, source)
    for offset, kind, key, value in ((28, 0xc0400, ARRAY_KEY, pointer + 48), (SOURCE_BYTES - 20, 0x10400, SIZE_KEY, BANK_BYTES)):
        host.require(call('lookup', (key,)) == pointer + offset and
                     struct.unpack('<4I', read(pointer + offset, 16)) == (kind, key, probe_core.import_qb.qbkey(SOURCE_NAME), value),
                     'Native sound symbols are not members of the whole original core source')
    cwd = system.allocate(bytes(2048))
    length = system.call('kernel32.dll', 'GetCurrentDirectoryW', (1024, cwd))
    host.require(0 < length < 1024 and Path(read(cwd, length * 2).decode('utf-16le')).resolve() == inputs['directory'],
                 'Original bank file access leaves its verified private working directory')
    manager = 0x842b18
    depth, current = word(manager + 0x10c), word(manager + 0xc8)
    host.require(depth < 16 and current == (manager + 0xc8 + depth * 4 if depth else 0), 'Original audio heap context differs')
    heap_context = read(manager + 0xc8, (depth + 1) * 4), depth
    heap = initialize_audio_heap(system, call, log)
    preserved = {address: read(address, size) for address, size in (*probe_audio.PRESERVED, (0x7740e0, 28))}
    host.require(word(0x6dcf84) == 0 and read(0x6dcf98, 2) == bytes(2) and read(0x6dd4a4, 1) == b'\0', 'Bank load requires idle original owner phases')
    callbacks = probe_audio.callbacks(system)
    initialize_stream(system, call, log)
    log('audio_bank_begin', source_sha256=SOURCE_SHA, bank_sha256=BANK_SHA, bank_bytes=BANK_BYTES,
        expected_samples=COUNT, whole_original_bank_setup=True, original_audio_heap=heap)
    call('GlobalBank')
    call('async_file_wait_all', (1,))
    stream_idle(system)
    files_idle(system, log)
    base = validate_table(system, samples, bank, start, end)
    host.require((read(manager + 0xc8, (depth + 1) * 4), word(manager + 0x10c)) == heap_context and
                 probe_audio.callbacks(system) == callbacks and all(read(address, len(value)) == value for address, value in preserved.items()) and
                 import_resources.plain_path(inputs['path']).read_bytes() == bank == import_resources.plain_path(inputs['source_path']).read_bytes(),
                 'Original bank load changed existing ownership, heap stack or source files')
    log('audio_bank_pass', original_samples=COUNT, looping_samples=sum(bool(s['flags'] & 8) for s in samples),
        whole_bank_sha256=hashlib.sha256(read(base, BANK_BYTES)).hexdigest(), bank_pointer=base,
        original_GlobalBankSetup=True, native_source_fields_verified=True, source_bytes_identical=True,
        host_sample_registration_calls=0, playback_invoked=False, lifetime='retained until bounded child termination',
        reusable_stream_worker_stop_join_verified=False, all_gameplay_SFX_proven=False, backend_ready=False)
    return dict(bank_pointer=base, samples=COUNT)
