"""Stock gameplay options applied between original simulation frames.

The original Change native deep-copies a retained CArray to enable/disable the
stock aerial table. Original movement, flip timing and scoring stay in THAW.
There is no reTHAWed style selector in this stock executable.
"""
import hashlib
import struct

import probe_handoff
import probe_loader as host
import probe_profile
import probe_vm_idle
from import_qb import qbkey
from probe_frame_input import packet, validate_packet
from perfect_balance import PerfectBalance, PINS as BALANCE_PINS

ABILITY, AERIAL = 0x90af6e6f, 0xb75d73e5
SOURCE = 'scripts/game/skater/airtricks.qb.wpc'
CAMERA_MODES = (0xf402ade7, 0xe89ad5aa, 0x2bb20192, 0x58c8868c)
GRIND_ZOOM = 0x5a7f5cc5
PINS = {
    **BALANCE_PINS,
    'Change': ('GameplayOptionsChange', 0x485c50, 720, 'ac222299515b59df5bb1a5122d3582f7c4656dee6c8ee8d736cd372ad8e3183e'),
    'ArrayAllocate': ('GameplayOptionsArrayAllocate_entry', 0x40ac70, 6, '4e8c2c0e955ec56e7d0c5a74628aab2169cc89e8236ae6a19e86e4038092e571'),
    'ArrayCtor': ('GameplayOptionsArrayCtor', 0x40a160, 17, '616d7123275b9f8f43729383efda57d3f5b216cbbfec8762781d4fca2966271c'),
    'ArrayCopy': ('GameplayOptionsArrayCopy_entry', 0x416ed0, 6, 'f45985438ccd3fa7b2c42a5798885a2872759faffe40e73932d399348cdc1aa9'),
    'AddArray': ('GameplayOptionsAddArray', 0x416150, 114, '49ce1c8098838d4c158eb30a887ce5b38859177d3b37dd420c96b75f15741483'),
    'EnableAll': ('GameplayOptionsEnableAllAbilities', 0x5c21b0, 59, 'a8e5cf07ec50ad423bcd18c689c601524b8d253be67e0e554bfd97128ed7eee0'),
    'AbilityQuery': ('GameplayOptionsAbilityQuery', 0x5c2180, 39, '445e6ec594f97f6641ee31dc247ac3b797154366d1326968e63e9bdc0a046e70'),
    'AbilityRefresh': ('GameplayOptionsAbilityRefresh', 0x5f6b70, 83, '8fbf5e6e286380e5e0c75922bb6a5b2a900b15e67b991610708b9ca1ce420d05'),
    'AbilityVtable': ('GameplayOptionsAbilityVtable', 0x6a0ad0, 48, '82ca0ff0801cf9fb88cc914ead13f5397cafd054757a28a3d3e3fa170a20957e'),
    'AerialSolver': ('GameplayOptionsOriginalAerialSolver', 0x5ce150, 1445, 'bd5902cae1c75d8147790dd5c920ba0be3464d32ad365cb25af808403d123a19'),
    'CameraRefresh': ('GameplayOptionsCameraRefresh', 0x5c5320, 22, '3fb6bc9fda95e2ff56238d811ad9f6350dc32baf79835707e10d71e455f8a442'),
    'CameraMode': ('GameplayOptionsCameraMode', 0x5c4380, 1064, '6afe3416458b8260dc17cda8bd28b6a30d82044d8fc1a42046c3dd94749d2e1f'),
    'CameraVtable': ('GameplayOptionsCameraVtable', 0x6a0b9c, 48, 'fb7a242821acc707abef6cf5293ca2b2d2cc3525e57261e966276f7b0e746ea4'),
    'AddInteger': ('GameplayOptionsAddInteger', 0x415d30, 114, '676c92ac3d9aa18d59dea75b7b8af629c18e0835b2c7a2ae8f6b8bf1295af3c8'),
    'StructAllocate': ('GameplayOptionsStructAllocate', 0x40ac30, 64, 'c080fedf2249d6a455ef44ef6f3a8bc247d5a0f381905ff3baa110af4429acb9'),
    'StructCopy': ('GameplayOptionsStructCopy', 0x416a80, 20, '327c07307bb9a6dcddb98d3fc117e60c83ceca1dbffb1a74ac642578b09aada3'),
    'StructCopyBody': ('GameplayOptionsStructCopyBody', 0x4169f0, 144, 'f8af68f42a69b10a53d7e1e7274392382cd2aa05fa8dafa70e4886a31ae7bfd0'),
    'AddStructure': ('GameplayOptionsAddStructure_entry', 0x4161d0, 6, '3b468ab5ca524af95548c87ddbbe22fb2a19d97cfedd0e1bd173cda4a1a441a9'),
    **{key: probe_handoff.PINS[key] for key in ('StructCtor', 'StructDtor', 'AddChecksum', 'AddFloat', 'GetFloat')},
    **{key: probe_profile.PINS[key] for key in ('GlobalArray', 'GlobalStructure', 'ArrayPointer', 'GetStat', 'Current')},
    'Lookup': host.QB_PINS['lookup'],
}


def validate_enabled(enabled):
    host.require(type(enabled) is bool, 'Aerial flips must be explicitly enabled or disabled')
    return enabled


def aerial_direction(raw):
    """Pitch/roll use the authored stock signs; axes and camera stay untouched."""
    validate_packet(raw)
    x = -1 if raw[2] < 96 else 1 if raw[2] > 160 else 0
    y = -1 if raw[3] < 96 else 1 if raw[3] > 160 else 0
    return x, y


class AerialGesture:
    """One Grab press becomes THAW's direction double tap while holding Grab.

    The native decoder merges the movement stick into its D-pad, so two brief
    stick releases are required to create actual direction edges. Camera axes
    stay intact; physics, rotation, animation and scoring remain native.
    Call once per actual source frame, never while retrying its GTA delivery.
    """
    def __init__(self, prepare):
        self.prepare = prepare
        self.reset()

    def reset(self):
        self.grab_down, self.pending = False, None

    def map(self, raw, sample, *, enabled=True):
        validate_packet(raw)
        mask = struct.unpack_from('<I', raw, 0x24)[0]
        grab, direction = bool(mask & 0x20), aerial_direction(raw)
        airborne = (sample.get('mode') == 0 and sample.get('walking_gate') == 0 and
                    sample.get('state') == 1)
        if not enabled or not airborne:
            self.pending = None
        elif grab and not self.grab_down and any(direction) and self.pending is None:
            if self.prepare(direction):
                x, y = direction
                bit = (0x4000 if y > 0 else 0x1000) if y else (0x8000 if x < 0 else 0x2000)
                self.pending = (bit, 0)
        self.grab_down = grab
        if self.pending is None:
            return raw
        bit, phase = self.pending
        mask = (mask & ~bit) | (bit if phase in (1, 3) else 0) | 0x20
        axes = tuple(raw[:4]) if phase not in (0, 2) else (*raw[:2], 128, 128)
        self.pending = (bit, phase + 1) if phase < 4 else None
        return packet(axes, mask)


class GameplayOptions:
    def __init__(self, api, process, record, deadline, log, context, system, *, vm_sources):
        self.api, self.process, self.record, self.deadline = api, process, record, deadline
        self.log, self.context, self.system, self.sources = log, context, system, vm_sources
        self.params, self.enabled, self.grind_camera_lowering = {}, True, False
        self.directional_active = False
        self.camera_params = []
        self.scratch = system.allocate(bytes(4))
        for name, va, size, digest in PINS.values():
            host.require(hashlib.sha256(system.read(va, size)).hexdigest() == digest,
                         'Original gameplay option pin differs: ' + name)
        self.profile = self._call('Current', this=system.word(0x83e894))
        self._validate()
        self.camera_modes = [self._call('GlobalStructure', (key, 1)) for key in CAMERA_MODES]
        host.require(all(self._float(mode, GRIND_ZOOM) == 1. for mode in self.camera_modes),
                     'Original standard camera modes have a different grind zoom')
        self.camera = self._camera()
        # Parsed QB structs alias the loaded source; replacing a member in
        # place would incorrectly free that source memory. Work on owned
        # native copies and let Change replace the globals safely.
        for key, original_mode in zip(CAMERA_MODES, self.camera_modes):
            params = system.allocate(bytes(8))
            self._call('StructCtor', this=params)
            mode = self._call('StructAllocate', (8, 1))
            host.require(mode and self._call('StructCtor', this=mode) == mode,
                         'Original camera option structure was not constructed')
            self._call('StructCopy', (original_mode,), mode)
            self._call('AddChecksum', (0x60f6f242, key), params)
            self._call('AddStructure', (0x4b40630d, mode), params)
            self.camera_params.append((params, mode))
        source, size = vm_sources[SOURCE]
        symbol = self._call('Lookup', (AERIAL,))
        host.require(size == 42100 and system.read(source, 1) == b'\1' and
                     struct.unpack('<4I', system.read(symbol, 16))[1:3] == (AERIAL, qbkey(SOURCE)),
                     'Original aerial table source differs')
        original = self._call('GlobalArray', (AERIAL, 1))
        host.require(system.word(original) >> 16 & 255 == 10 and system.word(original + 4) == 8,
                     'Original aerial table is not the eight authored stock entries')
        for enabled in (True, False, 'directional'):
            params = system.allocate(bytes(8))
            host.require(self._call('StructCtor', this=params) == params,
                         'Original gameplay option parameters were not constructed')
            array = self._call('ArrayAllocate', (12, 1))
            host.require(array and self._call('ArrayCtor', this=array) == array,
                         'Original gameplay option array was not constructed')
            if enabled:
                self._call('ArrayCopy', (array, original, 0))
                host.require(system.word(array + 4) == 8 and array != original,
                             'Original aerial table was not independently copied')
            self._call('AddChecksum', (0x60f6f242, AERIAL), params)
            self._call('AddArray', (0x4b40630d, array), params)
            self.params[enabled] = params
            if enabled == 'directional':
                self.directional_rows = [self._call('ArrayPointer', (index,), array) for index in (0, 2, 4, 6)]
        ability = context['components'][ABILITY]
        if system.read(ability + 0x18, 34) != bytes([1]) * 34:
            host.require(self._call('EnableAll', (0, 0), ability) == 1,
                         'Original ability unlock rejected the initialized skater')
        host.require(system.read(ability + 0x18, 34) == bytes([1]) * 34,
                     'Original skater did not retain all 34 unlocked abilities')
        self._validate()
        log('gameplay_options_ready', aerial_flips=True, ability_count=34,
            stats=[10] * 10, original_aerial_entries=8, native_array_copies=True,
            original_physics=True, rethawed_style_options=False)
        self.gesture = AerialGesture(self._prepare_direction)
        self.balance = PerfectBalance(system, self._call, self._validate)

    def _call(self, name, arguments=(), this=None):
        return host.native_call(self.api, self.process, PINS[name], list(arguments),
                                self.record, self.deadline, self.log, this)

    def _float(self, owner, key):
        host.require(self._call('GetFloat', (key, self.scratch, 1), owner) & 255,
                     'Original gameplay option float is absent')
        return struct.unpack('<f', self.system.read(self.scratch, 4))[0]

    def _camera(self):
        s, owner = self.system, self.context['camera']
        node, seen, camera = s.word(owner + 0x11c), set(), 0
        while node:
            host.require(node not in seen and len(seen) < 64 and s.word(node + 12) == owner,
                         'Original camera component list ownership differs')
            seen.add(node)
            if s.word(node + 4) == 0x5e43a604:
                host.require(not camera and s.word(node) == 0x6a0b9c,
                             'Original skater camera class is absent or duplicated')
                camera = node
            node = s.word(node + 16)
        host.require(camera and s.word(camera + 0x184) == self.context['actor'] and
                     s.word(camera + 0x188) == self.context['components'][0x43686585] and
                     s.word(0x6d6318) == 0x5c5320,
                     'Original skater camera target or refresh registration differs')
        return camera

    def _validate(self):
        s, context = self.system, self.context
        s.worker.check_call_access()
        probe_vm_idle.require_idle(s, context, self.sources)
        clock = s.read(0x8459c8, 16)
        host.require(clock[:8] == clock[8:], 'Gameplay options require a completed native frame')
        actor, ability = context['actor'], context['components'][ABILITY]
        host.require(s.word(actor) == 0x6989e8 and s.word(ability) == 0x6a0ad0 and
                     s.word(ability + 4) == ABILITY and s.word(ability + 12) == actor and
                     s.word(ability + 0x3c) == context['components'][0x4ff8dac6],
                     'Original ability component ownership differs')
        host.require(s.read(actor + 0x2c8, 40) == struct.pack('<10f', *([10.] * 10)) and
                     self._call('Current', this=s.word(0x83e894)) == self.profile and
                     [self._call('GetStat', (key,), self.profile) for key in probe_profile.STATS] == [10] * 10,
                     'Original skater and profile must retain all ten statistics at 10/10')
        return clock

    def set_aerial_flips(self, enabled):
        validate_enabled(enabled)
        clock = self._validate()
        if self.enabled == enabled:
            return dict(aerial_flips=enabled, changed=False, deferred=False)
        core = self.context['components'][0x5bd63b29]
        # An active flip retains an Extra array inside the current global table.
        # Changing the table then would free data still owned by the original move.
        if self.system.read(core + 0x4ef, 1) != b'\0':
            return dict(aerial_flips=self.enabled, changed=False, deferred=True,
                        message='Finish the current aerial flip before changing this option')
        host.require(self._call('Change', (self.params[enabled], 0)) & 255 == 1,
                     'Original Change rejected the aerial flip table')
        array = self._call('GlobalArray', (AERIAL, 1))
        host.require(self.system.word(array + 4) == (8 if enabled else 0),
                     'Original aerial option readback differs')
        self.enabled = enabled
        self.directional_active = False
        self.gesture.reset()
        host.require(self._validate() == clock, 'Gameplay option changed the simulation clock')
        result = dict(aerial_flips=enabled, changed=True, deferred=False, original_physics=True,
                      stats=[10] * 10, host_game_fields_written=False)
        self.log('gameplay_options_applied', **result)
        return result

    def _flipping(self):
        return self.system.read(self.context['components'][0x5bd63b29] + 0x4ef, 1) != b'\0'

    def _prepare_direction(self, direction):
        if self._flipping():
            return False
        self._validate()
        x, y = direction
        selected = (0 if y > 0 else 1) if y else (2 if x < 0 else 3)
        for index, (row, default) in enumerate(zip(self.directional_rows,
                                                  ((-360, 0), (360, 0), (0, -360), (0, 360)))):
            pitch, roll = (-y * 360, x * 360) if index == selected else default
            for key, value in ((0x0affd09d, pitch), (0xe4f1b1b1, roll)):
                self._call('AddInteger', (key, value & 0xffffffff), row)
        host.require(self._call('Change', (self.params['directional'], 0)) & 255,
                     'Original Change rejected the directional aerial table')
        self.directional_active = True
        self.log('aerial_gesture_prepared', direction=list(direction), pitch_degrees=-y*360,
                 roll_degrees=x*360, original_rotation_solver=0x5ce150,
                 original_score_and_extra_mappings_preserved=True)
        return True

    def map_input(self, raw, sample):
        if self.directional_active and self.gesture.pending is None and not self._flipping():
            self._validate()
            host.require(self._call('Change', (self.params[self.enabled], 0)) & 255,
                         'Original aerial table restoration failed')
            self.directional_active = False
        return self.gesture.map(raw, sample, enabled=self.enabled)

    def reset_input(self):
        self.gesture.reset()

    def set_grind_camera_lowering(self, enabled):
        validate_enabled(enabled)
        clock = self._validate()
        if self.grind_camera_lowering == enabled:
            return dict(grind_camera_lowering=enabled, changed=False)
        host.require(self._camera() == self.camera, 'Original skater camera identity changed')
        s, actor = self.system, self.context['actor']
        before, mode = s.read(actor, 0x35c), s.word(self.camera + 0x24)
        host.require(0 <= mode < len(CAMERA_MODES), 'Unsupported original skating camera mode')
        # Stock has no reTHAWed balance-camera switch. Its native grind zoom
        # scales both camera height and distance and retains collision solving.
        bits = struct.unpack('<I', struct.pack('<f', .8 if enabled else 1.))[0]
        for key, (params, pointer) in zip(CAMERA_MODES, self.camera_params):
            self._call('AddFloat', (GRIND_ZOOM, bits), pointer)
            host.require(struct.unpack('<I', struct.pack('<f', self._float(pointer, GRIND_ZOOM)))[0] == bits,
                         'Original grind camera parameter readback differs')
            host.require(self._call('Change', (params, 0)) & 255,
                         'Original Change rejected the owned camera option copy')
            current = self._call('GlobalStructure', (key, 1))
            host.require(struct.unpack('<I', struct.pack('<f', self._float(current, GRIND_ZOOM)))[0] == bits,
                         'Original global camera option readback differs')
        host.require(self._call('CameraRefresh', (0, 0), self.camera) == 1 and
                     s.word(self.camera + 0x24) == mode and s.word(self.camera + 0x12c) == bits,
                     'Original camera refresh did not retain its mode and requested grind zoom')
        host.require(s.read(actor, 0x35c) == before and self._validate() == clock,
                     'Camera setting changed skater gameplay or simulation time')
        self.grind_camera_lowering = enabled
        result = dict(grind_camera_lowering=enabled, changed=True,
                      original_grind_zoom=.8 if enabled else 1., original_camera_collision=True)
        self.log('gameplay_camera_option_applied', **result)
        return result

    def close(self):
        self.balance.set_enabled(False)
        self._validate()
        for params in self.params.values():
            self._call('StructDtor', this=params)
        for params, _ in self.camera_params:
            self._call('StructDtor', this=params)
        self.params.clear()
        self.camera_params.clear()

    def set_infinite_balance(self, enabled):
        result = self.balance.set_enabled(enabled)
        if result['changed']:
            self.log('gameplay_balance_option_applied', **result)
        return result
