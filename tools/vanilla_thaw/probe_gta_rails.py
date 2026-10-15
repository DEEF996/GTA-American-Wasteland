"""Initial original stock NodeArray/resource/parser proof, between updates only.

Only owned parameter structs are written. Native node/rail objects are created
by the real stock loader/parser and retained until the parent's child exits.
No gameplay update, unload, rail replacement or grind capture is asserted.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

import probe_loader as host
import probe_handoff
import probe_gta_scene
import probe_vm_idle
import probe_profile
from assets_export import pak_member, parse_col
from carentan_qb import read_member, node_fields
from native_worker import STATE, TID, COUNT, IDLE

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gta'))
import rail_export

# Full stock functions/metadata; protected dispatcher spans retain their original bytes.
PINS = {
    'NodeManagerAcquire': ('NodeManagerAcquire', 0x588db0, 77, '8a1b3b78ffaf3b2eef569312057e5bdd07a4586678496a866e0b0e86a994a576'),
    'NodeManagerCtor': ('NodeManagerCtor', 0x588b10, 167, 'ef4698f0e56db2248bac45c4950bcad09b3b17986c47efbc29381e11adf4d1cb'),
    'NodeManagerRegisterArray': ('NodeManagerRegisterArray', 0x588bd0, 437, '22f78ea6f5b911f96a7caea24d060188b5a70f8a44e1285702d17302763c60bb'),
    'NodeManagerLinkNames': ('NodeManagerLinkNames', 0x588770, 151, 'a6dc1c089da69ac71b740d775b2511b1c7b6fe7e63be0e9d8109ea711a613364'),
    'NodeManagerIndices': ('NodeManagerIndices', 0x5889f0, 282, '05d9b287dd93062574458f4f734defefffcc8b517203fb4c11939d4c460a675a'),
    'NodeManagerArrayName': ('NodeManagerArrayName', 0x588980, 91, '5ab9dfa7bb7d0a543f3d13cab059723ea35715234298081b3a5f7005d9c2b764'),
    'NodeArrayManLoad': ('NodeArrayManLoad', 0x59b3d0, 76, '3b07670faf5e17240d92e14d9752c8124859367784e09b7ca6a076022b77d948'),
    'ParseNodeArray': ('ParseNodeArray', 0x59e810, 664, 'd8477825d54f1e26ba232d7820793182a62061eb8f417a75c3f3984ae289c795'),
    'NativeQbResourceLoad': ('NativeQbResourceLoad', 0x41ba10, 195, '59722ef383581ba049e1990b9ed6a298da34b7d5886094dac6d8ac6ed08171cb'),
    'NativeFileResourceLoad': ('NativeFileResourceLoad', 0x41b020, 307, 'c2c4f796a99baac4158bee5ddd27caedd9d7978836a240551957303f97288bc6'),
    'RailManagerAllocate': ('RailManagerAllocate', 0x58a190, 104, 'e1d5346bb9677a702f3a24861ea4d2ac04599f63f6af85d6461f2ca25a18a639'),
    'RailNodeLoad': ('RailNodeLoad', 0x58b130, 597, '6a8d19e6c4eb5fb2f7748568bdd0d32f297f80e520b9950c0e04a017fb13f02b'),
    'RailNodeConnect': ('RailNodeConnect', 0x58ad70, 212, '6ddc82d1c2bb709ac551e7ae8f60ad8e6b1b95d33c5273f0e64821016bb6f45f'),
    'RailNodeSwap': ('RailNodeSwap', 0x58aed0, 127, 'eba87452e6c76eed09ac9d317f53817885d6daa93dd53179f69f6608cdc1acf1'),
    'NativeCStructCtor': ('NativeCStructCtor', 0x414230, 11, '219b92061b08d719b045423de28a365462f20df22e2ca28f240d235c37052a90'),
    'NativeAddChecksum': ('NativeAddChecksum', 0x415e90, 114, '6bb987885e7275c71b88d5645baaf83d9afdb9e2481e560d201cc71fa2cbad8e'),
    'NativeGetSkater': ('NativeGetSkater', 0x4a04e0, 158, 'd2a0c2c1b933b95db35fa85f0be8da337afb571e6fb4cf6897715163bb90c858'),
    'WholeResourceFactory': ('WholeResourceFactory', 0x41a670, 1140, '8c23cb3ee32f9391fa29246729732699556bdf9c2740fc9dcfebbf426b0552a0'),
    'WholeNodeParser': ('WholeNodeParser', 0x59e100, 1780, '9b4f4ca243d5fe68eea666e26756bdc80ad6e1988b520cb7d35af2711125c605'),
    'RailManagerBuild': ('RailManagerBuild', 0x58d8d0, 1594, '688b613418df8751024c68b8d76f7d2e5544a6cd909637180d2aae8dbaf4e834'),
    # 58B2D2 gets TerrainType, 4799A0 resolves its byte index, 58B2FE stores node+48.
    'RailNodeInitialize': ('RailNodeInitialize', 0x58b130, 597, '6a8d19e6c4eb5fb2f7748568bdd0d32f297f80e520b9950c0e04a017fb13f02b'),
    'NativeAddString': ('NativeAddString', 0x415c00, 127, '77224fbf9743d0bb35090abfef805a3705edc4e6731e33e1a5279022ffd47445'),
    'AddStringProtectedEntry': ('AddStringProtectedEntry', 0x4cac16, 21, '0f3581354dbd4811cebd9650c3d5254cd09891e9cfbd1beec9713b3aa7452c7c'),
    'GetString': ('GetString', 0x414580, 89, '61870b9c87471fd59a4282fe20e64193c5e9bebfe712567dabc8b8df9c455eb3'),
    'NodeArrayFindNamed': ('NodeArrayFindNamed', 0x5a50e0, 17, '8b3702a9bcfcd1328bea811512f28b81204512a81f4e44a969ce77b1699a8889'),
    'QbResourceCtorEntry': ('QbResourceCtorEntry', 0x41b9d0, 6, '161a223fe02ac72d71919054a88872e78f0d7badee568879b1f71004343071d1'),
    'QbResourceNodeFlagEntry': ('QbResourceNodeFlagEntry', 0x41b9f0, 6, '2a36288ce210da89070b0c8ee31028685c1dc9f3fc578c87772f88c395629922'),
    'QbResourceVtable': ('QbResourceVtable', 0x68f060, 76, '3bdda69a0aeef69c3562c416d66eaa7a0f953f2347d2b432a0ced6fe2b37a456'),
}
PINS.update({key: probe_handoff.PINS[key] for key in ('StructClear', 'StructDtor')})
PINS['lookup'] = host.QB_PINS['lookup']
PINS['SceneNodeTables'] = ('OriginalSceneNodeTables', 0x6936a4, 8, '4bdc1c77acf917ab11b649b86c8cad55d27082414142018f976bf554f5c80bdf')
PINS['SceneNodeUnlink'] = ('OriginalSceneNodeUnlink', 0x4cfcc0, 31, 'e289e0b0f117c8b91b4b72562c7aefb09d7d839272cf342765cb33a32468db0e')
PINS['LoadCOLMemory'] = probe_gta_scene.PINS['load_col']
PINS['ArrayPointer'] = probe_profile.PINS['ArrayPointer']
PINS['GetChecksum'] = probe_profile.PINS['GetChecksum']
PINS['GetVector'] = probe_handoff.PINS['GetVector']
PINS['ConfigLoad'] = host.QB_PINS['load']
GLOBAL_COL_SHA = 'bb53331a9bd979699327ff320bf6dab734ef2ba579d1eeafe929e9e1b90199df'
GLOBAL_ARRAY = rail_export.qbkey('Global_NodeArray')
GLOBAL_QB_SHA = 'f3cfc533bda4ea7091df7cdafde5815318f3fa53793b0a1d20357da9ac90f062'
RAIL_FORMAT_KEY = 0x725bce21
CONFIG_NAME = 'gta_thaw/dynamic_rail_config.qb.wpc'


def dynamic_rail_config():
    """Authored format-selection scalar, consumed by the original QB loader.

    Stock startup member23 defines this key as1. RailManagerBuild58D8D0
    skips NodeArray rails for that setting; the0 branch constructs them.
    The textual checksum name remains unidentified; this changes no physics.
    """
    return (struct.pack('<2I', 0, 48) + rail_export.HEADER +
            struct.pack('<5I', 0x10400, RAIL_FORMAT_KEY, rail_export.qbkey(CONFIG_NAME), 0, 0))


def read_global_data(directory):
    """Private source identity, prepared before the native deadline starts."""
    folder = directory/'data/worlds/Global'
    chunks = []
    for name, size, digest in (
            ('global.pak.wpc', 10496, 'e78e4147a73a59c32accce5de32fa20d5ea32f3860c361010920a30d6d13923a'),
            ('global.pab.wpc', 2672672, 'cefddab597f85f1a7353a2ae0adbf179f26568d091f373bd4c2ed359a6612cc8')):
        path = folder/name
        host.require(path.is_file() and path.stat().st_size == size, 'Global archive size differs')
        data = path.read_bytes()
        host.require(hashlib.sha256(data).hexdigest() == digest, 'Global archive identity differs')
        chunks.append(data)
    archive = b''.join(chunks)
    col, _ = pak_member(archive, 'worlds/global/global.col')
    host.require(len(col) == 332 and hashlib.sha256(col).hexdigest() == GLOBAL_COL_SHA,
                 'Global COL source identity differs')
    parse_col(col)
    source = read_member(archive, 'worlds/global/global.qb')
    host.require(len(source) == 696 and hashlib.sha256(source).hexdigest() == GLOBAL_QB_SHA and
                 struct.unpack_from('<I', source, 32)[0] == GLOBAL_ARRAY and
                 struct.unpack_from('<3I', source, 48) == (0xa0100, 2, 60), 'Original Global NodeArray source differs')
    nodes = []
    for index in range(2):
        fields = node_fields(source, struct.unpack_from('<I', source, 60+index*4)[0])
        host.require(all(kind in (0xd00, 0x1b00) for kind, _ in fields.values()) and
                     fields[rail_export.qbkey('Class')] != (0x1b00, rail_export.qbkey('RailNode')),
                     'Global source contains an unexpected field or rail node')
        nodes.append({key: (kind >> 9, source[value+4:value+16] if kind == 0xd00 else value)
                      for key, (kind, value) in fields.items()})
    return dict(col=bytes(col), node_source=source, nodes=nodes)


def verify_global_nodes(array, expected, words, read, call, scratch):
    """All authored fields through original native getters, with bounded key census."""
    host.require(words(array, 3)[:2] == (0xa0100, len(expected)), 'Global native array count/type differs')
    for index, reference in enumerate(expected):
        node = call('ArrayPointer', (index,), array)
        pointer = words(node, 2)[1]
        keys, seen = {}, set()
        while pointer:
            host.require(pointer not in seen and len(seen) < len(reference), 'Global native node field chain cycles/grew')
            seen.add(pointer)
            flags, key, value, pointer = words(pointer, 4)
            host.require(key not in keys, 'Global native node duplicates a field')
            keys[key] = flags >> 9 & 0x7f
        host.require(keys == {key: value[0] for key, value in reference.items()}, 'Global native node field set differs')
        for key, (kind, value) in reference.items():
            getter = 'GetVector' if kind == 6 else 'GetChecksum'
            host.require(call(getter, (key, scratch, 0), node) & 255, 'Global native getter failed')
            actual = read(scratch, 12) if kind == 6 else struct.unpack('<I', read(scratch, 4))[0]
            host.require(actual == value, 'Global native node value differs from original source')


def relocated_global_col(source, base):
    """One original COL object and one BSP leaf; no ignored mutable ranges.

    Whole LoadCOL 4E3BB0 -> 4E2CF0 passes the original pool bases to
    protected42DF80 (old42DE20); its leaf fixup rebases the index pointer.
    Every other byte must remain identical, including flags/materials.
    """
    host.require(len(source) == 332 and 0x10000 <= base <= 0xfffff000-len(source) and base % 16 == 0,
                 'Global COL buffer address/size differs')
    host.require(struct.unpack_from('<8I', source) == (10, 1, 8, 0, 12, 0, 8, 0) and
                 struct.unpack_from('<IHHHBBI', source, 80)[1:] == (0, 8, 12, 1, 1, 0) and
                 struct.unpack_from('<3I', source, 128) == (0, 0, 0) and
                 struct.unpack_from('<3I', source, 296) == (8, 0xc0003, 0),
                 'Global COL packing or BSP differs')
    expected = bytearray(source)
    for at, relative in ((92, 200), (128, 144), (132, 300), (136, 192), (304, 308)):
        struct.pack_into('<I', expected, at, base+relative)
    return bytes(expected)


def verify_global_col(contents, source, base):
    host.require(contents == relocated_global_col(source, base),
                 'Extra scene is not the complete original Global COL with exact native relocation')


def scene_chain(read, owned, log):
    """Bounded original intrusive list; discover identity before authorizing detach."""
    world = probe_gta_scene.WORLD
    sentinel = struct.unpack('<5I', read(world, 20))
    host.require(sentinel[:3] == (probe_gta_scene.LIST_VTABLE, 0xffffffff, 0), 'Scene sentinel differs')
    entries, pointer, previous = [], sentinel[3], world
    while pointer != world:
        host.require(len(entries) < 32 and pointer not in {entry['node'] for entry in entries} and
                     pointer % 4 == 0 and 0x10000 <= pointer < 0xfffff000, 'Scene list escapes x86 space/cycles')
        row = struct.unpack('<5I', read(pointer, 20))
        log('gta_rails_scene_chain_node', node=pointer, words=row, expected_previous=previous)
        host.require(row[0] in (0x6936a4, probe_gta_scene.LIST_VTABLE) and row[2] == 0 and row[4] == previous and
                     row[1] % 4 == 0 and 0x10000 <= row[1] < 0xfffff000, 'Scene backlink or address differs')
        contents = read(row[1], 0xa0)
        host.require(struct.unpack_from('<I', contents)[0] == 0x69520c, 'Scene is not an original PCScene')
        entries.append(dict(node=pointer, words=row, scene_words=struct.unpack('<40I', contents)))
        previous, pointer = pointer, row[3]
    host.require(sentinel[4] == previous and sum(row['node'] == owned['node'] and row['words'][1] == owned['scene']
                                               for row in entries) == 1, 'Scene tail/owned GTA membership differs')
    log('gta_rails_scene_chain', entries=entries)
    return entries


def prepare_inputs(path, report, scene_report, resource_directory):
    """Read/validate an already published private authored file before child start."""
    host.require(path.is_absolute() and resource_directory.is_absolute() and path.is_file(), 'Rails input paths must be absolute')
    path, resource_directory = path.resolve(), resource_directory.resolve()
    host.require(path.name == 'gta_rails.qb.wpc' and path.stat().st_size <= 2*1024*1024,
                 'Rails input must be the bounded authored basename')
    source = path.read_bytes()
    expected, canonical = rail_export.build_rails(report['candidate_provenance'], scene_report)
    host.require(source == expected and report == canonical, 'Rails payload/report/source provenance differs')
    native = os.path.relpath(path, resource_directory / 'data').replace('\\', '/')[:-4]
    host.require(len(native.encode('ascii')) <= 120 and '/' in native and '\0' not in native and ':' not in native,
                 'Native data-prefix filename must be bounded, relative and on the private resource drive')
    host.require((resource_directory / 'data' / (native+'.wpc')).resolve() == path, 'Native data prefix resolves to another rail file')
    return dict(path=path, directory=resource_directory, source=source, native_filename=native,
                global_data=read_global_data(resource_directory),
                report=canonical, scene_report=scene_report)


def validate_graph(contents, base, expected):
    """Check every native name/float bit/link; never accept an escaping pointer."""
    host.require(len(contents) == len(expected)*0x60 and len(expected) >= 2, 'Native rail graph size differs')
    actual, flags = {}, {}
    for offset in range(0, len(contents), 0x60):
        row = struct.unpack_from('<24I', contents, offset)
        name = row[0x44//4]
        host.require(name not in actual, 'Native rail graph duplicates a name')
        links = []
        for pointer in row[0x50//4:0x58//4]:
            host.require(pointer == 0 or base <= pointer < base+len(contents) and (pointer-base)%0x60 == 0,
                         'Native rail link escapes its group')
            links.append(struct.unpack_from('<I', contents, pointer-base+0x44)[0] if pointer else 0)
        actual[name] = (contents[offset+0x20:offset+0x2c], *links, row[0x48//4])
        flags[str(row[0x40//4])] = flags.get(str(row[0x40//4]), 0)+1
    reference = {}
    terrain_indices = {name: index for index, name in rail_export.terrain_mapping.TERRAINS.items()}
    for row in expected:
        next_ = expected[row['next_index']]['name'] if row['next_index'] is not None else 0
        previous = expected[row['previous_index']]['name'] if row['previous_index'] is not None else 0
        reference[row['name']] = (struct.pack('<3f', *row['position_thaw']), next_, previous,
                                  terrain_indices[row['thaw_terrain']])
    host.require(actual == reference, 'Original rail positions/next/previous links or terrain differ from all authored nodes')
    return dict(nodes=len(actual), links=sum(value[1] != 0 for value in actual.values()), flags=flags,
                terrain_indices=sorted({value[3] for value in actual.values()}))


def run(api, process, record, deadline, log, inputs, scene, context, system, *, preloaded=None):
    """After full native startup and real scene load; one retained owned worker."""
    path, report = inputs['path'], inputs['report']
    host.require(path.read_bytes() == inputs['source'] and hashlib.sha256(inputs['source']).hexdigest() == report['qb_sha256']
                 and scene.get('col_sha256') == report['scene_col_sha256'], 'Authored rail file or owned COL changed')
    read, word, worker = system.read, system.word, system.worker
    arena_start, arena_end = word(0x84376c), word(0x843770)
    actor = context['actor']

    def words(address, count):
        host.require(address % 4 == 0 and arena_start <= address <= arena_end-count*4, 'Rail native allocation leaves owned arena')
        return struct.unpack('<'+str(count)+'I', read(address, count*4))

    def call(key, args=(), this=None):
        return host.native_call(api, process, PINS[key], list(args), record, deadline, log, this)

    def pins():
        for name, va, size, digest in PINS.values():
            host.require(hashlib.sha256(read(va, size)).hexdigest() == digest, 'Stock rail pin changed: '+name)

    detached = []

    def idle(allow_global_detach=False):
        state = worker.snapshot()
        host.require(state[STATE//4] == IDLE and state[TID//4] == worker.tid and state[COUNT//4] == worker.count and
                     api.WaitForSingleObject(worker.thread, 0) == 258 and word(0x8405c0) == 0,
                     'Rails require idle owned worker and no collision cache query')
        evidence = probe_vm_idle.snapshot(system, context, preloaded)
        host.require(evidence['admissible'], 'Rails require an idle/restored original VM')
        world, node = probe_gta_scene.WORLD, scene['node']
        entries = scene_chain(read, scene, log)
        extras = [entry for entry in entries if entry['node'] != node]
        if extras:
            host.require(allow_global_detach and not detached and len(extras) == 1,
                         'Unexpected additional native world scene')
            extra = extras[0]
            address, header = extra['words'][1], extra['scene_words']
            host.require(extra['words'][0] == 0x6936a4 and header[0x10//4] == 1 and
                         header[0x54//4:0x5c//4] == (1, 1) and header[0x68//4] == 1 and
                         header[0x60//4] != 0 and header[0x6c//4] != 0 and header[0x94//4] != 0,
                         'Extra scene is not a loaded original Global candidate')
            source = inputs['global_data']['col']
            host.require(hashlib.sha256(source).hexdigest() == GLOBAL_COL_SHA,
                         'Prepared private Global COL changed')
            base = header[0x70//4]-80
            expected = relocated_global_col(source, base)
            contents = read(base, len(expected))
            log('gta_rails_global_col_identity', scene=address, col=base,
                source_sha256=GLOBAL_COL_SHA, expected_relocated_sha256=hashlib.sha256(expected).hexdigest(),
                native_relocated_sha256=hashlib.sha256(contents).hexdigest(),
                differing_byte_offsets=[i for i, (x, y) in enumerate(zip(contents, expected)) if x != y])
            verify_global_col(contents, source, base)
            host.require(read(address+0x34, 32) == source[48:80], 'Global scene grid bounds differ from original source')
            actor_state, scene_state = read(actor, 0x35c), read(address, 0xa0)
            call('SceneNodeUnlink', this=extra['node'])
            host.require(read(actor, 0x35c) == actor_state and read(address, 0xa0) == scene_state and
                         read(base, len(contents)) == contents and struct.unpack('<5I', read(extra['node'], 20)) ==
                         (0x6936a4, address, 0, extra['node'], extra['node']),
                         'Original Global unlink changed retained scene/actor/source or did not detach')
            detached.append(extra)
            log('gta_rails_global_scene_detached', scene=address, node=extra['node'], source_sha256=GLOBAL_COL_SHA,
                original_node_unlink=True, all_resources_retained=True, native_unload_called=False)
        host.require(struct.unpack('<5I', read(world, 20)) == (probe_gta_scene.LIST_VTABLE, 0xffffffff, 0, node, node) and
                     struct.unpack('<5I', read(node, 20)) == (probe_gta_scene.LIST_VTABLE, scene['scene'], 0, world, world),
                     'Rails require exactly the matched owned GTA scene')

    pins()
    idle(allow_global_detach=True)

    cwd_buffer = system.allocate(bytes(1024))
    n = system.call('kernel32.dll', 'GetCurrentDirectoryW', (512, cwd_buffer))
    host.require(0 < n < 512 and Path(read(cwd_buffer, n*2).decode('utf-16le')).resolve() == inputs['directory'],
                 'Original resource working directory differs from declared rail input')
    game, manager = word(0x83e81c), word(0x83e87c)
    rail_manager = words(game, 0x9c//4)[0x24//4]
    log('gta_rails_before_guards', node_manager=manager, node_manager_refs=word(0x83e880),
        game=game, rail_manager=rail_manager, rail_group_words=words(rail_manager, 5),
        parser_state=word(0x83f348), parser_queue=word(0x83f398), replay=word(0x842710))
    players = word(0x6ea804)
    words(players, 0x208//4)
    # Original thiscall walks Players+0x204; this is not the separate Game object.
    host.require(call('NativeGetSkater', (0,), players) == actor and word(actor) == 0x6989e8,
                 'Initial rails require the genuine registered local skater')
    host.require(words(rail_manager, 0xa4//4) == (0,)*(0xa4//4) and word(0x83f348) == word(0x83f398) == 0 and
                 word(0x6dd3f4) != 0 and words(word(0x842710), 17)[16] == 0xd443a2bc,
                 'Initial rails require empty genuine manager/parser and FreeSkate replay/resource context')
    actor_before = read(actor, 0x35c)
    vm_addresses = (0x6dcf84, 0x6dcf8c, 0x6dcf90, 0x6dcf98, 0x6dd4a4, 0x6dd4a8)
    vm_before = [word(va) for va in vm_addresses]
    heap_depth = word(0x842b18+0x10c)
    host.require(heap_depth < 16, 'Original heap stack exceeds its native capacity')
    heap_before = read(0x842b18+0xc8, (heap_depth+1)*4)
    if manager == 0:
        host.require(word(0x83e880) == 0, 'Unexpected NodeManager reference')
        manager = call('NodeManagerAcquire', (1,))
    manager_words = words(manager, 0xe10//4)
    slots = []
    for slot in range(48):
        row = manager_words[slot*8:slot*8+8]
        if any(row):
            slots.append(dict(slot=slot, words=row,
                              array_header=words(row[1], 3) if row[1] else None))
    log('gta_rails_node_manager_initial', manager=manager, slots=slots,
        nonzero_words=[(index*4, value) for index, value in enumerate(manager_words) if value])
    global_slot, global_asset = manager_words[:8], manager_words[0xe04//4]
    host.require(word(0x83e87c) == manager and word(0x83e880) == 1 and
                 global_slot[0] == GLOBAL_ARRAY and global_slot[2:4] == (1, 0) and global_slot[6] == 0 and
                 all(global_slot[i] != 0 for i in (1, 4, 5, 7)) and global_asset != 0 and
                 manager_words[8:0x600//4] == (0,)*((0x600-32)//4) and manager_words[0x600//4] == 0 and
                 manager_words[0x604//4:0xe04//4] == (0,)*((0xe04-0x604)//4) and
                 manager_words[0xe08//4:] == (0xffffffff, 0), 'Expected only the original Global NodeArray in slot zero')
    global_resource = words(global_asset, 0x34//4)
    host.require(global_resource[0] == 0x68f060 and global_resource[0x24//4] == GLOBAL_ARRAY and
                 global_resource[0x28//4] == global_slot[1] and global_resource[0x2c//4]&255 == 1,
                 'Global native resource identity differs')
    global_scratch = system.allocate(bytes(16))
    verify_global_nodes(global_slot[1], inputs['global_data']['nodes'], words, read, call, global_scratch)
    log('gta_rails_original_nodes_preserved', source_sha256=GLOBAL_QB_SHA, array=global_slot[1],
        count=2, original_slot=0, all_original_fields_verified=True, new_slot=1)
    keys = [rail_export.qbkey('gta_rails'+suffix) for suffix in ('_NodeArray', '_NodeArray_SortedNames', '_NodeArray_SortedIndices')]
    host.require(all(call('lookup', (key,)) == 0 for key in keys), 'Authored rail symbols already exist')
    params, scratch = system.allocate(bytes(8)), system.allocate(bytes(16))
    filename = system.allocate(inputs['native_filename'].encode('ascii')+b'\0')
    host.require(call('NativeCStructCtor', this=params) == params, 'Original rail parameter constructor differs')
    call('NativeAddString', (0, filename), params)
    host.require(call('GetString', (0, scratch, 1), params) & 255 and
                 read(word(scratch), len(inputs['native_filename'])+1) == inputs['native_filename'].encode('ascii')+b'\0',
                 'Original AddString/GetString roundtrip differs')
    log('gta_rails_before_load', native_filename=inputs['native_filename'], qb_sha256=report['qb_sha256'], nodes=report['node_count'])
    host.require(call('NodeArrayManLoad', (params, 0)) & 255, 'Original whole NodeArray resource load failed')
    after = words(manager, 0xe10//4)
    array, asset = after[9], after[0xe04//4]
    log('gta_rails_node_manager_loaded', first_two_slots=after[:16], selected_slot=after[0x600//4],
        array=array, asset=asset, array_header=words(array, 3))
    host.require(after[:8] == global_slot and after[8] == rail_export.ARRAY and after[10]&255 == 1 and after[0x600//4] == 1 and
                 words(array, 3)[:2] == (0xa0100, report['node_count']) and call('NodeArrayFindNamed', (rail_export.ARRAY,)) == array,
                 'Native NodeArray registration differs')
    resource = words(asset, 0x34//4)
    log('gta_rails_resource', manager=manager, array=array, asset=asset, resource_words=resource,
        source_crc_expected=rail_export.qbkey(inputs['native_filename']+'.Wpc'))
    host.require(resource[0] == 0x68f060 and resource[0x28//4] == array and resource[0x24//4] == rail_export.ARRAY and
                 resource[0x2c//4]&255 == 1 and resource[0x20//4] == rail_export.qbkey(inputs['native_filename']+'.Wpc'),
                 'Native enum13 resource identity/array differs')
    call('StructClear', this=params)
    call('NativeAddChecksum', (0xd6b86721, rail_export.ARRAY), params)
    old_option = call('lookup', (RAIL_FORMAT_KEY,))
    before_option = struct.unpack('<5I', read(old_option, 20))
    log('gta_rails_format_before', symbol=old_option, words=before_option, key=RAIL_FORMAT_KEY)
    host.require(before_option[:4] == (0x10400, RAIL_FORMAT_KEY, rail_export.qbkey('scripts/game/startup.qb.wpc'), 1),
                 'Original stock rail-format default/source differs')
    config = dynamic_rail_config()
    config_buffer = system.allocate(config+CONFIG_NAME.encode('ascii')+b'\0')
    call('ConfigLoad', (config_buffer+len(config), config_buffer, 0))
    option = call('lookup', (RAIL_FORMAT_KEY,))
    option_words = struct.unpack('<5I', read(option, 20))
    log('gta_rails_format_selected', symbol=option, words=option_words, source=CONFIG_NAME,
        source_bytes=len(config), source_sha256=hashlib.sha256(config).hexdigest(),
        original_loader=True, value_before=1, value_requested=0, host_symbol_writes=False)
    host.require(option == config_buffer+28 and read(config_buffer, 1) == b'\1' and
                 option_words[:4] == (0x10400, RAIL_FORMAT_KEY, rail_export.qbkey(CONFIG_NAME), 0),
                 'Original QB loader did not select its NodeArray rail format')
    host.require(call('ParseNodeArray', (params, 0)) & 255 and word(0x83f348) == word(0x83f398) == 0,
                 'Original whole NodeArray parser did not finish')
    call('StructDtor', this=params)
    group = words(rail_manager, 0xa4//4)
    log('gta_rails_manager_parsed', manager=rail_manager, group_words=group,
        source_node_count=report['node_count'], parser_state=word(0x83f348), parser_queue=word(0x83f398))
    host.require(group[0] == 1 and group[2] == group[3] == report['node_count'] and group[5:] == (0,)*36,
                 'Native RailManager did not retain exactly one complete group')
    words(group[1], report['node_count']*(0x60//4))
    result = validate_graph(read(group[1], report['node_count']*0x60), group[1], report['nodes'])
    host.require(words(manager, 8) == global_slot and words(global_asset, 0x34//4) == global_resource,
                 'Original Global slot/resource changed during new rail registration')
    verify_global_nodes(global_slot[1], inputs['global_data']['nodes'], words, read, call, global_scratch)
    host.require(read(actor, 0x35c) == actor_before and [word(va) for va in vm_addresses] == vm_before and
                 word(0x842b18+0x10c) == heap_depth and read(0x842b18+0xc8, (heap_depth+1)*4) == heap_before and
                 word(0x83e87c) == manager and word(game+0x24) == rail_manager,
                 'Original node parser changed actor, VM, heap stack or manager ownership')
    idle()
    pins()
    log('gta_rails_pass', **result, qb_sha256=report['qb_sha256'], col_sha256=report['scene_col_sha256'],
        original_node_resource_load=True, original_whole_parser=True, all_positions_links_verified=True,
        host_native_nodes_written=False, process_owned_until_exit=True, grind_contact_verified=False, streaming_verified=False)
    return dict(manager=manager, rail_manager=rail_manager, array=array, resource=asset, group=group[1], **result)
