"""Declared host entry state through original stock skater commands.

Call only between FrameDriver steps, after the complete native startup. Inputs
are THAW inches, inches/second and THAW Y-axis degrees. This helper does not
convert GTA coordinates, reset all components, advance a gameplay frame, or
write actor/physics/camera fields. MoveToNode(NoReset) runs its genuine camera
owner update and teleport notifications. A native failure ends the owned child.
"""
import hashlib
import math
import struct

import probe_loader as host
import probe_vm_idle

PINS = {
    'State': ('OriginalHandoffState', 0x5de730, 253, '3122ec273553aa99032ab9570ab29bc692a2c7757f64dc74a1267b68ac85b34c'),
    'Orientation': ('OriginalHandoffOrientation', 0x42fa30, 1026, 'cde9b6b1bebb253741c66d57d7fce9107d5cdc1a5f671e1d0a5731eb97441333'),
    'Position': ('OriginalHandoffPosition', 0x42eb40, 53, '6627011ec1aa32313bede35ab686e2e6b01232c26087f51b1d267f31c7355e08'),
    'MoveToNode': ('OriginalHandoffMoveToNode', 0x592480, 2365, 'dfb1abe4781fcf40910c5423837f4fe987e8f57a75cf8593d20b128a11bea6f2'),
    'Velocity': ('OriginalHandoffVelocity', 0x5ab530, 428, '1569f2e068d52667550ef9e5f3295b7fbfe5ed7da2667bbbc1dac210fe840709'),
    'StructCtor': ('OriginalHandoffStructCtor', 0x414230, 11, '219b92061b08d719b045423de28a365462f20df22e2ca28f240d235c37052a90'),
    'StructDtor': ('OriginalHandoffStructDtor', 0x4154b0, 5, '52db92b0d66a3064cd038a8e8a7d3796861fed5b7a3dcb5308c0425f77712290'),
    'StructClear': ('OriginalHandoffStructClear', 0x4152d0, 147, 'f23b53dba87191e649a66817212cb7591e3991ac5388a779046eb5ebddc3f331'),
    'AddFloat': ('OriginalHandoffAddFloat', 0x415de0, 118, '47393dff085a51079dfb5a8cf609ca60ad40d30510cbcd2999617b485387d3f8'),
    'GetFloat': ('OriginalHandoffGetFloat', 0x4146c0, 73, '39a0f11db05fd823f459d77c05bfa0ac9a6abf5028870daac29220b269b65acd'),
    'AddVector': ('OriginalHandoffAddVector', 0x415f40, 224, '69c5d1e5c8807c9fa6e76588e6f1f3697c889ed2e83b97322214173ea4574979'),
    'GetVector': ('OriginalHandoffGetVector', 0x414770, 105, '2cae740f5825056161bea4f0a69facec453e4f8819331c415ace9bad1d83df42'),
    'AddChecksum': ('OriginalHandoffAddChecksum', 0x415e90, 114, '6bb987885e7275c71b88d5645baaf83d9afdb9e2481e560d201cc71fa2cbad8e'),
    'ContainsFlag': ('OriginalHandoffContainsFlag', 0x414c90, 107, '63d2a04be38784c7c77c4b9077908de99773f4d9f8451e3e74651f5065b731b8'),
    'CoreHistory': ('OriginalHandoffCoreHistory', 0x5ca720, 614, '05e9267a3a07b61ec70bf4fad0753803d4e594a67a15454563c8bb28763e552b'),
    'MotionMove': ('OriginalHandoffMotionMove', 0x458e90, 222, '981d50f607e3708aeaf627ff5a5af681c631d904560040dc875b4e30211d842a'),
    'MarkTeleported': ('OriginalHandoffMarkTeleported', 0x4304b0, 52, '278df7e15c242122121292b87c0d7411eee78f0cf2a70602b6738fe6d9bd65fd'),
    'CameraOwner': ('OriginalHandoffCameraOwner', 0x58f4a0, 32, 'adc058844fc89a71fba0beaa9673d7a22ddbb4f9bd957ec0e4ce0484aed3c5fd'),
    'CameraOwnerUpdate': ('OriginalHandoffCameraOwnerUpdate', 0x42f590, 187, '2378e2ab9110af2d930ec43894179cff83ed0f3c944c39307ffaf7172d0124c2'),
    'CoreStateTransition': ('OriginalHandoffCoreStateTransition', 0x5c8880, 611, 'de6b2a0dfd84ba2273c25a33844c130cfa07f566fd39f358fd65d128cf1e159d'),
    'PositionPublication': ('OriginalHandoffPositionPublication', 0x685bd3, 10, '04c43c145b227fc37dccca093c1b54121daccd050610e70cd2206dc991bfa78c'),
    'OrientationPublication': ('OriginalHandoffOrientationPublication', 0x685be7, 10, 'bba00255a4da78d529588ca638889dec9a375c4d17cd955750c1da748ea434d2'),
    'StatePublication': ('OriginalHandoffStatePublication', 0x68a947, 10, '085b106925ce210d8ad3149235de1c7e9ac0eadebe31659fad809ca36c5436d0'),
    'ActorVtable': ('OriginalHandoffActorVtable', 0x6989e8, 40, '49a3772898fedfb7ea1e1ad54c0f8f54975477f165649fe0f2dcd3473585d8a1'),
    'VelocityRegistration': ('OriginalHandoffVelocityRegistration', 0x6d5008, 8, 'f768338faf8b8a330baab9ac49094304de4cc47b04aa098d89f4665b3f908dac'),
}
NULL_SCRIPT = 259952684
POSITION_KEY, YAW_KEY = 0xb9d31b0a, 0x0424d9ea
VELOCITY_KEYS = (0x6187906d, 0x1680a0fb, 0x8f89f141)


def validate_request(position, velocity, yaw, grounded):
    host.require(type(grounded) is bool, 'Handoff grounded state must be explicit')
    for name, values, limit in (('position', position, 10000000), ('velocity', velocity, 100000)):
        host.require(isinstance(values, (list, tuple)) and len(values) == 3 and
                     all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= limit for v in values),
                     'Handoff requires three bounded finite THAW ' + name + ' values')
    host.require(type(yaw) in (int, float) and math.isfinite(yaw) and abs(yaw) <= 360,
                 'Handoff yaw must be finite THAW degrees within one revolution')
    canonical = lambda values: struct.unpack('<' + 'f' * len(values), struct.pack('<' + 'f' * len(values), *values))
    return canonical(position), canonical(velocity), canonical([yaw])[0], 0 if grounded else 1


def validate_pose(snapshot, position, velocity, yaw, state, control_mode):
    expected_position = struct.pack('<4f', *position, 1.0)
    host.require(all(snapshot[key] == expected_position for key in ('position', 'previous', 'core_previous')),
                 'Original handoff position history differs from the declared position')
    host.require(snapshot['velocity'] == struct.pack('<4f', *velocity, 1.0),
                 'Original handoff did not retain declared three-axis momentum')
    host.require(snapshot['matrix'] == snapshot['display'] == snapshot['core_matrix'],
                 'Original handoff physics/display/Core orientations disagree')
    matrix = struct.unpack('<16f', snapshot['matrix'])
    angle = math.radians(yaw)
    expected = (math.cos(angle), 0, -math.sin(angle), 0,
                0, 1, 0, 0, math.sin(angle), 0, math.cos(angle), 0, 0, 0, 0, 1)
    host.require(all(math.isfinite(a) and abs(a - b) < 0.0001 for a, b in zip(matrix, expected)),
                 'Original handoff orientation differs from the declared upright Y heading')
    host.require(snapshot['state'] == snapshot['sound_state'] == state and snapshot['control_mode'] == control_mode,
                 'Original handoff state/sound/control ownership differs')


def run(api, process, record, deadline, log, context, system, positionTHAW, velocityTHAW, yaw, grounded, *, vm_sources=None):
    position, velocity, yaw, state = validate_request(positionTHAW, velocityTHAW, yaw, grounded)
    read, word = system.read, system.word
    pins = {**host.VM_PINS, **PINS}
    actor, components, manager = context['actor'], context['components'], 0x842b18
    start, end = word(0x84376c), word(0x843770)

    def words(address, count):
        host.require(address % 4 == 0 and start <= address <= end - count * 4,
                     'Handoff native object leaves its original arena')
        return struct.unpack(f'<{count}I', read(address, count * 4))

    def call(name, args=(), this=None):
        return host.native_call(api, process, pins[name], list(args), record, deadline, log, this)

    def check_pins():
        for name, va, size, digest in PINS.values():
            host.require(hashlib.sha256(read(va, size)).hexdigest() == digest, 'Handoff stock pin differs: ' + name)

    def idle(phase):
        try:
            evidence = probe_vm_idle.require_idle(system, context, vm_sources)
        except (RuntimeError, OSError, ValueError):
            # Read-only evidence before rejecting; never retry a command or clear
            # the current-script publication left by an original return 5.
            try:
                evidence = probe_vm_idle.snapshot(system, context, vm_sources)
            except (RuntimeError, OSError, ValueError) as error:
                evidence = dict(admissible=False, reason=str(error), native_calls=0, host_vm_context_modified=False)
            log('handoff_vm_diagnostic', after=phase, **evidence)
            raise
        if evidence['waiting_owner_context']:
            log('handoff_vm_waiting', after=phase, **evidence)
        return evidence

    def registry():
        expected = word(0x6dcf90)
        host.require(expected <= 4096, 'Handoff original script registry is oversized')
        pointer, previous, entries, identities = word(0x6dcf8c), 0, {}, set()
        while pointer:
            host.require(pointer not in entries and len(entries) < expected, 'Handoff script registry cycles')
            row = words(pointer, 63)
            host.require(row[2] == previous and row[44] not in identities, 'Handoff script backlinks/IDs differ')
            entries[pointer] = row[44]
            identities.add(row[44])
            previous, pointer = pointer, row[1]
        host.require(len(entries) == expected, 'Handoff original script count differs')
        return entries

    def stack():
        depth, current = word(manager + 0x10c), word(manager + 0xc8)
        host.require(word(0x84388c) == manager and depth < 16 and current ==
                     (manager + 0xc8 + depth * 4 if depth else 0), 'Handoff original heap stack differs')
        return depth, current

    def ownership():
        owner = words(actor, 0x35c // 4)
        host.require(owner[0] == 0x6989e8 and owner[3] == word(0x6dd6b0) and
                     owner[0xd8 // 4] & 0xff == 1 and owner[0x2c8 // 4:0x2f0 // 4] == (0x41200000,) * 10,
                     'Handoff requires the genuine local skater with ten maximum stats')
        pointer, actual = owner[0x11c // 4], {}
        while pointer:
            row = words(pointer, 6)
            host.require(len(actual) < 64 and row[1] not in actual and row[3] == actor and
                         0x68e000 <= row[0] < 0x6bc000, 'Handoff component ownership or chain differs')
            actual[row[1]], pointer = pointer, row[4]
        host.require(actual == components, 'Handoff component graph differs from the constructed actor')
        core, control = components[0x5bd63b29], components[0xd9151d4d]
        host.require(owner[0x2b8 // 4] == core and owner[0x2bc // 4] == control and
                     word(core + 0x192c) == components[0x43686585], 'Handoff native Core links differ')
        for offset, key in ((0x1910, 0x27d7cd28), (0x1914, 0xe594f0a2), (0x1918, 0x187574fa),
                            (0x191c, 0x270f56e1), (0x1930, 0x408d2aa9)):
            host.require(word(core + offset) == components[key], 'Handoff Core dependency differs')
        return core, control

    def snapshot():
        return dict(position=read(actor + 0x68, 16), previous=read(actor + 0x78, 16),
                    core_previous=read(core + 0xa8, 16), velocity=read(actor + 0xc8, 16),
                    matrix=read(actor + 0x88, 64), display=read(actor + 0xdc, 64), core_matrix=read(core + 0x18, 64),
                    state=word(components[0x43686585] + 0x18), sound_state=word(components[0x187574fa] + 0x18),
                    control_mode=word(control + 0x1c))

    check_pins()
    idle('entry')
    core, control = ownership()
    before, scripts_before, heap_before = snapshot(), registry(), stack()
    host.require(struct.unpack('<3I', read(0x6d6958, 12)) == (0x948ebf96, 0x5bd63b29, 0x5de730) and
                 word(0x6bd8d0) == 0x42eb40 and word(0x6bd900) == 0x42fa30 and
                 word(0x6989e8 + 12) == 0x592480 and call('CameraOwner', this=actor) == context['camera'],
                 'Handoff original publications or actual camera owner differ')
    log('handoff_begin', actor=actor, position_thaw=position, velocity_thaw=velocity, yaw_degrees=yaw,
        grounded=grounded, state_before=before['state'], control_mode=before['control_mode'], host_actor_writes=False)
    script_heap = word(manager + 0x128)
    words(script_heap, 12)
    call('HeapContextPush', (script_heap,), manager)
    script = call('ScriptAllocate', (0xfc, 1))
    words(script, 63)
    host.require(call('ScriptCtor', this=script) == script, 'Handoff temporary Script constructor failed')
    call('ScriptSetSource', (NULL_SCRIPT, 0, 0, actor), script)
    call('HeapContextPop', this=manager)
    host.require(stack() == heap_before and word(script + 0xc4) == actor and word(script + 0xd0) == NULL_SCRIPT and
                 registry() == {**scripts_before, script: word(script + 0xb0)}, 'Handoff temporary owner Script differs')
    owned = system.allocate(bytes(128))
    params, scratch = owned, owned + 32
    host.require(call('StructCtor', this=params) == params and read(params, 8) == bytes(8),
                 'Handoff original parameter constructor failed')

    def add_float(key, value):
        bits = struct.unpack('<I', struct.pack('<f', value))[0]
        call('AddFloat', (key, bits), params)
        host.require(call('GetFloat', (key, scratch, 0), params) & 0xff and word(scratch) == bits,
                     'Handoff original float parameter differs')

    # Grind exits can restore a previous position; state must precede the host position.
    checksum = 0x58007c97 if grounded else 0x439f4704
    call('AddChecksum', (0, checksum), params)
    host.require(call('StructGetChecksum', (0, scratch, 0), params) & 0xff and word(scratch) == checksum,
                 'Handoff original state parameter differs')
    host.require(call('State', (params, script), core) == 1, 'Original SetState rejected the handoff')
    log('handoff_state', state=word(components[0x43686585] + 0x18), current_script=word(0x6dcf84))
    idle('State')
    call('StructClear', this=params)
    add_float(YAW_KEY, yaw)
    host.require(call('Orientation', (params, script), actor) & 0xff, 'Original SetOrientation rejected the handoff')
    call('StructClear', this=params)
    bits = struct.unpack('<3I', struct.pack('<3f', *position))
    call('AddVector', (POSITION_KEY, *bits), params)
    host.require(call('GetVector', (POSITION_KEY, scratch, 0), params) & 0xff and
                 read(scratch, 16) == struct.pack('<4f', *position, 1.0), 'Handoff original vector parameter differs')
    host.require(call('Position', (params, script), actor) & 0xff, 'Original SetPosition rejected the handoff')
    call('AddChecksum', (0, 0xd607e2e6), params)
    host.require(call('ContainsFlag', (0xd607e2e6,), params) & 0xff and
                 call('ContainsFlag', (0x90a91232,), params) & 0xff == 0 and
                 call('StructGetChecksum', (0xa1dc81f9, scratch, 0), params) & 0xff == 0,
                 'Handoff requires genuine NoReset with neither Name nor orient')
    velocity_before_history = read(actor + 0xc8, 16)
    host.require(call('MoveToNode', (0x8819dd8b, params, script), actor) & 0xff and
                 read(actor + 0xc8, 16) == velocity_before_history,
                 'Original NoReset history command failed or unexpectedly changed momentum')
    idle('MoveToNode')
    call('StructClear', this=params)
    for key, value in zip(VELOCITY_KEYS, velocity):
        add_float(key, value)
    actor_before_velocity = read(actor, 0x35c)
    host.require(call('Velocity', (params, script)) & 0xff, 'Original SetSkaterVelocity rejected the handoff')
    actor_after_velocity = read(actor, 0x35c)
    host.require(actor_before_velocity[:0xc8] == actor_after_velocity[:0xc8] and
                 actor_before_velocity[0xd8:] == actor_after_velocity[0xd8:],
                 'Original velocity command changed unrelated actor fields')
    call('StructDtor', this=params)
    host.require(read(params, 8) == bytes(8), 'Original handoff parameter destructor did not clear its fields')
    call('ScriptDtor', this=script)
    call('ScriptFree', (script,))
    scripts_after = registry()
    host.require(script not in scripts_after and stack() == heap_before, 'Handoff temporary script or heap context remained')
    idle('cleanup')
    host.require(ownership() == (core, control), 'Handoff changed actor component ownership')
    after = snapshot()
    validate_pose(after, position, velocity, yaw, state, before['control_mode'])
    check_pins()
    log('handoff_pass', actor=actor, position_thaw=position, velocity_thaw=velocity, yaw_degrees=yaw,
        native_state=after['state'], sound_state=after['sound_state'], control_mode=after['control_mode'],
        original_history_and_camera_path=True, position_history_equal=True, no_reset_flag=True,
        native_camera_owner_update=True, actor_physics_frames=0, max_stats_preserved=True,
        scripts_before=len(scripts_before), scripts_after=len(scripts_after), host_actor_writes=False,
        whole_original_commands=True, return_to_gta_tested=False, repeated_midair_switch_tested=False)
    return dict(position=position, velocity=velocity, yaw=yaw, state=state, actor=actor)
