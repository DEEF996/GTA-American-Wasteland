"""Read genuine stock score, trick strings, balance and run timer after a step.

Requires the already verified owned stock child and its constructed local actor.
No native calls, allocation, text formatting, scoring rules or timer simulation.
Use on the frame driver's single producer thread between completed steps. This
exports data, not the original UI layout, fonts, fades or a GTA HUD renderer.
"""
import hashlib
import math
import struct

import native_worker
import probe_loader as host
import probe_vm_idle

SCORE, BALANCE, TIMER, WALK = 0xd262802f, 0x589e5fb3, 0x8bce7e66, 0x726e85aa
COMPONENTS = {SCORE: (0x6a1138, 0x1c), BALANCE: (0x6a0b54, 0x198), TIMER: (0x6a10ec, 0x28)}
KINDS = {0xef24413b: ('manual', 0x24), 0x0ac90769: ('nosemanual', 0x24),
         0x255ed86f: ('grind', 0x7c), 0x8d10119d: ('slide', 0x7c),
         0xa549b57b: ('lip', 0xd4), 0x3506ce64: ('skitch', 0x12c)}
TIMER_STATES = (0x742cf11f, 0x5392ca8e, 0x26776873, 0xdef417fa)
# Protected original routines stay in the whole pinned OS-loaded image. Entries
# labelled witness are exact instruction ranges establishing fields, not claims
# that those slices are standalone callable functions.
PINS = (
    ('score_component', 0x5f1940, 114, 'a37a5b22b9b50b0eedfc5401a8cb7919b880b1fea6a6e753e5b3f13aa40342aa'),
    ('score_initialization', 0x5f1380, 118, '44421add366b5f6ae5df3779c368aa9bf70651e2235eb55b3d53622017dd9342'),
    ('score_getter', 0x58efe0, 10, '3d3c40a05d7879f3d4c3e0d6987cc35143d23c04e2dbb2eb539e2c94d193de7a'),
    ('score_constructor', 0x56b420, 326, 'b80cef0ad5d499cd46b16cd5c918c037aae6532b52303447e349ec833ed47cd9'),
    ('score_recalculate', 0x56a540, 387, '6d78bc9696594e1f8c7f5cdb595cc0560e18cd130654bd4ab5e41575278ffbd4'),
    ('trick_allocation_witness', 0x56c34d, 54, '6135d10e50bb4c007b59bbb2beae2ca1dde2293aa7ccf4dc5c4c40a457dbf657'),
    ('trick_text_witness', 0x56c463, 111, '28ba96e150f0651d7c23a87c33890bea2204f08702dfe02cad9849551a61fee6'),
    ('balance_constructor', 0x5c3430, 133, '093a636dd36dab08c804f406766cc2831a7d385eabb67beb2aa249528402e5ce'),
    ('balance_binding', 0x587510, 59, '5deab29c36fbdaafa59855c5dbbd4e6b92b486f8d3a1b7cd675131934dbb9694'),
    ('balance_kind_getter', 0x5c3920, 91, '49113fc016ebbf0d20543d9724bc5350a3f478bc077c342d50b2e049be3b86c5'),
    ('balance_meter_witness', 0x587f57, 124, '529d2e731094032633c5d0011ce5fc9f664846ba1b69ce915f721d805763d779'),
    ('balance_scale', 0x698844, 4, '596efe745486347825731e447c921d9ac232490395ed5ae9442ea0eaa5f543ae'),
    ('timer_factory', 0x5f1210, 87, 'c804d1ea94d0ba927990bb2801023afea56a2864e26510e8573e505f6f0e25cc'),
    ('timer_update', 0x5f0f60, 129, 'a1bdfad1014966fa6e649e6030f17f27b402e458b4783448917356d983e75792'),
    ('timer_export', 0x5f1000, 60, 'fee6718032b86a17adab3260653832dfaa3b5146cca5f82a9cd9314899765d44'),
    ('timer_states', 0x6a1120, 16, '932c6b6ef70a45282459cfd1c644495844ccbc61bce051088513342093008dde'),
)


def u32(raw, offset=0):
    return struct.unpack_from('<I', raw, offset)[0]


def finite(*values):
    host.require(all(math.isfinite(v) and abs(v) <= 1e8 for v in values),
                 'Original HUD numeric field is nonfinite or out of capture bounds')


def score_fields(raw):
    host.require(len(raw) == 0x78, 'Original score header extent differs')
    count, capacity, used = u32(raw, 0x54), u32(raw, 0x50), u32(raw, 0x5c)
    multiplier = struct.unpack_from('<f', raw, 0x60)[0]
    finite(multiplier)
    host.require(0 <= used <= count <= 250 and count <= capacity <= 256 and multiplier >= 0 and raw[0x74] <= 1,
                 'Original score trick array or special flag is invalid')
    signed = lambda at: struct.unpack_from('<i', raw, at)[0]
    return dict(total_score_raw=signed(0xc), base_points=signed(0x14),
                native_combo_points=signed(0x20), multiplier=multiplier,
                trick_count=used, entry_count=count, special_points_raw=signed(0x70),
                special_active=bool(raw[0x74]))


def trick_fields(raw):
    host.require(len(raw) == 0xa0, 'Original trick allocation extent differs')
    result = dict(points_raw=struct.unpack_from('<i', raw)[0], flags_raw=u32(raw, 0x1c))
    # Stock writer56C472 uses entry+20 and56C4C6 uses entry+60, inside its
    # original160B allocation. Preserve native prefixes/control bytes verbatim.
    for name, at in (('name', 0x20), ('formatted_name', 0x60)):
        data = raw[at:at + 64]
        end = data.find(b'\0')
        host.require(end >= 0, 'Original trick string has no terminator inside its native field')
        result[name] = data[:end].decode('cp1252', errors='replace')
        result[name + '_bytes_hex'] = data[:end].hex()
    return result


def timer_fields(raw):
    host.require(len(raw) == 0x28, 'Original run timer extent differs')
    state = u32(raw, 0x18)
    remaining = struct.unpack_from('<f', raw, 0x1c)[0]
    finite(remaining)
    host.require(state < len(TIMER_STATES), 'Unknown original run timer state')
    # The native update may cross below zero on the expiration frame; retain it.
    return dict(state_raw=state, state_checksum=TIMER_STATES[state], seconds_remaining=remaining,
                counting_down=state == 1, expired=state == 3)


def balance_fields(raw, actor, component, score_component, input_component):
    host.require(len(raw) == 0x198, 'Original balance component extent differs')
    kind, doing = u32(raw, 0x18), raw[0x1c]
    host.require((kind == 0 or kind in KINDS) and doing <= 1, 'Unknown original balance kind or flag')
    result = dict(kind_checksum=kind, kind=KINDS[kind][0] if kind else None,
                  doing_balance_trick=bool(doing), raw_lean=None, raw_motion=None,
                  hud_normalized=None, meter_requested=False)
    if not kind:
        return result
    sub = raw[KINDS[kind][1]:KINDS[kind][1] + 0x58]
    host.require(struct.unpack_from('<5I', sub) ==
                 (0x698840, actor, component, score_component, input_component),
                 'Original balance subobject belongs to another actor/component')
    lean, motion = struct.unpack_from('<2f', sub, 0x30)
    controls = struct.unpack_from('<2I', sub, 0x44)
    finite(lean, motion)
    host.require(sub[0x55] <= 1, 'Original balance suppression flag differs')
    result.update(raw_lean=lean, raw_motion=motion, hud_normalized=lean * (-1 / 4096),
                  control_checksums=list(controls), meter_suppressed=bool(sub[0x55]),
                  meter_requested=all(controls) and not sub[0x55],
                  vertical_controls=all(v in (0xbc6b118f, 0xe3006fc4) for v in controls))
    return result


class HudSampler:
    def __init__(self, api, process, context, system, *, vm_sources=None):
        self.api, self.process, self.system = api, process, system
        self.worker = native_worker.retained_worker(api, process)
        self.actor, self.components = context['actor'], dict(context['components'])
        self.context, self.vm_sources = context, vm_sources
        self.arena = struct.unpack('<2I', system.read(0x84376c, 8))
        host.require(0x10000 <= self.arena[0] < self.arena[1] <= 0xffffffff and
                     all(k in self.components for k in (*COMPONENTS, WALK, 0x27d7cd28)),
                     'HUD requires the retained native actor/component arena')
        for name, address, size, digest in PINS:
            host.require(hashlib.sha256(system.read(address, size)).hexdigest() == digest,
                         'Original HUD observer witness differs: ' + name)
        self.actor_identity = self._actor_identity()

    def _arena_read(self, address, size):
        host.require(type(address) is int and address % 4 == 0 and 0 < size <= 65536 and
                     self.arena[0] <= address <= self.arena[1] - size,
                     'Original HUD object leaves its retained arena')
        return self.system.read(address, size)

    def _actor_identity(self):
        raw = self._arena_read(self.actor, 0x1c)
        host.require(u32(raw) == 0x6989e8 and
                     u32(self._arena_read(self.actor + 0x2b0, 4)) == self.components[SCORE],
                     'Original HUD actor/score identity changed')
        return raw[:4] + raw[0x18:0x1c]

    def _idle(self):
        self.worker.check_call_access()
        state = self.worker.snapshot()
        host.require(self.worker.thread and self.api.WaitForSingleObject(self.worker.thread, 0) == 258 and
                     state[native_worker.STATE // 4] == native_worker.IDLE and
                     state[native_worker.TID // 4] == self.worker.tid and
                     state[native_worker.COUNT // 4] == self.worker.count,
                     'HUD sampling requires its idle live native worker')
        vm = probe_vm_idle.require_idle(self.system, self.context, self.vm_sources)
        clock = self.system.read(0x8459c8, 16)
        host.require(struct.unpack('<2Q', clock)[0] == struct.unpack('<2Q', clock)[1],
                     'HUD sampling must occur between complete original frame/VM/event phases')
        host.require(struct.unpack('<2I', self.system.read(0x84376c, 8)) == self.arena and
                     self._actor_identity() == self.actor_identity, 'Original HUD arena/actor lifetime changed')
        return self.worker.count, clock, vm

    def read(self):
        before = self._idle()
        captured = []

        def read(address, size):
            raw = self._arena_read(address, size)
            captured.append((address, raw))
            return raw

        components = {}
        for key, (vtable, size) in COMPONENTS.items():
            raw = read(self.components[key], size)
            host.require((u32(raw), u32(raw, 4), u32(raw, 0xc)) == (vtable, key, self.actor),
                         'Original HUD component type/owner changed')
            components[key] = raw
        host.require(u32(components[TIMER], 0x24) == self.components[WALK],
                     'Original run timer lost its native Walk component')
        score_address = u32(components[SCORE], 0x18)
        score = read(score_address, 0x78)
        score_result = score_fields(score)
        count = score_result['entry_count']
        entries = []
        if count:
            pointers = struct.unpack('<' + 'I' * count, read(u32(score, 0x4c), count * 4))
            host.require(len(set(pointers)) == count, 'Original score trick-pointer list contains aliases')
            entries = [trick_fields(read(address, 0xa0)) for address in pointers]
        score_result['entries'] = entries
        result = dict(native_frame=struct.unpack_from('<Q', before[1], 8)[0], score=score_result,
                      combo_timer=timer_fields(components[TIMER]),
                      balance=balance_fields(components[BALANCE], self.actor, self.components[BALANCE],
                                             self.components[SCORE], self.components[0x27d7cd28]))
        host.require(all(self._arena_read(address, len(raw)) == raw for address, raw in captured) and
                     self._idle() == before, 'Original HUD changed during its coherent snapshot')
        return result
