"""Whole stock logical HUD update and bounded original draw-object readback.

This executes ScreenManagerUpdate on its real FrontEnd heap between completed
native frames. The original graph, notifications, text, fades and sprite layout
are retained; no native GPU drawing or GTA publication takes place here.
Readback records are private diagnostic inputs, not a replacement HUD design.
"""
import hashlib
import math
import struct

import native_worker
import probe_frontend
import probe_loader as host
import probe_vm_idle

ALLOCATOR = 0x842b18
SCREEN, EVENTS = 0x76b868, 0x6dd6d0
ROOT_ID = probe_frontend.ROOT_ID
GROUPS = (0x675adbaa, 0xf4d3a70e, 0x44727dae, 0xa4db8a4b,
          0x7d42821c, 0xfe1dc544, 0xf8a4ebee)
# Original ctor stores and full vtable prefixes establish these identities.
TYPES = {0x5b9da842: (0x694488, 0x188), 0xa13adbb7: (0x6946b4, 0x188),
         0xb12b510a: (0x694610, 0x190), 0x5200dfb6: (0x694680, 0x1bc),
         0x40d92263: (0x69464c, 0x1d0)}
PINS = (
    ('ScreenManagerUpdate', 0x4fa3d0, 54, '87abbb3007fdfd0325623205718ed850b42e60036fb6489b7a6abdde8d63db41'),
    ('RootUpdateLock', 0x4f8f00, 6, '3f24436c035ac12f40abb56e7444f0e2668387c3d6cfc73f1fb96349c688294c'),
    ('ContainerVtable', 0x694488, 44, 'a2a3e4492479b38bdd518ba8dd1bdb09b0df30a35c02341a7dda53c7f22d8c97'),
    ('WindowVtable', 0x6946b4, 44, '490d84fbb677c0d25849f5900f1123a0f34066f176cffa131a182dff1b2188f2'),
    ('SpriteVtable', 0x694610, 44, '2651b8bf1f59011c3a4ae129aee30a24222f28c7fd295d5868af68dc86db8689'),
    ('TextVtable', 0x694680, 44, 'b1719b4b7f23f4b844bd06ed312476c8db1c040b625186fd6344c17adfb90829'),
    ('TextBlockVtable', 0x69464c, 44, 'd6c911e8b8ec6a1b7925c597f9109793b93d4a1e14b0bb4b8e9beee3e4812818'),
    ('TextWrapperVtable', 0x694ddc, 20, '3b69325e5294de6187d9d489c41eb3688f03fbafbeb28a6880b8cacc54c95663'),
    ('SpriteWrapperVtable', 0x695360, 52, '808850d5f6541d2ee42e9b40c4eac03f7e8a776c7c25a2265599de5e695fe6bb'),
    ('TextBackendVtable', 0x6955e0, 20, '2a2fca3ca976a16215642730a3c1a24c86338cec4f54d016ba602b8f6f46e5e8'),
    ('SpriteBackendVtable', 0x696c10, 32, 'c137723c163c4b6c9a66965ec0a086dd64b29cee43efed0537fda9f578b01a00'),
    ('ContainerStore', 0x432559, 12, '06214fe6972d1f294802e615ceb178f38b46dfa86f196b7334b06c8945d63b56'),
    ('WindowStore', 0x471b7f, 6, '79f3fe48e40bafaf122408670489c4363f28a84af64a21415edbe8bbcaae27ec'),
    ('SpriteStore', 0x532e32, 12, '79ee496ab9291fbdcbe9059ce8b53a001db5d1eb843ded85c66ffc200bb4a32c'),
    ('TextStore', 0x4ff0a5, 12, '0ab8c310801c30aff37d8c7bb40c0b87b0a549853a9534dab0f7dca537502927'),
    ('TextBlockStore', 0x4feafc, 12, 'd6d3bdabde1190fed2ad2cf9021d6fd4bfdb54020c87ae101e78e5f0e109b9ad'),
    ('TextBackendCtor', 0x529290, 56, '1ce73a0df5120d31761b97d9b54530b69a874fba18fd6366f5bcb16b6a59c2f1'),
    ('SpriteBackendStore', 0x53ef27, 55, '3ebca16ff3c81cb7ee8117ed97ae4f9d3cb102e9c314c40e1d0c0e00d643ed9c'),
    ('TextSetText', 0x509980, 118, '1418f233475aee022d56e20837f15599cf5e85499e24fcedf05ff88faf946ea2'),
    ('SpriteSourceBinding', 0x51fe60, 99, 'f22b3176957043d9d220336997f625b7b17f0661f0277fe306acdc79417975ae'),
)


def u32(raw, offset=0):
    return struct.unpack_from('<I', raw, offset)[0]


def graph(read, manager):
    """Check the real intrusive registry, then all parent/child/sibling edges."""
    data = read(manager, 0x6ac)
    host.require(u32(data) == 0x6944bc, 'Original layout manager identity differs')
    head, node, previous, rows = manager + 4, u32(data, 0x10), manager + 4, {}
    while node != head:
        address = node - 0x38
        host.require(len(rows) < 2048 and address not in rows, 'Original screen registry is cyclic or oversized')
        raw = read(address, 0x90)
        kind = u32(raw, 0x20)
        host.require(kind in TYPES, 'Unsupported original screen element type')
        vtable, size = TYPES[kind]
        host.require(u32(raw) == vtable and u32(raw, 0xc) == manager and u32(raw, 0x48) == previous,
                     'Original screen element identity, owner or previous link differs')
        host.require(u32(raw, 0x50) < 0x7fffffff, 'Original screen update lock cannot accept increment')
        rows[address] = read(address, size)
        previous, node = node, u32(raw, 0x44)
    host.require(previous == u32(data, 0x14), 'Original screen registry tail differs')
    root = u32(data, 0x44)
    host.require(root in rows and u32(rows[root], 0x18) == ROOT_ID and
                 u32(rows[root], 0x20) == 0xa13adbb7 and u32(rows[root], 0x60) == 0,
                 'Original screen root is absent or belongs to another parent')
    edges = set()
    for parent, raw in rows.items():
        child, prior, seen = u32(raw, 0x6c), 0, set()
        while child:
            host.require(child in rows and child not in seen and child not in edges,
                         'Original UI children are cyclic, duplicated or outside registry')
            seen.add(child)
            row = rows[child]
            host.require(u32(row, 0x60) == parent and u32(row, 0x84) == prior,
                         'Original UI child parent or previous sibling differs')
            edges.add(child)
            prior, child = child, u32(row, 0x78)
        ancestor, seen = u32(raw, 0x60), {parent}
        while ancestor:
            host.require(ancestor in rows and ancestor not in seen, 'Original UI parent chain is cyclic or foreign')
            seen.add(ancestor)
            ancestor = u32(rows[ancestor], 0x60)
    host.require(edges == {p for p, r in rows.items() if u32(r, 0x60)}, 'Original UI parent/child lists disagree')
    pending, visited = [root], set()
    while pending:
        address = pending.pop()
        host.require(address in rows and address not in visited, 'Original root lock walk is cyclic or foreign')
        visited.add(address)
        for at in (0x78, 0x6c):
            value = u32(rows[address], at)
            if value:
                pending.append(value)
        host.require(len(pending) <= 32, 'Original lock walk exceeds its native fixed stack')
    return dict(manager=manager, root=root, rows=rows, reachable=visited,
                projected=sum(bool(u32(rows[p], 8) & 0x80000) for p in visited))


class DrawReader:
    def __init__(self, read):
        self.read = read

    def string(self, address):
        if not address:
            return b''
        data = bytearray()
        while len(data) < 512:
            part = self.read(address + len(data), min(64, 512 - len(data)))
            end = part.find(b'\0')
            if end >= 0:
                return bytes(data + part[:end])
            data.extend(part)
        raise RuntimeError('Original HUD string has no bounded terminator')

    def draws(self, tree, suffix):
        host.require(type(suffix) is int and 0 <= suffix <= 3, 'Original HUD player suffix differs')
        wanted = {((key + suffix) & 0xffffffff) for key in GROUPS}
        roots = [p for p, r in tree['rows'].items() if u32(r, 0x18) in wanted]
        host.require(len({u32(tree['rows'][p], 0x18) for p in roots}) == len(roots), 'Original HUD group ID is duplicated')
        pending, visited, result = list(roots), set(), []
        while pending:
            pointer = pending.pop()
            # Authored named groups may nest inside another selected group.
            # graph() has already rejected cyclic/duplicated native edges; an
            # overlapping selection is not a second draw or a corrupt graph.
            if pointer in visited:
                continue
            host.require(pointer in tree['reachable'],
                         f'Original HUD group is detached: widget={pointer:#x} '
                         f'id={u32(tree["rows"][pointer], 0x18):#x} root={tree["root"]:#x}')
            host.require(len(visited) < 128, 'Original HUD selected subtree exceeds 128 elements')
            visited.add(pointer)
            row = tree['rows'][pointer]
            kind, widget_id = u32(row, 0x20), u32(row, 0x18)
            if kind == 0x5200dfb6:
                count = u32(row, 0x198)
                host.require(count <= 32, 'Original text effect count exceeds bounds')
                for offset in (0x194, 0x190):
                    array = u32(row, offset)
                    if not array or not count:
                        continue
                    for wrapper in struct.unpack('<' + 'I' * count, self.read(array, count * 4)):
                        header = self.read(wrapper, 0x50)
                        host.require(u32(header) == 0x694ddc, 'Original text wrapper identity differs')
                        draw = self.read(u32(header, 0x4c), 0x4c)
                        host.require(u32(draw) == 0x6955e0 and draw[8] <= 1,
                                     'Original text draw identity differs')
                        if draw[8] or not draw[0x47]:
                            continue
                        text = self.string(u32(draw, 0x24))
                        if not text:
                            continue
                        font = self.read(u32(draw, 0x1c), 0x178)
                        effects = self.read(u32(draw, 0x20), len(text) * 10) if u32(draw, 0x20) else b''
                        result.append(dict(kind='text', widget_id=widget_id, widget=pointer,
                                           draw_hex=draw.hex(), text_hex=text.hex(),
                                           text=text.decode('cp1252', errors='replace'), effects_hex=effects.hex(),
                                           font=u32(draw, 0x1c), font_header_hex=font.hex()))
            elif kind == 0xb12b510a:
                header = self.read(u32(row, 0x18c), 0x18)
                host.require(u32(header) == 0x695360, 'Original sprite wrapper identity differs')
                draw = self.read(u32(header, 0x14), 0x50)
                host.require(u32(draw) == 0x696c10 and draw[8] <= 1, 'Original sprite draw identity differs')
                if not draw[8] and draw[0x4b]:
                    image = self.read(u32(draw, 0x1c), 16)
                    result.append(dict(kind='sprite', widget_id=widget_id, widget=pointer,
                                       draw_hex=draw.hex(), image_header_hex=image.hex(),
                                       image_key=u32(row, 0x184)))
            host.require(len(result) <= 1024, 'Original HUD draw object count exceeds capture limit')
            child = u32(row, 0x6c)
            while child:
                pending.append(child)
                child = u32(tree['rows'][child], 0x78)
        return result


class NativeHudLayout:
    def __init__(self, api, process, record, deadline, log, context, system, *, vm_sources=None):
        self.api = api
        self.system, self.context, self.vm_sources = system, context, vm_sources
        self.worker, self.log = system.worker, log
        self.arena = struct.unpack('<2I', system.read(0x84376c, 8))
        host.require(0x10000 <= self.arena[0] < self.arena[1] <= 0xffffffff, 'Original UI allocator arena differs')
        self.manager, self.events = system.word(SCREEN), system.word(EVENTS)
        self.heap = system.word(ALLOCATOR + 0x120)
        host.require(self.manager and self.events and self.heap, 'Original screen/events/FrontEnd heap must exist')
        for name, va, count, digest in PINS:
            host.require(hashlib.sha256(system.read(va, count)).hexdigest() == digest,
                         'Original HUD layout pin differs: ' + name)
        pins = {'Update': PINS[0], 'Push': host.VM_PINS['HeapContextPush'],
                'Pop': host.VM_PINS['HeapContextPop']}
        self.call = lambda name, args=(), this=None: host.native_call(
            api, process, pins[name], list(args), record, deadline, log, this)
        self.actor_identity = self.identity()
        self.root = graph(self.read, self.manager)['root']

    def read(self, address, size):
        host.require(type(address) is int and type(size) is int and 0 < size <= 65536 and
                     self.arena[0] <= address <= self.arena[1] - size,
                     'Original HUD layout read leaves genuine allocator arena')
        return self.system.read(address, size)

    def identity(self):
        raw = self.read(self.context['actor'], 0x1c)
        return raw[:4] + raw[0x18:0x1c]

    def idle(self):
        s, worker = self.system, self.worker
        worker.check_call_access()
        state = worker.snapshot()
        host.require(worker.thread and self.api.WaitForSingleObject(worker.thread, 0) == 258 and
                     state[native_worker.STATE // 4] == native_worker.IDLE and
                     state[native_worker.TID // 4] == worker.tid and
                     state[native_worker.COUNT // 4] == worker.count,
                     'Original HUD layout requires its idle worker')
        clock = s.read(0x8459c8, 16)
        host.require(clock[:8] == clock[8:] and s.word(0x84388c) == ALLOCATOR and
                     s.word(SCREEN) == self.manager and s.word(EVENTS) == self.events and
                     s.word(ALLOCATOR + 0x120) == self.heap and s.word(SCREEN + 4) >= 1 and
                     struct.unpack('<2I', s.read(0x84376c, 8)) == self.arena and
                     self.identity() == self.actor_identity,
                     'Original HUD clock, owner, actor or heap identity changed')
        return clock, probe_vm_idle.require_idle(s, self.context, self.vm_sources)

    def update(self):
        before_clock, before_vm = self.idle()
        before = graph(self.read, self.manager)
        # World-projected widgets require the already published native view and
        # projection, never guessed matrices. Ordinary 2D score has no dependency.
        if before['projected']:
            for address in (0x7f3b88, 0x7f3bc8):
                values = struct.unpack('<16f', self.system.read(address, 64))
                host.require(all(math.isfinite(v) for v in values) and any(values),
                             'Original projected HUD requires genuine published camera matrices')
        depth = self.system.word(ALLOCATOR + 0x10c)
        host.require(depth < 16, 'Original FrontEnd heap stack has no capacity')
        heap_before = self.system.read(ALLOCATOR + 0xc8, (depth + 1) * 4)
        self.call('Push', (self.heap,), ALLOCATOR)
        try:
            host.require(self.system.word(ALLOCATOR + 0x10c) == depth + 1 and
                         self.system.word(ALLOCATOR + 0xc8) == ALLOCATOR + 0xcc + depth * 4 and
                         self.system.word(ALLOCATOR + 0xcc + depth * 4) == self.heap,
                         'Original HUD did not enter its genuine FrontEnd heap')
            self.call('Update', this=self.manager)
        finally:
            self.call('Pop', this=ALLOCATOR)
        host.require(self.system.word(ALLOCATOR + 0x10c) == depth and
                     self.system.read(ALLOCATOR + 0xc8, len(heap_before)) == heap_before,
                     'Original HUD did not restore its heap context')
        after = graph(self.read, self.manager)
        host.require(after['root'] == self.root == before['root'], 'Original HUD update replaced RootWindow')
        dimensions = struct.unpack_from('<2f', after['rows'][self.root], 0x168)
        host.require(dimensions == (640.0, 448.0), 'Original HUD canvas dimensions differ')
        draws = DrawReader(self.read).draws(after, u32(self.read(self.context['actor'] + 0x2f0, 4)))
        host.require(self.idle() == (before_clock, before_vm), 'Original HUD update changed frame or admitted VM context')
        result = dict(native_frame=struct.unpack_from('<Q', before_clock)[0], canvas=list(dimensions),
                      original_layout_update=True, original_frontend_heap_restored=True,
                      elements_before=len(before['rows']), elements_after=len(after['rows']),
                      retained_locks_balanced=all(u32(r, 0x50) == u32(after['rows'][p], 0x50)
                          for p, r in before['rows'].items() if p in after['rows']),
                      draws=draws, original_gpu_draw_executed=False, gta_hud_published=False)
        self.log('hud_layout_pass', **result)
        return result
