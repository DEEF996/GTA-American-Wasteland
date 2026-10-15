"""Publish original solved THAW camera samples for an already-owned GTA MOVE2 lease.

This adapter performs coordinate conversion only. Camera input, collision,
recentring and FOV are solved by the source game. The native consumer smooths
presentation between samples and restores GTA camera control when the lease ends.
"""
from __future__ import annotations
import math
import struct

from geometry import thaw_to_gta
from preview_gameplay_move import scene, require
from preview_pose import Publisher

BODY = struct.Struct('<8s4I4Q4I2Q13f')
assert BODY.size == 140


def source_pose(camera, *, origin=(0, 0, 0)):
    require(camera.get('original_solved_pose') is True and camera.get('current_projection_verified') is True,
            'Camera requires the actual original solved pose and verified native projection')
    eye = thaw_to_gta(camera['position_thaw'], origin=origin)
    forward = thaw_to_gta(camera['forward_thaw'], scale=1)
    up = thaw_to_gta(camera['up_thaw'], scale=1)
    fov = camera['effective_horizontal_fov_degrees']
    projection = camera['original_projection']
    aspect = camera['native_viewport_aspect']
    require(len(projection) == 16 and all(math.isfinite(v) for v in projection)
            and projection[0] > 0 and projection[5] > 0 and math.isfinite(aspect)
            and 1 < aspect <= 10, 'Invalid original projection or viewport aspect')
    view = (1/projection[0], 1/projection[5])
    require(all(.01 < v < 10 for v in view)
            and abs(math.degrees(2*math.atan(view[0]))-fov) < .01,
            'Original projection does not match its horizontal FOV')
    require(all(math.isfinite(v) for v in (*eye, *forward, *up, fov)) and
            all(abs(v) <= 100000 for v in eye) and 10 <= fov <= 150 and
            abs(sum(v*v for v in forward)-1) < .005 and abs(sum(v*v for v in up)-1) < .005 and
            abs(sum(a*b for a, b in zip(forward, up))) < .005,
            'Invalid source camera basis, coordinates or horizontal FOV')
    # No CJ root/feet offset: the camera already lives in the original collision world's coordinates.
    return (*eye, *forward, *up, fov, *view, aspect)


def packet(state, identity, request, sequence, now_ms, source_tick, source_time_us, camera, *, origin=(0, 0, 0)):
    require(scene(state) == identity, 'Camera scene changed')
    require(all(type(v) is int for v in (request, sequence, now_ms, source_tick, source_time_us)) and
            0 < request < 2**64 and 0 < sequence < 0x3fffffff and 0 <= now_ms < 2**64 and
            0 <= source_tick < 2**64 and 0 <= source_time_us < 2**64 and
            (source_tick == 0) == (source_time_us == 0), 'Invalid camera source clock or lease')
    pose = source_pose(camera, origin=origin)
    require(math.dist(pose[:3], state['position']) <= 100, 'Camera is outside the owned player scene')
    return BODY.pack(b'GTACAM02', 2, BODY.size, state['pid'], state['epoch'],
                     state['creation_100ns'], request, sequence, now_ms, state['ped_address'],
                     state['area'], state['ped_area'], 0, source_tick, source_time_us, *pose)


class CameraPublisher:
    """Keep one existing transport mutex; clear between F7 sessions, close on host exit.

    Publish once per completed original frame, after its matching MOVE2 sample.
    `source_tick/time_us` must use that lease's relative source clock. `sequence`
    stays monotonic across sessions. No input polling or gameplay activation here.
    """
    def __init__(self, observer):
        self.transport = Publisher(observer, transport='CAMERA_V2', packet_size=BODY.size)
        self.sequence = 0

    def publish(self, state, identity, request, now_ms, source_tick, source_time_us, camera, *, origin=(0, 0, 0)):
        self.sequence += 1
        self.transport.publish(packet(state, identity, request, self.sequence, now_ms, source_tick,
                                      source_time_us, camera, origin=origin), self.sequence)

    def clear(self):
        self.transport.clear()

    def close(self):
        self.transport.close()
