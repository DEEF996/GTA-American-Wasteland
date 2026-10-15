"""Read one original THAW PC PAK/COL and export untouched collision geometry.

Format references (not runtime dependencies):
  slfx77/neversoft-multitool: Core/Formats/{Archives/PakArchive,Collision/ColFile}.cs
  denetii/io_thps_scene: import_thug2.py
Only little-endian THAW COL v10 with the supersector header is supported.
"""

import argparse
import binascii
import hashlib
import json
import math
from pathlib import Path
import struct
import sys


def require(condition, message):
    if not condition:
        raise ValueError(message)


def region(data, offset, size, name):
    require(0 <= offset <= len(data) and 0 <= size <= len(data) - offset,
            f"{name}: byte range [{offset}, {offset + size}) exceeds {len(data)} bytes")
    return memoryview(data)[offset:offset + size]


def qbkey(name):
    return binascii.crc32(name.lower().replace("/", "\\").encode("ascii")) ^ 0xFFFFFFFF


def pak_member(data, member):
    """Resolve header-relative offsets; never write embedded archive filenames."""
    target = qbkey(member)
    cursor, first_payload, found = 0, len(data), []
    while cursor + 32 <= first_payload:
        fields = struct.unpack("<8I", region(data, cursor, 32, "PAK entry"))
        kind, relative, size, name_hash, _, _, _, flags = fields
        if kind in (0xB524565F, 0x2CB3EF3B):
            require(len(found) == 1, f"Expected one {member!r} entry; found {len(found)}")
            return found[0]
        require(flags & ~0x33 == 0, f"Unsupported PAK flags 0x{flags:x}")
        header_size = 192 if flags & 0x20 else 32
        region(data, cursor, header_size, "PAK entry header/name")
        absolute = cursor + relative
        require(absolute >= cursor + header_size, "PAK payload overlaps its header")
        payload = region(data, absolute, size, "PAK payload")
        first_payload = min(first_payload, absolute)
        if name_hash == target and kind == qbkey(".col"):
            found.append((payload, {"entry_checksum": f"0x{target:08x}",
                                    "entry_offset": absolute, "entry_size": size}))
        cursor += header_size
    raise ValueError("PAK entry table has no terminator before payload data")


def vec3(data, offset):
    values = list(struct.unpack("<3f", region(data, offset, 12, "vector")))
    require(all(math.isfinite(value) for value in values), "Non-finite coordinate")
    return values


def parse_col(data):
    header = struct.unpack("<8I", region(data, 0, 32, "COL header"))
    version, count, total_vertices, large_faces, small_faces, large_vertices, small_vertices, _ = header
    require(version == 10, f"Only THAW PC COL v10 is supported; found {version}")
    require(total_vertices == large_vertices + small_vertices, "COL vertex totals disagree")
    region(data, 32, 48, "supersector header")
    require(struct.unpack_from("<I", data, 32)[0] == 0, "Missing THAW supersector header")
    require(0 < count <= (len(data) - 80) // 64, "Invalid COL object count")
    vertex_base = (80 + count * 64 + 15) & ~15
    vertex_bytes = large_vertices * 12 + small_vertices * 6
    intensity_base = vertex_base + vertex_bytes
    face_base = (intensity_base + total_vertices + 3) & ~3
    face_bytes = large_faces * 10 + small_faces * 8
    region(data, vertex_base, vertex_bytes, "vertex pool")
    region(data, intensity_base, total_vertices, "intensity pool")
    region(data, face_base, face_bytes, "face pool")

    positions, indices, face_flags, terrain_types, objects = [], [], [], [], []
    totals = [0, 0, 0, 0]  # large vertices, small vertices, large faces, small faces
    degenerate = 0
    for number in range(count):
        start = 80 + number * 64
        checksum, flags, vertices, faces, small, fixed, face_offset = struct.unpack_from("<IHHHBBI", data, start)
        require(small in (0, 1) and fixed in (0, 1), f"Object {number}: invalid packing flags")
        lower, upper = vec3(data, start + 16), vec3(data, start + 32)
        require(all(lo <= hi for lo, hi in zip(lower, upper)), f"Object {number}: invalid bounds")
        vertex_offset, _, intensity_offset = struct.unpack_from("<IiI", data, start + 48)
        vertex_stride, face_stride = (6 if fixed else 12), (8 if small else 10)
        require(vertex_offset + vertices * vertex_stride <= vertex_bytes,
                f"Object {number}: vertices exceed declared pool")
        require(face_offset + faces * face_stride <= face_bytes,
                f"Object {number}: faces exceed declared pool")
        require(intensity_offset + vertices <= total_vertices,
                f"Object {number}: intensity range exceeds declared pool")
        vertex_start, face_start = len(positions), len(face_flags)
        for vertex in range(vertices):
            offset = vertex_base + vertex_offset + vertex * vertex_stride
            if fixed:
                packed = struct.unpack_from("<3H", data, offset)
                position = [lower[axis] + packed[axis] / 16 for axis in range(3)]
            else:
                position = vec3(data, offset)
            require(lower[0] - 0.125 <= position[0] <= upper[0] + 0.125 and
                    lower[1] - 0.125 <= position[1] <= upper[1] + 0.125 and
                    lower[2] - 0.125 <= position[2] <= upper[2] + 0.125,
                    f"Object {number}: vertex {vertex} outside its source bounds")
            positions.append(position)
        for face in range(faces):
            offset = face_base + face_offset + face * face_stride
            face_flag, terrain, ia, ib, ic = struct.unpack_from("<2H3B" if small else "<5H", data, offset)
            require(ia < vertices and ib < vertices and ic < vertices, f"Object {number}: invalid triangle index")
            global_triangle = [vertex_start + ia, vertex_start + ib, vertex_start + ic]
            indices.extend(global_triangle)
            face_flags.append(face_flag)
            terrain_types.append(terrain)
            a, b, c = positions[global_triangle[0]], positions[global_triangle[1]], positions[global_triangle[2]]
            ab, ac = (b[0]-a[0], b[1]-a[1], b[2]-a[2]), (c[0]-a[0], c[1]-a[1], c[2]-a[2])
            nx, ny, nz = ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2], ab[0]*ac[1]-ab[1]*ac[0]
            degenerate += sum((nx*nx, ny*ny, nz*nz)) == 0
        totals[fixed] += vertices
        totals[2 + small] += faces
        objects.append({"checksum": f"0x{checksum:08x}", "flags": flags,
                        "vertex_start": vertex_start, "vertex_count": vertices,
                        "face_start": face_start, "face_count": faces,
                        "bounds": {"min": lower, "max": upper}})
    require(totals == [large_vertices, small_vertices, large_faces, small_faces],
            "Object vertex/face totals disagree with COL header")
    require(positions and len(face_flags) > degenerate, "No non-degenerate source geometry")
    bounds = {"min": [min(v[i] for v in positions) for i in range(3)],
              "max": [max(v[i] for v in positions) for i in range(3)]}
    return {"format": "thaw-col-v10", "coordinates": {"axes": "original THAW XYZ (Y up)",
            "scale": 1, "units": "original game units"}, "bounds": bounds,
            "positions": positions, "indices": indices, "face_flags": face_flags,
            "terrain_types": terrain_types, "objects": objects,
            "stats": {"objects": count, "vertices": len(positions), "triangles": len(face_flags),
                      "degenerate_triangles_preserved": degenerate,
                      "bounds_checked": True, "indices_checked": True}}


def export(pak, member, output):
    require(pak.resolve() != output.resolve(), "Output must not overwrite source archive")
    require(pak.stat().st_size <= 512 * 1024 * 1024, "PAK exceeds 512 MiB export limit")
    data = pak.read_bytes()
    payload, source = pak_member(data, member)
    scene = parse_col(payload)
    source.update({"pak": str(pak.resolve()), "member": member,
                   "sha256": hashlib.sha256(data).hexdigest(),
                   "col_sha256": hashlib.sha256(payload).hexdigest()})
    scene["source"] = source
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects both existing exports and accidentally selected game files.
    with output.open("x", encoding="utf-8") as file:
        json.dump(scene, file, separators=(",", ":"), allow_nan=False)
    return {"output": str(output.resolve()), **scene["stats"], "bounds": scene["bounds"],
            "source_sha256": source["sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pak", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--member", help="Archive path without .wpc; defaults to the PAK's worldzone COL")
    args = parser.parse_args()
    zone = args.pak.name.split(".")[0].lower()
    member = args.member or f"worlds/worldzones/{zone}/{zone}.col"
    try:
        print(json.dumps(export(args.pak, member, args.output), indent=2))
    except (OSError, ValueError, struct.error) as error:
        print(f"Asset export failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
