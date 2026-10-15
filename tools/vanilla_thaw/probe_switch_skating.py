"""Return a walking actor to skating through its complete original script route.

Explicit, opt-in preparation before probe_handoff: no default handoff behavior
is changed. The stock walking reset caller starts switch_ongroundai with
newscript=ongroundai on the actor's own Script. Its authored cleanup restores
board, animation, exceptions and sounds as well as PhysicsControl/camera mode.
Calling only WalkingToSkating changes physics but leaves walking animations.
Only Ground/Air walking is admitted; this is not a reset for ledges or ladders.
"""
import hashlib
import math
import struct

import probe_loader as host
import probe_handoff
import probe_vm_idle
from import_qb import qbkey
from motion_contact import classify

MEMBER = 0x5c038f9b
SCRIPT, NEW_SCRIPT, ON_GROUND = 0x93a4e402, 0x91d0d784, 0x1dde4804
PINS = (
    ('ActorMemberDispatch', 0x42f140, 97, 'c298ae46f58c5a168a30299c8874f8e0d779eec8e6d9622b669cc0b51d81e634'),
    ('PhysicsControlMember', 0x5f04f0, 52, '1cbfdc7bcac331db120ab933f62ebc55ccea62b57e91bf5834a41d567b446f92'),
    ('WalkingToSkating', 0x5f0390, 338, '66c69a64a68a7f24c01f53c9e5d0abcffb95651e377ff6e3926004d746f97337'),
    ('SetPhysicsMode', 0x5f2360, 30, 'e5e488a2436cf88690dc30f61b296269923cfefe262a4615d092f55830de0199'),
    ('ControlBindings', 0x5eff40, 63, 'd106cd494c1d6d50e07355fc68516b2701de9bd33e0f242b271460ab9976c42a'),
    ('ComponentSuspension', 0x42e560, 29, '2ca14ed3a0047dc2c2dd243ea9fd669ce3bf4f9bf9c9add92a764a6863d26161'),
    ('OriginalProfileSwitchCaller', 0x590fd2, 16, '8dd4a1d7985cde0badbc11a60370497d095eeefee0af3222d1151eabbc631b09'),
    ('ControlVtable', 0x6a1038, 52, 'ab84697c37f98b402bb12d6534e85aa18c33549a0f6c757f156c1710ab0e538e'),
    ('StateWalking', 0x5f2440, 27, '7ee0e9b87b08d7e17962120b6f63688c12d27ea8dc7fe4bcdd56703a005025d6'),
    ('StateSkating', 0x5f2460, 30, 'f36bc1b503b0f18006c156c79566f9759b90dc18b8048c7fb8e2382d8cd589ce'),
    ('WalkGround', 0x5f23b0, 13, 'b7e3f28ede1688065f443d4fc4207d6ffa99f4f696f8f21061a07a7f3c73a895'),
    ('WalkAir', 0x5f23c0, 14, '2bfb19672143b9319b602390481ddee83ed0bc233d02bda701fdd08b459686ee'),
    ('ActorStartScript', 0x591340, 141, '35b1b32d400f4201d565ae39574e9433c49364ca7809d7d2fedb108923835e8b'),
    ('OriginalWalkingSwitchCaller', 0x591f00, 157, '181af0c6b9c68a82df62150e3fa616a87a56e0b043fbeccddae631b557be2943'),
)
# Relocated live QB headers retain their original stored script bodies. Import
# already verifies every complete source; these guards identify the exact route
# again before changing the existing actor-owned Script.
SCRIPTS = (
    ('scripts/game/skater/switch_control.qb.wpc', 1988, SCRIPT, 1444, 1464, 497, 378,
     '617876537f888e265a9f69ab3c52e813e331ed83ff5147607eeb1bb2dfe9e602'),
    ('scripts/game/skater/switch_control.qb.wpc', 1988, 0x3db26a2e, 1192, 1212, 174, 137,
     '63c3dcdd61852a0e2628fbe6f7a661d22e4d21f127d215413a9f00a4db21def0'),
    ('scripts/game/skater/walking_control.qb.wpc', 40728, 0xa3fd373e, 324, 344, 358, 185,
     'd3dd6a5bf7b34ad65263665ce98d3093777f9e2484ca19c0823db100fcf6fb3b'),
)


def validate_script_source(read, sources, pin):
    name, expected_size, key, section, header, length, stored, digest = pin
    host.require(name in sources, 'Original skating transition source is absent')
    base, size = sources[name]
    host.require(type(base) is int and 0x10000 <= base < 0xffffffff - expected_size and
                 size == expected_size and read(base, 1) == b'\1',
                 'Original skating transition source ownership differs')
    entry = struct.unpack('<5I', read(base + section, 20))
    script_header = struct.unpack('<3I', read(base + header, 12))
    host.require(entry[:4] == (0x70400, key, qbkey(name), base + header) and
                 script_header[1:] == (length, stored) and
                 hashlib.sha256(read(base + header + 12, stored)).hexdigest() == digest,
                 'Original skating transition script differs')


def validate_switch_contact(sample):
    contact = classify(sample)
    if sample['mode'] == 1 and contact not in ('ground', 'air'):
        raise ValueError('Original mode transition supports only Ground/Air walking')
    return contact


def validate_transition(before, after):
    host.require(before['mode'] == 1 and validate_switch_contact(before) in ('ground', 'air'),
                 'Original mode transition needs a supported walking state')
    host.require(before['suspended'] == [1, 0, 1, 0] and
                 after['mode'] == 0 and after['suspended'] == [0, 1, 0, 1],
                 'Original transition did not exchange Core/Walk and their camera ownership')
    host.require(classify(after) == classify(before),
                 'Original WalkingToSkating changed its Ground/Air contact classification')


def run(api, process, record, deadline, log, context, system, *, vm_sources):
    system.worker.check_call_access()
    read, word = system.read, system.word
    probe_vm_idle.require_idle(system, context, vm_sources)
    clock = read(0x8459c8, 16)
    host.require(clock[:8] == clock[8:], 'Switch requires a completed original frame')
    for name, va, size, digest in PINS:
        host.require(hashlib.sha256(read(va, size)).hexdigest() == digest, 'Original switch pin differs: ' + name)
    actor, owner = context['actor'], context['camera']
    host.require(word(actor) == 0x6989e8 and word(actor + 0xc) == word(0x6dd6b0) and
                 read(actor + 0x2c8, 40) == struct.pack('<10f', *([10.] * 10)),
                 'Switch requires the genuine local actor with ten maximum stats')
    start, end = word(0x84376c), word(0x843770)

    def chain(object_pointer):
        node, result, seen = word(object_pointer + 0x11c), {}, set()
        while node:
            host.require(node % 4 == 0 and start <= node <= end - 24 and
                         node not in seen and len(seen) < 64, 'Switch component chain is invalid')
            seen.add(node)
            row = struct.unpack('<6I', read(node, 24))
            host.require(row[3] == object_pointer and row[1] not in result,
                         'Switch component owner/type is aliased')
            result[row[1]], node = node, row[4]
        return result

    components, cameras = chain(actor), chain(owner)
    host.require(components == context['components'], 'Switch actor component graph changed')
    control, state = components[0xd9151d4d], components[0x43686585]
    core, walk = components[0x5bd63b29], components[0x726e85aa]
    skater_camera, walk_camera = cameras[0x5e43a604], cameras[0xc7d37ec2]
    host.require(word(control) == 0x6a1038 and word(control + 0x28) == core and
                 word(control + 0x2c) == state and word(core + 0x192c) == state and
                 read(control + 0x18, 1) == b'\0', 'Switch PhysicsControl links or pause state differ')

    def snapshot():
        values = struct.unpack('<8f', read(actor + 0x68, 16) + read(actor + 0xc8, 16))
        host.require(all(math.isfinite(v) for v in values), 'Switch actor position or velocity is nonfinite')
        return dict(mode=word(state + 0x1c), walking_gate=read(state + 0x130, 1)[0],
                    state=word(state + 0x18), walk_state=word(walk + 0x18),
                    suspended=[word(ptr + 8) & 1 for ptr in (core, walk, skater_camera, walk_camera)],
                    position=list(values[:3]), velocity=list(values[4:7]))

    before = snapshot()
    validate_switch_contact(before)
    if before['mode'] == 0:
        host.require(before['suspended'] == [0, 1, 0, 1], 'Skating component ownership differs')
        log('switch_skating_already_active', before=before, native_calls=0, host_actor_writes=False)
        return before
    host.require(before['suspended'] == [1, 0, 1, 0], 'Walking component ownership differs before switching')
    for pin in SCRIPTS:
        validate_script_source(read, vm_sources, pin)
    parameter_pins = {key: probe_handoff.PINS[key] for key in ('StructCtor', 'StructDtor', 'AddChecksum')}
    for name, va, size, digest in parameter_pins.values():
        host.require(hashlib.sha256(read(va, size)).hexdigest() == digest,
                     'Original switch parameter pin differs: ' + name)
    owner_script = word(actor + 0x10)
    host.require(owner_script % 4 == 0 and start <= owner_script <= end - 0xfc and
                 word(owner_script + 0xc4) == actor, 'Switch requires the original actor-owned Script')
    params = system.allocate(bytes(8))
    def call(key, args=(), this=None):
        return host.native_call(api, process, parameter_pins[key], list(args), record, deadline, log, this)
    host.require(call('StructCtor', this=params) == params and read(params, 8) == bytes(8),
                 'Original switch parameters were not constructed')
    call('AddChecksum', (NEW_SCRIPT, ON_GROUND), params)
    log('switch_skating_begin', before=before, original_member=MEMBER,
        original_script=SCRIPT, newscript=ON_GROUND, owner_script=owner_script)
    # Same whole call and parameter as 591F7B..591F83. It reuses actor+10 and
    # executes the original wrapper immediately, including its owner-script GOTO.
    start_script = next(pin for pin in PINS if pin[0] == 'ActorStartScript')
    host.native_call(api, process, start_script, [SCRIPT, params], record, deadline, log, actor)
    call('StructDtor', this=params)
    host.require(read(params, 8) == bytes(8) and word(actor + 0x10) == owner_script and
                 word(owner_script + 0xc4) == actor and word(owner_script + 0xd0) != 0,
                 'Original skating transition changed Script ownership or retained parameters')
    after = snapshot()
    log('switch_skating_observed', before=before, after=after)
    validate_transition(before, after)
    probe_vm_idle.require_idle(system, context, vm_sources)
    host.require(read(0x8459c8, 16) == clock and chain(actor) == components and chain(owner) == cameras,
                 'Original switch changed frame ownership or component identity')
    log('switch_skating_pass', before=before, after=after, original_member=MEMBER,
        original_mode_event=0x6bac488c, original_script=SCRIPT, newscript=ON_GROUND,
        owner_script=owner_script, owner_checksum=word(owner_script + 0xd0),
        complete_authored_transition=True, native_calls=4, native_frames=0,
        host_actor_writes=False, requested_handoff_applied=False)
    return after
