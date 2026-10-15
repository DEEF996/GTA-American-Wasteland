"""Author GTA collision transfer labels without changing rail material identities."""
import hashlib
import math
from pathlib import Path

import terrain_mapping as materials

# Stock skutils: mFD_VERT=8, mFD_BANK=256. Spine search5D5788 requires VERT;
# transfer target5D1592 accepts either. GTA needs these authored surface labels.
VERT = 8
BANK = 256
BANK_MIN_SLOPE = 25.
BANK_MAX_SLOPE = 85.
VERT_MIN_SLOPE = 5.
VERT_SHALLOW_SLOPE = 35.
VERT_STEEP_SLOPE = 60.
VERT_MAX_BEND = 35.
COPLANAR_MAX_BEND = .1



def fingerprint():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def collision_flags(positions, triangles):
    """Author banks and connected concave ramps; never replace the native solver."""
    flags, normals, parents, edges, welded = [], {}, {}, {}, {}
    bank_low, bank_high = math.cos(math.radians(BANK_MAX_SLOPE)), math.cos(math.radians(BANK_MIN_SLOPE))
    vert_high = math.cos(math.radians(VERT_MIN_SLOPE))
    bend_min, coplanar_min = math.cos(math.radians(VERT_MAX_BEND)), math.cos(math.radians(COPLANAR_MAX_BEND))
    steep, shallow = math.cos(math.radians(VERT_STEEP_SLOPE)), math.cos(math.radians(VERT_SHALLOW_SLOPE))
    for index, triangle in enumerate(triangles):
        a, b, c = (tuple(positions[i]) for i in triangle)
        ab, ac = (b[0]-a[0], b[1]-a[1], b[2]-a[2]), (c[0]-a[0], c[1]-a[1], c[2]-a[2])
        normal = (ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2], ab[0]*ac[1]-ab[1]*ac[0])
        squared = sum((normal[0]*normal[0], normal[1]*normal[1], normal[2]*normal[2]))
        length = math.sqrt(squared)
        flags.append(materials.WALL_RIDABLE if squared > 0 and
                     normal[1] ** 2 <= materials.WALLRIDE_NORMAL_Y_SQUARED * squared else 0)
        if not length:
            continue
        normal = (normal[0]/length, normal[1]/length, normal[2]/length)
        if bank_low < normal[1] <= bank_high:
            flags[-1] |= BANK
        if not 0 <= normal[1] <= vert_high:
            continue
        normals[index], parents[index] = normal, index
        points = (a, b, c)
        ids = [welded.setdefault(point, len(welded)) for point in points]
        for first, second, opposite in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
            edge = tuple(sorted((ids[first], ids[second])))
            edges.setdefault(edge, []).append((index, points[opposite]))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for neighbors in edges.values():
        if len(neighbors) != 2:
            continue  # Boundaries and ambiguous overlapping surfaces cannot prove a transition.
        (first, a), (second, b) = neighbors
        n1, n2 = normals[first], normals[second]
        alignment = sum((n1[0]*n2[0], n1[1]*n2[1], n1[2]*n2[2]))
        if alignment < bend_min:
            continue
        offset = (b[0]-a[0], b[1]-a[1], b[2]-a[2])
        first_side = sum((n1[0]*offset[0], n1[1]*offset[1], n1[2]*offset[2]))
        second_side = sum((n2[0]*offset[0], n2[1]*offset[1], n2[2]*offset[2]))
        # Float32 world translation can bend an authored quad across its diagonal.
        coplanar = False
        if alignment >= coplanar_min:
            ulp = max(2.**max(-149, math.frexp(abs(value))[1]-24) if value else 2.**-149 for value in (*a, *b))
            coplanar = max(abs(first_side), abs(second_side)) <= 4*ulp
        if not coplanar and not (first_side > 0 and second_side < 0):
            continue
        parents[root(first)] = root(second)
    extents = {}
    for index, normal in normals.items():
        group = root(index)
        low, high = extents.get(group, (normal[1], normal[1]))
        extents[group] = min(low, normal[1]), max(high, normal[1])
    for index in normals:
        low, high = extents[root(index)]
        if low <= steep and high >= shallow:
            flags[index] |= VERT
    return flags


def surface_policy(flags):
    return {**materials.policy(), 'thaw_face_flags': sorted(set(flags)), 'collision_policy_sha256': fingerprint(),
            'automatic_wallride': materials.wallride_policy(),
            'automatic_bank': dict(face_flag=BANK, minimum_slope_degrees=BANK_MIN_SLOPE,
                                   maximum_slope_degrees_exclusive=BANK_MAX_SLOPE, upward_faces_only=True),
            'automatic_vert': dict(face_flag=VERT, minimum_slope_degrees=VERT_MIN_SLOPE,
                shallow_slope_degrees=VERT_SHALLOW_SLOPE, steep_slope_degrees=VERT_STEEP_SLOPE,
                maximum_adjacent_bend_degrees=VERT_MAX_BEND, concave_transitions_only=True,
                coplanar_maximum_bend_degrees=COPLANAR_MAX_BEND, coplanar_plane_tolerance_float32_ulps=4,
                exact_final_float32_vertex_welding=True, native_solver_unchanged=True)}

