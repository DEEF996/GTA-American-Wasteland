"""Compile triangle data to THAW PC COL v10; patch a COPY of one PAK member.

No game process or installed file is changed. Geometry is data for the original
THAW collision solver, not a replacement solver. Rails/restarts are separate QB
nodes and are not created by this tool. Format evidence: NXTools author's
formats/fmt_thcol_export.py and inspected original reTHAWed COL loader.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

from assets_export import parse_col, qbkey, region, require


def align(value, multiple):
    return (value + multiple - 1) & -multiple


def transform_point(point, coordinates, scale, offset):
    require(len(point) == 3 and all(math.isfinite(x) for x in point), "Invalid source coordinate")
    x, y, z = point
    point = (x, z, -y) if coordinates == "cod" else (x, y, z)
    result = tuple(struct.unpack("<f", struct.pack("<f", point[i] * scale + offset[i]))[0] for i in range(3))
    require(all(math.isfinite(x) and abs(x) <= 10_000_000 for x in result), "Transformed coordinate out of range")
    return result


def partition(triangles, positions):
    """Spatially bounded objects; original BSP format permits one leaf per object."""
    if len(triangles) <= 50:
        yield triangles
        return
    centers = {face: tuple(sum(positions[v][a] for v in triangle) / 3 for a in range(3))
               for face, triangle in triangles}

    def divide(rows):
        if len(rows) <= 50:
            yield rows
            return
        selected = [centers[face] for face, _ in rows]
        axis = max(range(3), key=lambda a: max(c[a] for c in selected) - min(c[a] for c in selected))
        rows = sorted(rows, key=lambda item: centers[item[0]][axis])
        middle = len(rows) // 2
        yield from divide(rows[:middle])
        yield from divide(rows[middle:])

    yield from divide(triangles)


def compile_col(source, coordinates="cod", scale=1.0, offset=(0.0, 0.0, 0.0), face_flag=0, terrain=0,
                return_face_order=False):
    """Build COL bytes; optionally also return the serialized source face order."""
    require(coordinates in ("cod", "thaw"), "Unknown coordinate system")
    require(math.isfinite(scale) and scale > 0, "Scale must be finite and positive")
    require(len(offset) == 3 and all(math.isfinite(x) for x in offset), "Invalid translation")
    require(0 <= face_flag <= 0xFFFF and 0 <= terrain <= 0xFFFF, "THAW flag/terrain must fit u16")
    require(type(return_face_order) is bool, "Face order output must be explicitly enabled or disabled")
    positions = [transform_point(p, coordinates, scale, offset) for p in source["positions"]]
    raw_indices = source["indices"]
    require(positions and raw_indices, "Empty source geometry")
    if isinstance(raw_indices[0], (list, tuple)):
        triangles = [tuple(triangle) for triangle in raw_indices]
    else:
        require(len(raw_indices) % 3 == 0, "Triangle index count is not divisible by three")
        triangles = [tuple(raw_indices[i:i + 3]) for i in range(0, len(raw_indices), 3)]
    require(len(triangles) <= 1_000_000, "Source exceeds one million triangles")
    require(all(len(t) == 3 and all(type(i) is int and 0 <= i < len(positions) for i in t) for t in triangles),
            "Invalid source triangle index")
    if source.get("format") == "waw-clipmap-v1":
        contents = source.get("content_flags", [])
        require(len(contents) == len(triangles) and all(value & 1 for value in contents),
                "Use the WORLD+CONTENTS_SOLID export; non-solid CoD volumes are not THAW surfaces")
        require(all(obj.get("submodel") == 0 for obj in source.get("objects", [])),
                "Local moving-submodel geometry needs its original world transform")
        # The verified CoD terrain triangle winding points inward; reconstructed
        # convex brush faces already point outward. THAW original floor faces
        # use upward normals. The x,z,-y rotation itself preserves handedness.
        for obj in source.get("objects", []):
            if obj["kind"] == "terrain":
                start, count = obj["first_triangle"], obj["triangle_count"]
                require(0 <= start <= len(triangles) and 0 <= count <= len(triangles) - start, "Invalid terrain triangle range")
                for index in range(start, start + count):
                    a, b, c = triangles[index]
                    triangles[index] = (a, c, b)
    # CoD content/surface flags have unrelated meanings; never copy those as THAW flags.
    flags = source.get("face_flags", [face_flag] * len(triangles)) if source.get("format") == "thaw-col-v10" else [face_flag] * len(triangles)
    terrains = source.get("terrain_types", [terrain] * len(triangles)) if source.get("format") == "thaw-col-v10" else [terrain] * len(triangles)
    if source.get("format") == "waw-clipmap-v1":
        # Original skutils mFD_WALL_RIDABLE=4 gates 5D6F00 via contact query 5D3B10.
        # This authors conservative wall metadata; VERT/BANK need ramp topology.
        maximum_wall_normal_y_squared = math.sin(math.radians(5)) ** 2
        for index, triangle in enumerate(triangles):
            a, b, c = (positions[vertex] for vertex in triangle)
            ab = [b[axis] - a[axis] for axis in range(3)]
            ac = [c[axis] - a[axis] for axis in range(3)]
            normal = (ab[1] * ac[2] - ab[2] * ac[1],
                      ab[2] * ac[0] - ab[0] * ac[2],
                      ab[0] * ac[1] - ab[1] * ac[0])
            length_squared = sum(value * value for value in normal)
            if length_squared > 0 and normal[1] ** 2 <= maximum_wall_normal_y_squared * length_squared:
                flags[index] |= 0x4
    require(len(flags) == len(triangles) == len(terrains), "Face metadata count mismatch")
    require(all(type(x) is int and 0 <= x <= 0xFFFF for x in flags + terrains), "Invalid THAW face metadata")
    metadata, vertices, intensities, faces, bsp, bsp_faces = (bytearray() for _ in range(6))
    face_order = [] if return_face_order else None
    count = 0
    for chunk in partition(list(enumerate(triangles)), positions):
        if face_order is not None:
            face_order.extend(source_face for source_face, _ in chunk)
        local = {}
        for _, triangle in chunk:
            for index in triangle:
                if index not in local:
                    local[index] = len(local)
        points = [positions[index] for index in local]
        lower = [min(p[a] for p in points) for a in range(3)]
        upper = [max(p[a] for p in points) for a in range(3)]
        metadata.extend(struct.pack("<IHHHBBI4f4fIIIf", qbkey(f"carentan_collision_{count}"), 0,
                                    len(points), len(chunk), 0, 0, len(faces), *lower, 1.0, *upper, 1.0,
                                    len(vertices), len(bsp), len(intensities), 0.0))
        for point in points:
            vertices.extend(struct.pack("<3f", *point))
            intensities.append(128)
        for index, (source_face, triangle) in enumerate(chunk):
            faces.extend(struct.pack("<5H", flags[source_face], terrains[source_face], *(local[v] for v in triangle)))
            bsp_faces.extend(struct.pack("<H", index))
        bsp.extend(struct.pack("<BBHI", 3, 0, len(chunk), len(bsp_faces) // 2 - len(chunk)))
        count += 1
    lower = [min(p[a] for p in positions) for a in range(3)]
    upper = [max(p[a] for p in positions) for a in range(3)]
    rows, columns = (max(1, int((upper[a] - lower[a]) / 10000)) for a in (0, 2))
    require(rows * columns <= 65536, "Scene supersector grid exceeds the compiler limit")
    vertex_count = len(vertices) // 12
    result = bytearray(struct.pack("<12I4f4f", 10, count, vertex_count, len(triangles), 0,
                                  vertex_count, 0, 0, 0, 0, rows, columns, *lower, 1.0, *upper, 1.0))
    result.extend(metadata)
    result.extend(vertices)
    result.extend(intensities)
    result.extend(bytes(align(len(result), 4) - len(result)))
    result.extend(faces)
    result.extend(bytes(len(triangles)))  # One optimization byte per LARGE face.
    result.extend(bytes(align(len(result), 4) - len(result)))
    result.extend(struct.pack("<I", len(bsp)))
    result.extend(bsp)
    result.extend(bsp_faces)
    parsed = parse_col(result)
    validate_bsp(result)
    require(parsed["stats"]["triangles"] == len(triangles), "Generated COL face count changed")
    return (bytes(result), face_order) if return_face_order else bytes(result)


def validate_bsp(data):
    """Validate full native tail, including the original retail split-node layout."""
    version, count, total, large_faces, small_faces, large_verts, small_verts, _ = struct.unpack_from("<8I", data)
    require(version == 10, "BSP validation requires THAW COL v10")
    vertex_base = align(80 + count * 64, 16)
    face_base = align(vertex_base + large_verts * 12 + small_verts * 6 + total, 4)
    tail = align(face_base + large_faces * 10 + small_faces * 8 + large_faces, 4)
    node_size, = struct.unpack("<I", region(data, tail, 4, "BSP size"))
    node_base = tail + 4
    region(data, node_base, node_size, "BSP nodes")
    face_base = node_base + node_size
    require(node_size % 8 == 0 and (len(data) - face_base) % 2 == 0, "Invalid BSP pool alignment")
    for obj in range(count):
        object_faces = struct.unpack_from("<H", data, 80 + obj * 64 + 8)[0]
        root = struct.unpack_from("<I", data, 80 + obj * 64 + 52)[0]
        pending, visited, covered = [root], set(), set()
        while pending:
            node = pending.pop()
            require(node % 8 == 0 and node + 8 <= node_size and node not in visited, "Invalid/cyclic BSP node")
            visited.add(node)
            first, second = struct.unpack_from("<2I", data, node_base + node)
            if first & 3 == 3:
                require(first & 0xFFFF == 3, "Unsupported BSP leaf flags")
                face_count = first >> 16
                indices = region(data, face_base + second * 2, face_count * 2, "BSP face references")
                for index, in struct.iter_unpack("<H", indices):
                    require(index < object_faces, "BSP references a face outside its object")
                    covered.add(index)
            else:
                pending.extend((second, second + 8))
        require(len(covered) == object_faces, "BSP does not cover all object faces")


def replace_pak_member(data, member, payload, member_type=None):
    """Preserve every other member and the original footer, shifting offsets only."""
    target = qbkey(member)
    member_type = qbkey(".col") if member_type is None else member_type
    entries, found, cursor, first_payload = [], [], 0, len(data)
    while cursor + 32 <= first_payload:
        entry = struct.unpack("<8I", region(data, cursor, 32, "PAK header"))
        kind, relative, size, checksum, _, _, _, flags = entry
        start = cursor + relative
        region(data, start, size, "PAK payload")
        entries.append((cursor, start, size))
        if kind in (0xB524565F, 0x2CB3EF3B):
            break
        require(flags & ~0x33 == 0, "Unsupported PAK flags")
        first_payload = min(first_payload, start)
        if checksum == target and kind == member_type:
            found.append((cursor, start, size))
        cursor += 192 if flags & 0x20 else 32
    else:
        raise ValueError("PAK has no terminator")
    require(len(found) == 1, "Expected exactly one target PAK member/type")
    header, start, size = found[0]
    following = min((offset for _, offset, _ in entries if offset > start), default=len(data))
    require(start + size <= following and start % 32 == 0 and following % 32 == 0, "Unsupported PAK payload layout")
    end = start + align(len(payload), 32)
    delta = end - following
    result = bytearray(data[:start]) + payload + bytes(end - start - len(payload)) + data[following:]
    for entry_offset, old_start, old_size in entries:
        new_start = old_start + delta if old_start >= following else old_start
        struct.pack_into("<I", result, entry_offset + 4, new_start - entry_offset)
        if entry_offset == header:
            struct.pack_into("<I", result, entry_offset + 8, len(payload))
        else:
            require(result[new_start:new_start + old_size] == data[old_start:old_start + old_size],
                    "An unrelated PAK member changed")
    return bytes(result)


def self_test():
    """Small runnable check: python -c \"import carentan_col; carentan_col.self_test()\"."""
    from assets_export import pak_member
    source = {"positions": [[0, 0, 0], [128, 0, 0], [128, 128, 0], [0, 128, 0]],
              "indices": [[0, 1, 2], [0, 2, 3]]}
    original = compile_col(source)
    assert parse_col(original)["positions"] == [[0, 0, 0], [128, 0, 0], [128, 0, -128], [0, 0, -128]]
    assert transform_point([40, -1232, 152], "cod", 1, (0, 0, 0)) == (40, 152, 1232)
    member = "worlds/worldzones/z_el/z_el.col"
    other_offset = 96 + align(len(original), 32)
    pak = bytearray(struct.pack("<8I", qbkey(".col"), 96, len(original), qbkey(member), 0, 0, 0, 0))
    pak.extend(struct.pack("<8I", qbkey(".qb"), other_offset - 32, 4, 123, 0, 0, 0, 0))
    pak.extend(struct.pack("<8I", 0xB524565F, other_offset + 32 - 64, 4, 0, 0, 0, 0, 0))
    pak.extend(original)
    pak.extend(bytes(other_offset - len(pak)))
    pak.extend(b"KEEP" + bytes(28) + b"\xab" * 4)
    source["indices"] *= 75
    replacement = compile_col(source)
    assert parse_col(replacement)["stats"]["objects"] == 4
    patched = replace_pak_member(pak, member, replacement)
    assert bytes(pak_member(patched, member)[0]) == replacement
    source["indices"] = [[0, 1, 999]]
    try:
        compile_col(source)
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid source index was accepted")
    # End-to-end metadata checks include both windings, slopes straddling the
    # policy boundary, horizontal surfaces and native degenerate preservation.
    wall = [[0, 0, 0], [0, 100, 0], [0, 0, 100]]
    floor = [[0, 0, 0], [100, 0, 0], [0, 100, 0]]
    shapes = [wall, list(reversed(wall)),
              [wall[0], wall[1], [100 * math.tan(math.radians(4)), 0, 100]],
              [wall[0], wall[1], [100 * math.tan(math.radians(6)), 0, 100]],
              floor, list(reversed(floor)), [[0, 0, 0]] * 3]
    geometry = {"positions": [point for shape in shapes for point in shape],
                "indices": [[3 * i, 3 * i + 1, 3 * i + 2] for i in range(len(shapes))]}
    waw = {**geometry, "format": "waw-clipmap-v1", "content_flags": [1] * len(shapes),
           "objects": [{"kind": "terrain", "submodel": 0, "first_triangle": 5, "triangle_count": 1}]}
    authored = parse_col(compile_col(waw))
    assert authored["face_flags"] == [4, 4, 4, 0, 0, 0, 0]
    assert authored["terrain_types"] == [0] * len(shapes)
    assert authored["stats"]["degenerate_triangles_preserved"] == 1
    assert authored["indices"][15:18] == [15, 16, 17]
    assert authored["positions"][15:18] == [[0, 0, -100], [0, 0, 0], [100, 0, 0]]
    assert parse_col(compile_col(waw, scale=2, offset=(7, -3, 11)))["face_flags"] == authored["face_flags"]
    assert parse_col(compile_col(waw, face_flag=0x100))["face_flags"] == [0x104] * 3 + [0x100] * 4
    # Generic geometry keeps explicit flags; imported native THAW metadata is
    # authoritative even when its authored walls differ from our map policy.
    assert parse_col(compile_col(geometry))["face_flags"] == [0] * len(shapes)
    native_flags = [0, 1, 8, 2, 0x104, 0x100, 0x10]
    native = {**geometry, "format": "thaw-col-v10", "face_flags": native_flags, "terrain_types": list(range(7))}
    copied = parse_col(compile_col(native, coordinates="thaw"))
    assert copied["face_flags"] == native_flags and copied["terrain_types"] == list(range(7))
    assert native["face_flags"] == native_flags
    for invalid in ({**waw, "content_flags": [0] * len(shapes)},
                    {**waw, "objects": [{"kind": "brush", "submodel": 1}]}):
        try:
            compile_col(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("Non-solid or untransformed moving collision was accepted")
    print("PASS: coordinates, spatial chunks, COL/BSP, PAK preservation, automatic wall metadata, native flags, invalid input")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Exported triangle JSON; CoD defaults to x,z,-y")
    parser.add_argument("output", type=Path, help="New .col.wpc output; existing files are refused")
    parser.add_argument("--coordinates", choices=("cod", "thaw"), default="cod")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--offset", type=float, nargs=3, default=(0.0, 0.0, 0.0))
    parser.add_argument("--face-flag", type=lambda x: int(x, 0), default=0)
    parser.add_argument("--terrain", type=int, default=0)
    parser.add_argument("--base-pak", type=Path)
    parser.add_argument("--output-pak", type=Path)
    parser.add_argument("--member", default="worlds/worldzones/z_el/z_el.col")
    args = parser.parse_args()
    require(bool(args.base_pak) == bool(args.output_pak), "Supply both --base-pak and --output-pak")
    require(not args.output.exists() and (not args.output_pak or not args.output_pak.exists()), "Outputs must be new files")
    require(not args.output_pak or args.output.resolve() != args.output_pak.resolve(), "Outputs must have different paths")
    source = json.loads(args.source.read_text(encoding="utf-8"))
    payload = compile_col(source, args.coordinates, args.scale, args.offset, args.face_flag, args.terrain)
    patched = replace_pak_member(args.base_pak.read_bytes(), args.member, payload) if args.base_pak else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream:
        stream.write(payload)
    if args.output_pak:
        args.output_pak.parent.mkdir(parents=True, exist_ok=True)
        with args.output_pak.open("xb") as stream:
            stream.write(patched)
    report = {"col": str(args.output.resolve()), "col_sha256": hashlib.sha256(payload).hexdigest(),
              "bytes": len(payload), "coordinates": args.coordinates, "scale": args.scale, "offset": args.offset,
              "stats": parse_col(payload)["stats"], "runtime_tested": False,
              "qb_nodes": "Unchanged. Original level restarts, rails and scripted objects still need replacement."}
    if source.get("spawn", {}).get("origin"):
        report["transformed_source_spawn"] = transform_point(source["spawn"]["origin"], args.coordinates, args.scale, args.offset)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
