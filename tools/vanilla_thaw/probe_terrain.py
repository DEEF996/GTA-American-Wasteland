"""Publish the complete stock terrain table into its original Game-owned maps."""
import hashlib
import json
from pathlib import Path
import struct

import probe_core
import probe_loader as host
from carentan_qb import node_fields

SOURCE = 'scripts/game/terrain.qb.wpc'
SOURCE_SHA = 'dfc4310c1d04bde8c78a03ab938a805496e752ef7667769cf15c74bfd1e75adb'
ARRAYS = ((0x533ad720, 28, 48), (0x828087b8, 3436, 3456))
MAPS = (0x6e81f0, 0x6e81d4)
PINS = {
    'publish': ('OriginalTerrainPublisherDispatch', 0x479bb0, 6, '2a97f294d9ff11a53181d49f2d9d3fac5d00951937cf84110dcbb17f3399b57a'),
    'caller': ('OriginalTerrainBeforeLightingCaller', 0x56e240, 25, 'd0b847a9fb34ca2ab24f353d819dfb2a05e9aea1252342dc0c4938a8e6d4d4bd'),
    'map_crt': ('OriginalTerrainMapIdentity', 0x688cc0, 26, '32424b429d7f8779b79856d6bfe596214f9328c5ae2db961bf7244f7b3f5250f'),
    'custom_crt': ('OriginalCustomTerrainMapIdentity', 0x688ce0, 26, '28e62e5ab82c2afe6dd1a056ff0f9051b6ecd515b0f2221b84c517d0b337afc1'),
    'lookup': ('OriginalTerrainHashLookup', 0x433a60, 72, 'ad6d761176b5488c13ec5a70c350f7252f98d6b973449c1fe26afc80ece94b6e'),
}


def source_names(source):
    host.require(len(source) == 15736 and hashlib.sha256(source).hexdigest() == SOURCE_SHA,
                 'Whole stock terrain source differs')
    for key, section, payload in ARRAYS:
        header = struct.unpack_from('<5I', source, section)
        host.require(header[:2] == (0xc0400, key) and header[3:] == (payload, 0),
                     'Original terrain array section differs')
    host.require(struct.unpack_from('<3I', source, 48) == (0xa0100, 60, 60) and
                 struct.unpack_from('<3I', source, 3456) == (0x100, 0, 0), 'Original terrain array shape differs')
    fields = [node_fields(source, p)[0x21902065] for p in struct.unpack_from('<60I', source, 60)]
    host.require(all(kind == 0x1b00 for kind, _ in fields), 'Original terrain names are not checksums')
    names = [name for _, name in fields]
    host.require(len(set(names)) == 60, 'Original terrain names are not unique')
    return names


def read_inputs(qb_manifest, image_manifest):
    manifest = json.loads(qb_manifest.read_text(encoding='utf-8-sig'))
    image = json.loads(image_manifest.read_text(encoding='utf-8-sig'))
    directory = qb_manifest.resolve().parent
    original = Path(image['source']).resolve()
    host.require(manifest.get('format') == 'stock-thaw-core-qb-cache-v1' and
                 manifest.get('sources_revalidated_before_publish') is True and
                 Path(manifest['source_directory']).resolve() == original and not directory.is_relative_to(original),
                 'Terrain requires the selected separate stock QB cache')
    rows = [row for row in manifest['members'] if row['order'] == 21]
    host.require(len(rows) == 1 and (rows[0]['canonical_name'], rows[0]['bytes'], rows[0]['sha256']) ==
                 (SOURCE, 15736, SOURCE_SHA), 'Original terrain source manifest differs')
    path = (directory / rows[0]['cache_file']).resolve()
    host.require(path.is_relative_to(directory), 'Terrain source escapes private cache')
    source = path.read_bytes()
    return source, source_names(source)


def run(api, process, record, deadline, log, inputs, preloaded, system, game_context):
    source, names = inputs
    host.require(source_names(source) == names, 'Terrain inputs changed after preflight')
    read, word = system.read, system.word
    start, end = word(0x84376c), word(0x843770)

    def words(address, count):
        host.require(address % 4 == 0 and start <= address <= end - count * 4,
                     'Original terrain object leaves native arena')
        return struct.unpack(f'<{count}I', read(address, count * 4))

    for name, address, size, digest in PINS.values():
        host.require(hashlib.sha256(read(address, size)).hexdigest() == digest, 'Original terrain pin differs: ' + name)
    host.require(all(word(a) == 0 for a in (0x6dcf84, 0x6dcf8c, 0x6dcf90)) and
                 read(0x6dcf98, 2) == bytes(2) and read(0x6dd4a4, 1) == b'\0',
                 'Original terrain startup requires idle VM before actor scripts')
    states = [struct.unpack('<7I', read(a, 28)) for a in MAPS]
    host.require(all(state[0] == 8 and state[2] == 0 and not any(words(state[1], 256 * 3)) for state in states),
                 'Terrain requires already-constructed empty Game-owned maps')
    pointer = probe_core.reuse(api, process, preloaded, SOURCE, source)
    headers = []
    for key, section, payload in ARRAYS:
        symbol = host.native_call(api, process, host.QB_PINS['lookup'], [key], record, deadline, log)
        host.require(symbol == pointer + section and word(symbol + 12) == pointer + payload,
                     'Terrain source ownership changed')
        headers.append(read(pointer + payload, 12))
    preserved = {address: read(address, size) for address, size in
                 ((game_context['game'], 0x9c), (0x842be0, 72), (0x8505ec, 8), (0x6dcf84, 16))}
    log('terrain_startup_begin', original_map_counts=[state[2] for state in states], source_names=len(names))
    host.native_call(api, process, PINS['publish'], [], record, deadline, log)
    host.require([word(a + 8) for a in MAPS] == [60, 0] and
                 all(word(a) == state[0] and word(a + 4) == state[1] for a, state in zip(MAPS, states)),
                 'Original terrain publisher changed map ownership or counts')
    scratch = system.allocate(bytes(4))
    seen = set()
    for index, key in enumerate(names):
        host.remote_write(api, process, scratch, struct.pack('<I', key))
        value = host.native_call(api, process, PINS['lookup'], [scratch, 0], record, deadline, log, MAPS[0])
        native = words(value, 2)
        host.require(value not in seen and native[0] & 0xff == index and native[1] == key,
                     'Original terrain name/index differs from complete authored table')
        seen.add(value)
    host.require(all(read(a, len(v)) == v for a, v in preserved.items()) and
                 all(read(pointer + payload, 12) == before for (_, _, payload), before in zip(ARRAYS, headers)),
                 'Terrain startup changed Game, heap, CRT, VM or source ownership')
    log('terrain_startup_pass', terrain_names=60, custom_terrain_names=0, original_whole_publisher=True,
        original_game_maps_reused=True, host_terrain_fields_written=False, replacement_callbacks=False,
        source_sha256=SOURCE_SHA, sound_playback_verified=False, backend_ready=False)
