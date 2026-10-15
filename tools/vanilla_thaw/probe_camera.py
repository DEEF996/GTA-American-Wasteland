"""Read-only solved stock camera export, between original FrameDriver steps.

The source owns its eye and basis. Effective FOV needs the original renderer
getter/publication path, which is not executed here. Cached FOV fields are
diagnostics only; this reader deliberately does not infer a current projection.
"""
import hashlib
import math
import struct

import probe_loader as host

PINS = (
    ('CameraEyeGetter', 0x4c22f0, 4, 'f5b9d7385c09f9d310fbd9c5b7d7a59e9d0aa483ec3265c47c4d76513a80fd36'),
    ('CameraMatrixGetterEntry', 0x4c2310, 6, '9e782596bc043dc487d583368820eada4905ee5ffbac62b65f16a6c70eb9f240'),
    ('CameraEffectiveFOV', 0x4c23a0, 77, 'b94f682f43de822a3343f7c4b71f93473163b7c5b9e64e3cf4043734b496c9dd'),
    ('CameraRendererCaller', 0x508a78, 58, '7471c56ccd1989dd06ed4b1b3241da7933e3c6e83906440af7db63129468126f'),
    ('CameraDtorIdentity', 0x4c2240, 11, '1fb0f8bce6e25b7512db940e81c685a1b39637177d7bf18b03b40d5d2fefc0cb'),
    ('CameraVtable', 0x6933d0, 28, '85e03053538ba83782da146241083fd20030e08ddef23f4e2f263d2492ca5255'),
    ('CameraProjectionPublisher', 0x536e50, 985, '896debdf4af259eabad8ccca9c0561dfc072216524fcdd66040b30ba29535f4b'),
)


def decode_pose(raw):
    host.require(len(raw) == 0x68, 'Stock camera pose extent differs')
    values = struct.unpack('<20f', raw[:80])
    host.require(all(math.isfinite(v) and abs(v) < 10000000 for v in values),
                 'Stock camera eye/matrix contains an invalid value')
    position, right, up, backward = values[:3], values[4:7], values[8:11], values[12:15]
    axes = right, up, backward
    host.require(all(0.9 <= sum(v * v for v in axis) <= 1.1 for axis in axes) and
                 all(abs(sum(a * b for a, b in zip(axes[i], axes[j]))) < 0.05
                     for i, j in ((0, 1), (0, 2), (1, 2))), 'Stock camera axes are not orthonormal')
    # Original publisher536E50 uses rows1/2 and builds target = eye - row2.
    projection_fields = struct.unpack('<6I', raw[80:])
    base_fov, cached_fov = struct.unpack('<2f', raw[80:88])
    host.require(math.isfinite(base_fov) and 1 <= base_fov <= 179 and math.isfinite(cached_fov),
                 'Stock camera base/cached field of view differs')
    return dict(position_thaw=list(position), forward_thaw=[-v for v in backward], up_thaw=list(up),
                right_thaw=list(right), base_fov_degrees=base_fov, cached_horizontal_fov_degrees=cached_fov,
                projection_fields_u32=list(projection_fields), fov_y_radians=None,
                current_projection_verified=False, pose_sha256=hashlib.sha256(raw[:80]).hexdigest())


class NativeCameraReader:
    def __init__(self, context, system):
        self.context, self.system = context, system
        for name, address, size, digest in PINS:
            host.require(hashlib.sha256(system.read(address, size)).hexdigest() == digest,
                         'Stock camera evidence pin differs: ' + name)
        self.identity = self._identity()

    def _identity(self):
        read, word = self.system.read, self.system.word
        start, end = word(0x84376c), word(0x843770)

        def words(address, count):
            host.require(address % 4 == 0 and start <= address <= end - count * 4,
                         'Stock camera object leaves its genuine arena')
            return struct.unpack(f'<{count}I', read(address, count * 4))

        owner = self.context['camera']
        fields = words(owner, 0x124 // 4)
        host.require(fields[3] == word(0x6dd6b0), 'Stock camera owner manager changed')
        node, seen, component = fields[0x11c // 4], set(), 0
        while node:
            host.require(node not in seen and len(seen) < 64, 'Stock camera component chain cycles')
            seen.add(node)
            row = words(node, 7)
            host.require(row[3] == owner, 'Stock camera component has another owner')
            if row[1] == 0xc4e311fa:
                host.require(component == 0, 'Stock camera component is duplicated')
                component = node
            node = row[4]
        host.require(component != 0, 'Stock camera component is absent')
        camera = word(component + 0x18)
        host.require(words(camera, 0xd8 // 4)[0] == 0x6933d0, 'Stock Nxcamera class differs')
        host.require(word(0x76b6f8) == 0 and word(0x76b704) == 1 and word(0x6c0ee0) == 0x694128,
                     'Camera reader requires the existing original single local viewport')
        viewport = word(0x76b6d0)
        host.require(words(viewport, 9)[6] == camera, 'Stock viewport references another camera')
        return owner, component, viewport, camera

    def sample(self):
        host.require(self._identity() == self.identity, 'Stock camera identity changed before sampling')
        camera = self.identity[3]
        before = self.system.read(camera + 0x70, 0x68)
        result = decode_pose(before)
        host.require(self._identity() == self.identity and self.system.read(camera + 0x70, 0x68) == before,
                     'Stock camera changed during read; sample only between original worker steps')
        return dict(result, camera_owner=self.identity[0], camera=camera, viewport=self.identity[2],
                    original_solved_pose=True, native_calls=0, host_camera_writes=False)
