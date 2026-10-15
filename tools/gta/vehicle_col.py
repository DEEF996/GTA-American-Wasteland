"""Repack exact local vehicle triangles into stock COL objects with BSP trees.

The static world compiler is deliberately unchanged. Stock 0x42D310 reads a
signed fixed-point plane (1/16 inch), visits child 0 for min < plane and child 1
for max >= plane. 0x42D230 relocates the adjacent child pair and leaf indices.
Triangles crossing a split are referenced by both leaves, never cut or moved.
"""
import struct

from assets_export import parse_col, qbkey, require
from carentan_col import align, validate_bsp

MAX_OBJECT_FACES = 1024
MAX_LEAF_FACES = 50
MAX_DEPTH = 12
MAX_OBJECT_REFERENCES = 2048


class _CannotSplit(Exception):
    pass


def _tree(face_ids, bounds):
    """Bound both tree depth and duplicated leaf references for one object."""
    remaining = [min(len(face_ids) * 8, MAX_OBJECT_REFERENCES), 511]

    def split(ids, depth):
        remaining[1] -= 1
        if remaining[1] < 0:
            raise _CannotSplit
        if len(ids) <= MAX_LEAF_FACES:
            remaining[0] -= len(ids)
            if remaining[0] < 0:
                raise _CannotSplit
            return ids
        if depth == MAX_DEPTH:
            raise _CannotSplit
        best = None
        for axis in range(3):
            centers = sorted((bounds[i][0][axis]+bounds[i][1][axis])*.5 for i in ids)
            planes = {round(centers[len(ids)*n//8]*16) for n in range(1, 8)}
            for fixed in sorted(planes):
                plane = fixed/16
                left = tuple(i for i in ids if bounds[i][0][axis] < plane)
                right = tuple(i for i in ids if bounds[i][1][axis] >= plane)
                if not left or not right or max(len(left), len(right)) >= len(ids):
                    continue
                score = (max(len(left), len(right)), len(left)+len(right), axis, fixed)
                if best is None or score < best[0]:
                    best = score, left, right
        if best is None:
            raise _CannotSplit
        (_, _, axis, fixed), left, right = best
        require(-(1 << 29) <= fixed < (1 << 29), 'Vehicle BSP plane exceeds signed fixed-point range')
        return axis, fixed, split(left, depth+1), split(right, depth+1)

    return split(tuple(face_ids), 0)


def _groups(count, bounds):
    def group(first, end):
        try:
            tree = _tree(range(first, end), bounds)
        except _CannotSplit:
            # Coincident/overlapping faces cannot always make a useful spatial
            # split. Keep exact geometry in more objects instead of losing it.
            require(end-first > MAX_LEAF_FACES, 'Vehicle BSP leaf budget differs')
            middle = (first+end)//2
            yield from group(first, middle)
            yield from group(middle, end)
        else:
            yield first, end, tree
    for first in range(0, count, MAX_OBJECT_FACES):
        yield from group(first, min(count, first+MAX_OBJECT_FACES))


def repack_col(payload):
    """Return COL bytes with the identical ordered float32 triangles/materials."""
    parsed = parse_col(payload)
    positions, indices = parsed['positions'], parsed['indices']
    triangles = list(zip(indices[::3], indices[1::3], indices[2::3]))
    require(len(triangles) <= 51200, 'Vehicle BSP input exceeds the vehicle triangle budget')
    bounds = [(tuple(min(positions[v][a] for v in face) for a in range(3)),
               tuple(max(positions[v][a] for v in face) for a in range(3))) for face in triangles]
    metadata, vertices, intensities, faces, nodes, refs = (bytearray() for _ in range(6))
    count = 0

    def encode(tree, slot, first):
        if len(tree) == 4 and isinstance(tree[0], int) and isinstance(tree[2], tuple):
            axis, fixed, left, right = tree
            child = len(nodes)
            nodes.extend(bytes(16))
            struct.pack_into('<II', nodes, slot, ((fixed << 2) | axis) & 0xffffffff, child)
            encode(left, child, first)
            encode(right, child+8, first)
        else:
            require(0 < len(tree) <= MAX_LEAF_FACES, 'Vehicle BSP leaf exceeds proven bound')
            struct.pack_into('<II', nodes, slot, 3 | len(tree) << 16, len(refs)//2)
            refs.extend(struct.pack('<'+str(len(tree))+'H', *(i-first for i in tree)))

    for first, end, tree in _groups(len(triangles), bounds):
        local, points, local_faces = {}, [], []
        for face in triangles[first:end]:
            converted = []
            for vertex in face:
                raw = struct.pack('<3f', *positions[vertex])
                if raw not in local:
                    local[raw] = len(points)
                    points.append(positions[vertex])
                converted.append(local[raw])
            local_faces.append(converted)
        lower = [min(p[a] for p in points) for a in range(3)]
        upper = [max(p[a] for p in points) for a in range(3)]
        root = len(nodes)
        nodes.extend(bytes(8))
        encode(tree, root, first)
        metadata.extend(struct.pack('<IHHHBBI4f4fIIIf', qbkey(f'gta_vehicle_bsp_{count}'),
            0, len(points), end-first, 0, 0, len(faces), *lower, 1., *upper, 1.,
            len(vertices), root, len(intensities), 0.))
        for point in points:
            vertices.extend(struct.pack('<3f', *point))
        intensities.extend(bytes([128])*len(points))
        for i, face in enumerate(local_faces, first):
            faces.extend(struct.pack('<5H', parsed['face_flags'][i], parsed['terrain_types'][i], *face))
        count += 1
    lower, upper = parsed['bounds']['min'], parsed['bounds']['max']
    rows, columns = (max(1, int((upper[a]-lower[a])/10000)) for a in (0, 2))
    require(rows*columns <= 65536, 'Vehicle COL grid exceeds the compiler bound')
    vertex_count = len(vertices)//12
    result = bytearray(struct.pack('<12I4f4f', 10, count, vertex_count, len(triangles),
        0, vertex_count, 0, 0, 0, 0, rows, columns, *lower, 1., *upper, 1.))
    result.extend(metadata)
    result.extend(vertices)
    result.extend(intensities)
    result.extend(bytes(align(len(result), 4)-len(result)))
    result.extend(faces)
    result.extend(bytes(len(triangles)))
    result.extend(bytes(align(len(result), 4)-len(result)))
    result.extend(struct.pack('<I', len(nodes)))
    result.extend(nodes)
    result.extend(refs)
    validate_bsp(result)
    # parse_col checks all native packing/count/bounds. Face order is retained,
    # so existing ordered provenance and material associations remain valid.
    check = parse_col(result)
    require(check['stats']['triangles'] == len(triangles), 'Vehicle BSP face count changed')
    return bytes(result)
