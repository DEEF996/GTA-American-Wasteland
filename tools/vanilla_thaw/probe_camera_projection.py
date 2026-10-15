"""Original stock camera getters and renderer projection on the retained worker.

The glue follows pinned renderer caller508A78. x87 return values are copied to
owned output, then the whole original536E50 publishes native transforms. It
does not start rendering, draw, change aspect/FOV preferences, or tick physics.
Use the same worker.operation_budget as the caller's other bounded operations.
"""
import hashlib
import math
import struct

import probe_camera
import probe_loader as host
import probe_vm_idle

PINS = probe_camera.PINS + (
    ('ViewportAspect', 0x4ee020, 34, '603d12baff39fdc2ba49cc5475a57fc81bb121fed1ecb2a7fb3963ebcddbd143'),
    ('CameraClipFOVUpdate', 0x4c2250, 128, '1ab019d986e9dddeda28d984f271119e07c7ca6b0aa64d2b9f2d5eebbbc3d30e'),
    ('ViewportGlobalAspect', 0x4eda80, 7, '78e05d8c6f3432b63883556d4a427dfe7e3155ed45f8c8062329915e23667399'),
    ('ViewportGlobalFOV', 0x4edaa0, 7, '041789c58236282ebdfa095a1a3b37ec85c952c2250c9feec6b9878549962eb2'),
    ('ViewportBaselineAspect', 0x4edab0, 7, '5fdc8782123449a3444d096e0774b0a40f0accd3487fc0ef45bd022a6d663c7d'),
    ('OriginalViewFrustumCache', 0x527a70, 716, 'b0185b65f816e2447082b2fe226930664e7e3cbe3027cf15b735c63e7216cd93'),
)
CANARY = b'CAM-PROJ-GUARD!!'
MATRICES = (('world', 0x100, 0x7f3b48, 0x7ecdb8),
            ('view', 2, 0x7f3b88, 0x7ece10), ('projection', 3, 0x7f3bc8, 0x7ecd68))


def projection_stub(viewport, camera, record, output):
    host.require(all(type(p) is int and 0x10000 <= p <= 0xffffff00 and p % 4 == 0
                     for p in (viewport, camera, record, output)), 'Invalid owned projection glue addresses')
    pack = lambda value: struct.pack('<I', value)
    code = bytearray(b'\xc7\x05' + pack(record) + pack(2))

    def getter(target, this, offset, floating):
        code.extend(b'\xb9' + pack(this) + b'\xb8' + pack(target) + b'\xff\xd0')
        code.extend((b'\xd9\x1d' if floating else b'\xa3') + pack(output + offset))

    getter(0x4ee020, viewport, 0, True)
    getter(0x4c23a0, camera, 4, True)
    getter(0x4c22f0, camera, 8, False)
    getter(0x4c2310, camera, 12, False)
    # cdecl(matrix, eye, effective_horizontal_fov, viewport_aspect, 1.0f, 0)
    code.extend(b'\x68' + pack(0) + b'\x68' + pack(0x3f800000))
    for offset in (0, 4, 8, 12):
        code.extend(b'\xff\x35' + pack(output + offset))
    code.extend(b'\xb8' + pack(0x536e50) + b'\xff\xd0\x83\xc4\x18')
    code.extend(b'\xc7\x05' + pack(record) + pack(6) + host.STUB)
    return bytes(code)


def decode_projection(raw, horizontal_degrees):
    """Decode the produced matrix; do not reconstruct original aspect tuning."""
    host.require(len(raw) == 64 and math.isfinite(horizontal_degrees) and 1 <= horizontal_degrees <= 179,
                 'Original projection extent or effective horizontal FOV differs')
    matrix = struct.unpack('<16f', raw)
    host.require(all(math.isfinite(v) and abs(v) <= 10000000 for v in matrix) and matrix[0] > 0 and
                 matrix[5] > 0 and abs(matrix[11] + 1) < 0.001 and abs(matrix[15]) < 0.001,
                 'Original projection is not a finite right-handed perspective matrix')
    expected_x = 1 / math.tan(math.radians(horizontal_degrees) * 0.5)
    host.require(abs(matrix[0] - expected_x) < 0.01, 'Native projection does not match its actual horizontal FOV getter')
    vertical = 2 * math.atan(1 / matrix[5])
    host.require(0.01 < vertical < 3.13, 'Native projection vertical FOV is invalid')
    return list(matrix), vertical


class NativeCameraProjection:
    def __init__(self, api, process, record, deadline, log, context, system, *, vm_sources=None):
        self.api, self.process, self.record, self.deadline, self.log = api, process, record, deadline, log
        self.system, self.worker = system, system.worker
        self.context, self.vm_sources = context, vm_sources
        self.reader = probe_camera.NativeCameraReader(context, system)
        self.storage = system.allocate(CANARY + bytes(128) + CANARY)
        self.output = self.storage + 16
        self.check_pins()

    def check_pins(self):
        for name, va, count, digest in PINS:
            host.require(hashlib.sha256(self.system.read(va, count)).hexdigest() == digest,
                         'Original camera projection pin differs: ' + name)

    def publish(self):
        system, worker, record = self.system, self.worker, self.record
        worker.check_call_access()
        before = self.reader.sample()
        viewport, camera = before['viewport'], before['camera']
        device = system.word(0x7ecd04)
        host.require(device >= 0x10000, 'Original camera publication requires the existing Device')
        vm_before = probe_vm_idle.require_idle(system, self.context, self.vm_sources)
        self.check_pins()
        system.write(self.storage, CANARY + bytes(128) + CANARY)
        system.write(record + 36, bytes(36))
        system.write(record + 452, bytes(24))
        code = projection_stub(viewport, camera, record, self.output)
        address = worker.prepare_command(code)
        self.log('camera_projection_begin', camera=camera, viewport=viewport, worker_tid=worker.tid,
                 original_renderer_caller=0x508a78, original_publisher=0x536e50)
        waited, returned = worker.execute(address, self.deadline)
        status = struct.unpack('<18I', system.read(record, 72))
        self.log('camera_projection_result', wait_result=waited, return_value=returned, status=status[0],
                 exception_code=status[9], eip=status[10], worker_tid=worker.tid)
        if waited != 0 or status[0] != 6:
            try:
                self.log('camera_projection_fault_stack', **host.read_fault_stack(self.api, self.process, record))
            except (OSError, RuntimeError) as error:
                self.log('camera_projection_fault_stack', error=str(error))
        host.require(waited == 0 and returned == 0 and status[0] == 6,
                     f'Original camera publication failed: status={status[0]} EIP={status[10]:#x}')
        host.require(system.read(address, len(code)) == code, 'Owned camera projection glue changed')
        aspect, horizontal, eye, matrix_pointer = struct.unpack('<ffII', system.read(self.output, 16))
        host.require(math.isfinite(aspect) and 0.1 <= aspect <= 10 and eye == camera + 0x70 and
                     matrix_pointer == camera + 0x80 and system.word(0x7ecd04) == device,
                     'Original camera getters changed their object or viewport identity')
        matrices = {}
        for name, kind, source, cache in MATRICES:
            original = system.read(source, 64)
            host.require(system.read(cache, 64) == original, 'Original camera transform cache differs: ' + name)
            result = system.com('CameraGetTransform:' + name, device, 45, (kind, self.output + 32))
            host.require(result < 0x80000000 and system.read(self.output + 32, 64) == original,
                         'Actual native D3D9 transform differs from its published camera matrix: ' + name)
            values = struct.unpack('<16f', original)
            host.require(all(math.isfinite(v) and abs(v) <= 10000000 for v in values),
                         'Original camera transform contains nonfinite values: ' + name)
            matrices[name] = list(values)
        raw_projection = system.read(0x7f3bc8, 64)
        projection, vertical = decode_projection(raw_projection, horizontal)
        after = self.reader.sample()
        host.require(after['pose_sha256'] == before['pose_sha256'] and after['camera'] == camera and
                     after['viewport'] == viewport and after['cached_horizontal_fov_degrees'] == horizontal,
                     'Original renderer projection changed solved camera pose or FOV ownership')
        host.require(system.read(self.storage, 16) == CANARY and system.read(self.storage + 144, 16) == CANARY,
                     'Owned camera projection output crossed its guard')
        host.require(probe_vm_idle.require_idle(system, self.context, self.vm_sources) == vm_before,
                     'Original camera publication changed its admitted VM context')
        self.check_pins()
        result = dict(after, fov_y_radians=vertical, current_projection_verified=True,
                      effective_horizontal_fov_degrees=horizontal, native_viewport_aspect=aspect,
                      original_projection=projection, original_world=matrices['world'], original_view=matrices['view'],
                      original_projection_bits=list(struct.unpack('<16I', raw_projection)),
                      waiting_owner_context=vm_before['waiting_owner_context'],
                      native_calls=8, native_gpu_draws=0, native_gameplay_frames=0)
        self.log('camera_projection_pass', **result, worker_tid=worker.tid,
                 gpu_transform_readbacks=3, original_source_functions_unmodified=True,
                 host_aspect_fov_or_camera_field_writes=False, additional_startup=False)
        return result
