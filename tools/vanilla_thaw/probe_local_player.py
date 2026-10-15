"""Original disconnected Client and PlayerInfo on the already genuine Base.

Only original factories publish native objects/lists. The host declares one
local player (flags=1, connection=null); this does not run pending multiplayer
startup, the GameSpy subclass, network tasks, a connection or transport loop.
"""
import hashlib
import struct

import probe_frontend_base
import probe_loader as host

PINS = {
    'PlatformAcquire': ('OriginalLocalPlatformAcquireEntry', 0x438c60, 6, '536ee08e45006eebeb26f1ff596b958b689171b9a8a75d103d5dce6dcc58a6f3'),
    'PlatformCaller': ('OriginalPlatformAcquireStartupCaller', 0x54354d, 21, 'bddafe2abbf992c1746711da26cbbec32793b71fa1ed3f985adbe879fc58c559'),
    'PlatformRelease': ('OriginalPlatformSingletonIdentity', 0x438fd0, 69, 'c5b3c1e1f3415a78341ef37774fecf64430fff2cfd9fd7821cdf2f4776d0e96b'),
    'PlatformInit': ('OriginalLocalPlatformInitialize', 0x440070, 212, '8ca79adadcd86e99ff14c59501a4f3bc1f11491f57d9932c9d67e3cca3f8f781'),
    'PlatformVtable': ('OriginalLocalPlatformVtable', 0x68fa00, 20, 'b290781bb3adef34c1942537a3836a56ec818ee1a79881b6cd5ad42ffd92ad94'),
    'SocketStartup': ('OriginalPlatformWinsockStartup', 0x43ff20, 95, '5cf1b7638da6c12e06f7cfbe67d83557eb185343acdca789ce4f8167cd232ce1'),
    'CreateClient': ('OriginalBaseCreateLocalClient', 0x4a2410, 134, 'eeeaaae8a633b6f7e62f9ae6a64b15d01f7aa773c75c9979fa66caaffbcf5275'),
    'PlatformCreateClient': ('OriginalPlatformCreateClientRegion', 0x4384d0, 212, '0b55d06d75d7c32aeab1352102b394651ff2d58d85fbee0974eb37f9ceb2f09e'),
    'ClientFactory': ('OriginalLocalClientFactoryEntry', 0x43fd00, 6, 'df0fa3d5e2502874b523872efc2e2433b97bb65b626f4b9a18066d926102bdc2'),
    'ClientCtor': ('OriginalClientBaseCtor', 0x43c770, 502, '1a102ea22c41cd01c5f693fe0ea73fbc9322fee7223dfc03f2113295ea59fbf5'),
    'ClientEmbeddedCtor': ('OriginalClientEmbeddedCtorEntry', 0x43a630, 6, '7dfe97993d15a88a09419984432734f4aa20a5368d53c175d9c0375b94e0d802'),
    'ClientLocalFlag': ('OriginalClientLocalFlag', 0x43a430, 13, 'dfacd51cf6681a36ac8e1738548c97bad596fa062d5882d02fe95d1191469506'),
    'ClientInit': ('OriginalClientLocalSocketSkip', 0x440190, 138, '7a495dc990a7d94dc53d955e7a9203ae2ba1032bffbe512ca571c04f581c6bf0'),
    'ClientTasks': ('OriginalClientTaskAndHandlerEntry', 0x43a0b0, 6, '150a48a2913c62d0fb2ad96f1a9e615e5fcddca17e2abb82b4f014718409c32b'),
    'ClientVtable': ('OriginalLocalClientVtable', 0x68fb10, 64, '55f04329dc455357557d3e550f1ccf6d06aa37f107143d8b1e7ef4dc3f2ed62e'),
    'BaseVtable': ('OriginalBaseLocalClientVirtuals', 0x6927d8, 168, 'cbed81740a80c7d49456d4a82240b88459ea84f342da7f8b9bf33f0a1180d122'),
    'BaseOnClient': ('OriginalBaseOnClient', 0x4a24a0, 49, 'b2efb89b9ffe257ff7ecc4f7d3974e0254f04b440bc580d8690f7d83f2b2e79d'),
    'BaseDefaultNotification': ('OriginalBaseDefaultNotification', 0x4b94e0, 3, 'e598d0c3ba86d917b177d7adde0556aa99bc355543c57aee0a3e50b684dd7e99'),
    'CreatePlayer': ('OriginalCreateLocalPlayerInfo', 0x54a930, 272, 'cc884a255e5f211d36ccefc3aef49610b413be3f2d10f0f44668e6b601a203ff'),
    'PlayerCtor': ('OriginalPlayerInfoCtorEntry', 0x552b20, 6, '5514adf1434139b815b86173a0494a4da2f98f5f3530381947901654b3562eae'),
    'PlayerVtable': ('OriginalPlayerInfoVtable', 0x6974a0, 24, '8994fe043924a39d7f9978e07c53f7e319f6a67c4f563c91f5a686d1c39bd25f'),
    'RegisterPlayer': ('OriginalRegisterLocalPlayerInfo', 0x4a4820, 28, 'ade5b940c45ebb2dc0ff613819a8e4da70c737cc412b3d038116c354808ae4bb'),
    'LookupPlayer': ('OriginalPlayerLookupRoutedEntry', 0x4a6670, 7, '2b03e9496cef02ffe76f804184421cd223f78299c57bdd28bfebba1bb2de76c4'),
    'GetSkater': ('OriginalPlayerInfoGetSkater', 0x552bc0, 4, '75315a13a7503cb9a126675518a19ff201faa8c510e8a849d0f971d9c61bbb2b'),
    'LocalIndex': ('OriginalPlayerLocalIndexRoutedEntry', 0x551850, 10, '4e8e1f5212ae0fd1c4a50871cf928ff748fee31ad0b649e8c11659730d4e35be'),
    'GetClient': ('OriginalBaseGetLocalClient', 0x4a27a0, 14, '39fa2f9488c7b69b576f773db654ca885cf94f2a3374dcfa60289fe95569a85f'),
    'Connected': ('OriginalBaseConnectedPredicate', 0x4a4b40, 58, '668d003f7c509ab30321e58772e0d4c8faa9d45e3b0c411f0e65bd8fa653666f'),
    'NetworkHeapStartup': ('OriginalSharedNetworkHeapStartup', 0x627436, 48, '77f12f98cb9492822582e8f271eb030b61417b06ac146f204db792437763f4e9'),
    'NetworkHeapName': ('OriginalNetworkHeapName', 0x6a22ec, 8, '8896dffd72754eb4de4406982e270f08167793428b478198010d16d408e66b77'),
    'ActivationCaller': ('OriginalPlayerActivationCaller', 0x5503f0, 487, '324368f0e01d324a07bf27b97637c20c84849f52d3f56c96bc3d61ef0683f122'),
}


def validate_player(fields, pointer, actor, links):
    host.require(len(fields) == 0x1f0 // 4 and fields[0] == 0x6974a0 and fields[1:3] == (pointer, 0) and
                 fields[3:5] == links and fields[0x14 // 4] == 0 and fields[0x34 // 4] == 1 and
                 fields[0x44 // 4] == actor and fields[0x1ec // 4] == 0,
                 'Original PlayerInfo is not the declared disconnected local actor')


def unchanged_except(before, after, offsets):
    host.require(len(before) == len(after) and all(a == b or i * 4 in offsets
                 for i, (a, b) in enumerate(zip(before, after))), 'Native local startup changed unrelated owner fields')


def validate_client(fields, client, platform, count, base_client, local_flag):
    # The untouched stock factory leaves this field zero (native runs 16/17).
    # ClientInit's original local branch skips socket creation and bind; this
    # is the constructor field, not an INVALID_SOCKET or opened transport.
    host.require(len(fields) == 0x2018 // 4 and fields[0] == 0x68fb10 and
                 fields[0x1e9c // 4] & 8 == 8 and fields[0x1428 // 4] == 0 and
                 fields[0x1458 // 4] == platform and count == 1 and
                 base_client == client and local_flag & 0xff == 1,
                 'Original local Client ownership or socket-skip flag differs')


def run(api, process, record, deadline, log, context, system):
    read, word = system.read, system.word
    pins = {**host.VM_PINS, **PINS}
    start, end = word(0x84376c), word(0x843770)

    def words(address, count):
        host.require(address % 4 == 0 and count > 0 and start <= address <= end - count * 4,
                     'Original local player allocation leaves its native arena')
        return struct.unpack(f'<{count}I', read(address, count * 4))

    def call(name, args=(), this=None):
        return host.native_call(api, process, pins[name], list(args), record, deadline, log, this)

    def check_pins():
        for name, va, size, digest in PINS.values():
            host.require(hashlib.sha256(read(va, size)).hexdigest() == digest, 'Local player native pin differs: ' + name)

    def idle():
        host.require(word(0x6dcf84) == 0 and read(0x6dcf98, 2) == bytes(2) and read(0x6dd4a4, 1) == b'\0',
                     'Local PlayerInfo requires idle original script and owner phases')

    check_pins()
    idle()
    actor, base, manager = context['actor'], word(0x83e468), 0x842b18
    host.require(base >= 0x10000 and word(0x7689e8) == base and word(base) == 0x6927d8 and
                 word(0x6dd908) == word(0x6dd90c) == word(manager + 0x154) == 0,
                 'Original local graph requires the existing Base and unused platform/Network heap')
    before_base = struct.unpack('<' + 'I' * (0xfe24 // 4), read(base, 0xfe24))
    host.require(all(before_base[at // 4] == 0 for at in (0x38, 0xa0, 0xc4, 0xd8, 0x8188, 0x818c, 0x8190, 0x8194, 0x8198)) and
                 before_base[0x50 // 4:0x60 // 4] == (0xffffffff, 0, base + 0x4c, base + 0x4c),
                 'Original Base is not an empty disconnected local context')
    actor_before = words(actor, 0x35c // 4)
    host.require(actor_before[0] == 0x6989e8 and actor_before[0x18 // 4] == 0 and actor_before[0x124 // 4] == 0 and
                 actor_before[0xd8 // 4] & 0xff == 1, 'PlayerInfo requires genuine local skater index zero')
    vm_before = read(0x6dcf84, 16)
    heap_before = (word(manager + 0xc8), word(manager + 0x10c))
    task_manager = word(0x6dd8d8)
    queues_before = probe_frontend_base.task_queues(words, task_manager)
    arena, frontend = word(manager + 0x11c), word(manager + 0x120)
    arena_before, frontend_before = words(arena, 8), words(frontend, 12)
    host.require(arena_before[5] - arena_before[1] == 0x100000 and arena_before[6:] == (frontend, 0) and
                 frontend_before[9:11] == (arena, 1) and arena_before[5] - frontend_before[11] >= 0x10000 and
                 word(manager + 0xc0) < 48, 'Original shared FrontEnd arena cannot own a Network heap')
    heap = call('CreateHeap', (arena, 0xffffffff, 0x6a22ec), manager)
    heap_fields = words(heap, 12)
    host.require(heap_fields[9:11] == (arena, 0xffffffff) and words(arena, 8)[6:] == (frontend, heap),
                 'Original Network heap did not register the opposite shared-arena direction')
    # This borrowed manager slot is published by the pinned original startup.
    host.remote_write(api, process, manager + 0x154, struct.pack('<I', heap))
    log('local_player_begin', base=base, actor=actor, network_heap=heap, original_base_reused=True,
        declared_local_flags=1, declared_connection=0, pending_multiplayer_startup=False)
    platform = call('PlatformAcquire', (1,))
    platform_state = words(platform, 0x1d0 // 4)
    host.require(platform_state[0] == 0x68fa00 and word(0x6dd908) == platform and word(0x6dd90c) == 1 and
                 platform_state[0x1c8 // 4] == 0, 'Original local platform singleton identity or initial clients differ')
    client = call('CreateClient', (1, 1, 0), base)
    client_state = words(client, 0x2018 // 4)
    local_flag = call('ClientLocalFlag', this=client)
    log('local_client_observed', client=client, vtable=client_state[0],
        flags=client_state[0x1e9c // 4], socket=client_state[0x1428 // 4],
        platform=platform, client_platform=client_state[0x1458 // 4],
        platform_clients=word(platform + 0x1c8), base_client=word(base + 0x8190),
        local_flag=local_flag & 0xff)
    validate_client(client_state, client, platform, word(platform + 0x1c8), word(base + 0x8190), local_flag)
    sentinel = client + 0x1ee4
    host.require(words(sentinel, 5)[1:] == (0xffffffff, 0, sentinel, sentinel),
                 'Original local Client has a connection or malformed sentinel')
    tasks = [client_state[at // 4] for at in (0x145c, 0x1460, 0x1464, 0x1468)]
    host.require(len(set(tasks)) == 4 and all(words(task, 8)[7] == client and words(task, 8)[3] != 0 for task in tasks),
                 'Original local Client did not own all four registered tasks')
    queues_after = probe_frontend_base.task_queues(words, task_manager)
    old_links = {node[0] for queue in queues_before for node in queue['links']}
    new_links = {node[0] for queue in queues_after for node in queue['links']}
    host.require(old_links <= new_links and new_links - old_links == {words(task, 8)[2] for task in tasks},
                 'Original local Client changed prior task ownership or registered other tasks')
    host.require(call('Connected', this=base) & 0xff == 0 and call('LookupPlayer', (0,), base) == 0,
                 'Original Base already has this PlayerInfo or entered a network game')
    info = call('CreatePlayer', (actor, 0, 1), base)
    validate_player(words(info, 0x1f0 // 4), info, actor, (info, info))
    call('RegisterPlayer', (info,), base)
    validate_player(words(info, 0x1f0 // 4), info, actor, (base + 0x4c,) * 2)
    host.require(struct.unpack('<4I', read(base + 0x50, 16)) == (0xffffffff, 0, info, info) and
                 call('LookupPlayer', (0,), base) == info and call('GetSkater', this=info) == actor and
                 call('LocalIndex', this=info) == 0 and call('GetClient', (0,), base) == client,
                 'Original PlayerInfo/actor/index/Client lookup chain failed')
    unchanged_except(before_base, struct.unpack('<' + 'I' * (0xfe24 // 4), read(base, 0xfe24)), {0x58, 0x5c, 0x8190})
    host.require(words(actor, len(actor_before)) == actor_before and words(client, len(client_state)) == client_state and
                 read(0x6dcf84, 16) == vm_before and (word(manager + 0xc8), word(manager + 0x10c)) == heap_before,
                 'Original local registration changed actor, Client, source VM or heap execution context')
    idle()
    check_pins()
    log('local_player_pass', original_base_reused=True, base=base, platform=platform, client=client, player_info=info,
        actor=actor, local_index=0, original_factory_flags=1, connection=0, original_lookup_chain=True,
        native_client_tasks_registered=4, native_network_tasks_executed=False,
        original_local_branch_skips_socket_and_bind=True, actual_transport_connection_requested=False,
        original_socket_field=client_state[0x1428 // 4],
        game_spy_subclass_constructed=False, original_player_fields_written_by_host=False,
        lifetime='original objects retained until bounded child termination', backend_ready=False)
    return {'platform':platform, 'client':client, 'player_info':info}
