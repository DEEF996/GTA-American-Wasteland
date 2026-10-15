"""Request the original stock walking-camera flush on its real camera owner.

The complete original member resets shared LookAround offsets and requests the
native walking camera's return behind its target. Later ordinary frame updates
own easing, collision, height and pose. No camera/actor field is written by us.
The authored stock caller is walking_control::869B86AD. Its effect while skating
must be assessed separately; resetting shared offsets alone is not that proof.
"""
import hashlib
import struct

import probe_camera
import probe_loader as host
import probe_vm_idle
from import_qb import qbkey

SOURCE = 'scripts/game/skater/walking_control.qb.wpc'
SOURCE_BYTES = 40728
SOURCE_SHA = '9735036fe54d6de76db9353d1afecbda5eb4e3545ca7b1e7767d4909fa2e1d9c'
SCRIPT, SCRIPT_SECTION, SCRIPT_HEADER = 0x869b86ad, 32692, 32712
BODY_SHA = '9c2de54322e638ff5368e13e27a68f756b484b73b94b3f0d024b3e34bf7733e3'
WALK_CAMERA, LOOK, WALK, CONTROL = 0xc7d37ec2, 0x81c7122d, 0x726e85aa, 0xd9151d4d
PINS = (
    ('OriginalWalkCameraFlush', 0x5fa6b0, 77, '62ce0eb969b6e52485339a38e80a25fbf5b9f1b1b704d6534c8e815e0fcf4044'),
    ('OriginalWalkCameraTargetBinding', 0x5fa370, 83, '0e039d73224bee5051cd61bbf788f087f074326ec0f6e37ad5e1e6028c3b2c1e'),
    ('OriginalWalkCameraComponentsBinding', 0x5fa1e0, 62, 'ca435b707b8d3deb7a4b5b9926a9c5dfd95e55bd5dfcd0d173fba6b1fa489c4b'),
    ('OriginalWalkCameraVtable', 0x6a148c, 48, '1b4ddc75a9dd34abed1b87af33b24683a7cb0d93deab63f0223886728c35056e'),
    ('OriginalLookAroundVtable', 0x6a0600, 48, '441956fcb7740679640be97351deba60fb2b6e249d1454092a9c169d6b16b0a4'),
    ('OriginalCameraFlushZoom', 0x68e3a0, 4, 'e00e5eb9444182f352323374ef4e08ebcb784725fdd4fd612d7730540b3e0c8c'),
    ('OriginalCameraFlushDescriptorIdentity', 0x6d8220, 8, '8e936bc51d2cfad37d54b27aa6c7a34f98683486e237bc6dc2f0fe0ac8c95589'),
    ('OriginalCameraFlushCRT', 0x68c6e0, 93, '6281679f59d322df6222355dc9b78c12a4610be1a040869aabc7b89e66c072a3'),
)


def validate_changes(before, after):
    camera = bytearray(before['walk_camera'])
    look = bytearray(before['look'])
    walk = bytearray(before['walk_flag'])
    host.require(len(camera) == 0xb8 and len(look) == 0x48 and len(walk) == 4,
                 'Original recenter snapshot has a different object extent')
    camera[0x1c], walk[0], look[0x18] = 1, 1, 0
    struct.pack_into('<3I', look, 0x1c, 0x3f800000, 0, 0)
    host.require(after['walk_camera'] == camera and after['walk_flag'] == walk and after['look'] == look,
                 'Original recenter changed unexpected component fields')
    host.require(after['actor'] == before['actor'] and after['pose'] == before['pose'],
                 'Recenter must preserve actor motion and defer solved camera pose to original frames')


def run(api, process, record, deadline, log, context, system, *, vm_sources):
    system.worker.check_call_access()
    read, word = system.read, system.word
    vm_before = probe_vm_idle.require_idle(system, context, vm_sources)
    clock = read(0x8459c8, 16)
    host.require(clock[:8] == clock[8:], 'Original recenter requires a completed native frame')
    for name, va, size, digest in PINS:
        host.require(hashlib.sha256(read(va, size)).hexdigest() == digest, 'Original recenter pin differs: ' + name)
    source, size = vm_sources[SOURCE]
    host.require(size == SOURCE_BYTES and read(source, 1) == b'\1', 'Original camera helper source ownership differs')
    section = struct.unpack('<5I', read(source + SCRIPT_SECTION, 20))
    header = struct.unpack('<3I', read(source + SCRIPT_HEADER, 12))
    host.require(section[:4] == (0x70400, SCRIPT, qbkey(SOURCE), source + SCRIPT_HEADER) and
                 header[1:] == (21, 21) and hashlib.sha256(read(source + SCRIPT_HEADER + 12, 21)).hexdigest() == BODY_SHA,
                 'The complete authored stock camera-flush helper differs')
    host.require(struct.unpack('<3I', read(0x6d8220, 12)) == (0x73febd0f, WALK_CAMERA, PINS[0][1]),
                 'Original camera flush member publication differs')
    start, end = word(0x84376c), word(0x843770)

    def words(address, count):
        host.require(address % 4 == 0 and start <= address <= end - count * 4,
                     'Original recenter object leaves its native arena')
        return struct.unpack(f'<{count}I', read(address, count * 4))

    actor, owner = context['actor'], context['camera']
    host.require(words(actor, 0x35c // 4)[0] == 0x6989e8 and
                 words(owner, 0x124 // 4)[3] == word(0x6dd6b0), 'Original recenter actor/camera owner differs')
    pointer, components, seen = word(owner + 0x11c), {}, set()
    while pointer:
        host.require(pointer not in seen and len(seen) < 64, 'Original recenter camera component chain cycles')
        seen.add(pointer)
        row = words(pointer, 6)
        host.require(row[3] == owner and row[1] not in components, 'Original camera component owner/type is aliased')
        components[row[1]], pointer = pointer, row[4]
    camera, look, walk = components[WALK_CAMERA], components[LOOK], context['components'][WALK]
    wc, lc, target = words(camera, 0xb8 // 4), words(look, 0x48 // 4), words(walk, 0x76c // 4)
    host.require(wc[0] == 0x6a148c and lc[0] == 0x6a0600 and target[1] == WALK and target[3] == actor and
                 wc[0xa0 // 4] == look and wc[0xa4 // 4] == components[0xc4e311fa] and
                 wc[0xa8 // 4] == components[0x5e43a604] and
                 wc[0xac // 4:0xb8 // 4] == (actor, walk, context['components'][CONTROL]),
                 'Original recenter components do not share the actual actor and camera')
    reader = probe_camera.NativeCameraReader(context, system)

    def snapshot():
        return dict(walk_camera=read(camera, 0xb8), look=read(look, 0x48), walk_flag=read(walk + 0x768, 4),
                    actor=read(actor, 0x35c), pose=reader.sample()['pose_sha256'])

    before = snapshot()
    result = host.native_call(api, process, PINS[0], [0, 0], record, deadline, log, camera)
    host.require(result == 1, 'Original camera flush member rejected the request')
    validate_changes(before, snapshot())
    host.require(probe_vm_idle.require_idle(system, context, vm_sources) == vm_before and
                 read(0x8459c8, 16) == clock, 'Original recenter changed VM or frame ownership')
    result = dict(original_member=0x73febd0f, original_function=0x5fa6b0,
                  authored_script=SCRIPT, camera_owner=owner, actor=actor,
                  walking_gate=word(context['components'][0x43686585] + 0x130) & 255,
                  native_calls=1, native_gameplay_frames=0, host_camera_or_actor_field_writes=False,
                  original_height_preserved=True, native_camera_easing_pending=True,
                  effect_while_skating_verified=False)
    log('camera_recenter_requested', **result)
    return result
