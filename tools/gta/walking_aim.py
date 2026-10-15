"""Express walking input in the original camera basis, using the owned aim view."""
import math
import struct

from geometry import thaw_to_gta
from probe_frame_input import packet, validate_packet


def walking(sample):
    # Hanging shimmy and wall-run controls are relative to their contact, not the view.
    return (sample.get('mode') == 1 and sample.get('walking_gate') == 0
            and sample.get('walk_state') in (0, 1) and not sample.get('bailing', False))


def rotate_packet(value, original_forward, aim_forward):
    validate_packet(value)
    if value[2:4] == b'\x80\x80':
        return value
    source = thaw_to_gta(original_forward, scale=1)
    vectors = source, aim_forward
    if any(len(v) != 3 or not all(math.isfinite(x) for x in v)
           or math.hypot(*v[:2]) < .01 for v in vectors):
        raise ValueError('Walking camera direction has no finite horizontal basis')
    source, aim = ([v[0]/math.hypot(*v[:2]), v[1]/math.hypot(*v[:2])] for v in vectors)
    right = (value[2]-128)/(128 if value[2] < 128 else 127)
    forward = (128-value[3])/(128 if value[3] < 128 else 127)
    length = max(1., math.hypot(right, forward))
    right, forward = right/length, forward/length
    # GTA is Z-up: screen right is (forward.y, -forward.x).
    world = (aim[1]*right + aim[0]*forward, -aim[0]*right + aim[1]*forward)
    local_right = world[0]*source[1] - world[1]*source[0]
    local_back = -(world[0]*source[0] + world[1]*source[1])

    def axis(x):
        return max(0, min(255, round(128 + x*(128 if x < 0 else 127))))

    return packet((*value[:2], axis(local_right), axis(local_back)),
                  struct.unpack_from('<I', value, 0x24)[0])
