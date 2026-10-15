"""Export original THAW PC THPS7_Human hierarchy/bind data; no game code runs.

Reference: NeversoftMultitool/Core/Formats/Mesh/Ps2Scene/Skeleton/ThawSkeletonFile.cs.
The exporter retains serialized row-vector matrices, original indices/checksums,
and stored local vec4s. Derived bind matrices are inspection data, not animation.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys

from assets_export import qbkey, region, require


def multiply(a, b):
    result = []
    for row in (0, 4, 8, 12):
        x, y, z, w = a[row], a[row+1], a[row+2], a[row+3]
        # Keep sum's floating-point semantics without a generator per cell.
        result.extend([sum((x*b[c], y*b[c+4], z*b[c+8], w*b[c+12])) for c in (0, 1, 2, 3)])
    return result


def inverse(matrix):
    rows = [matrix[i * 4:i * 4 + 4] + [float(i == j) for j in range(4)] for i in range(4)]
    for column in range(4):
        pivot = max(range(column, 4), key=lambda i: abs(rows[i][column]))
        require(abs(rows[pivot][column]) > 1e-12, "Singular inverse bind matrix")
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        rows[column] = [value / divisor for value in rows[column]]
        for row in range(4):
            if row != column:
                factor = rows[row][column]
                rows[row] = [a - factor * b for a, b in zip(rows[row], rows[column])]
    return [value for row in rows for value in row[4:]]


def parse_skeleton(data):
    header = struct.unpack("<HH11I", region(data, 0, 48, "SKE header"))
    version, header_size, count, zero1, zero2, *offsets = header
    require(version == 1 and header_size == 48 and 0 < count <= 256,
            "Not a supported THAW PC skeleton")
    require(zero1 == zero2 == 0, "Unknown SKE header fields")
    names_at, parents_at, flips_at, flip_indices_at, flags_at, tail_at, matrices_at, vectors_at = offsets
    require(vectors_at == 48 and matrices_at == 48 + count * 16,
            "Unexpected SKE transform offsets")
    expected = 48 + count * 80
    for offset in offsets[:6]:
        require(offset == expected, "Unexpected SKE metadata array layout")
        expected += count * 4
    require(tail_at + 128 == len(data), "SKE length/trailer mismatch")
    require(bytes(region(data, tail_at, 128, "SKE trailer")) == struct.pack("<I", 0x7F7FC99E) * 32,
            "Unexpected SKE trailer")
    arrays = [struct.unpack_from(f"<{count}I", data, offset) for offset in offsets[:5]]
    names, parents, flips, flip_indices, flags = arrays
    require(len(set(names)) == count, "Duplicate bone checksums")
    lookup = {checksum: index for index, checksum in enumerate(names)}
    parent_indices = []
    for index, checksum in enumerate(parents):
        require(checksum == 0 or checksum in lookup, f"Bone {index}: unknown parent checksum")
        parent_indices.append(-1 if checksum == 0 else lookup[checksum])
    for index in range(count):
        seen, cursor = set(), index
        while cursor != -1:
            require(cursor not in seen, "Cycle in bone parent hierarchy")
            seen.add(cursor)
            cursor = parent_indices[cursor]
    inverse_binds, bind_models, vectors = [], [], []
    for index in range(count):
        matrix = list(struct.unpack_from("<16f", data, matrices_at + index * 64))
        vector = list(struct.unpack_from("<4f", data, vectors_at + index * 16))
        require(all(math.isfinite(value) for value in matrix + vector), "Non-finite SKE transform")
        require(all(abs(matrix[i] - v) <= 1e-5 for i, v in zip((3, 7, 11, 15), (0, 0, 0, 1))),
                "SKE matrix is not affine in the expected row-vector convention")
        bind = inverse(matrix)
        identity = multiply(matrix, bind)
        require(max(abs(value - float(i // 4 == i % 4)) for i, value in enumerate(identity)) < 1e-5,
                "Unstable inverse bind matrix")
        inverse_binds.append(matrix)
        bind_models.append(bind)
        vectors.append(vector)
    bones, max_translation_error = [], 0.0
    for index in range(count):
        parent = parent_indices[index]
        local = multiply(bind_models[index], inverse_binds[parent]) if parent >= 0 else bind_models[index]
        error = max(abs(local[12 + axis] - vectors[index][axis]) for axis in range(3))
        require(error < 0.001, f"Bone {index}: derived local translation disagrees with stored vector")
        max_translation_error = max(max_translation_error, error)
        require(flip_indices[index] == 0xFFFFFFFF or flip_indices[index] < count,
                f"Bone {index}: invalid flip index")
        bones.append({"index": index, "checksum": names[index], "name": None,
                      "parent_checksum": parents[index], "parent_index": parent,
                      "flip_checksum": flips[index], "flip_index": flip_indices[index], "flags": flags[index],
                      "stored_local_vec4": vectors[index], "inverse_bind_row_major": inverse_binds[index],
                      "bind_model_row_major": bind_models[index], "local_bind_row_major": local,
                      "bind_model_position": bind_models[index][12:15]})
    return {"format": "thaw-ske-v1", "bone_count": count, "bones": bones,
            "coordinates": {"scale": 1, "units": "original game units",
                            "matrices": "row-major serialized row-vector convention; translation indices 12..14",
                            "axes": "unaltered source skeleton; local bone axes are not world axes"},
            "validation": {"hierarchy_acyclic": True, "inverse_bind_checked": True,
                           "max_local_translation_error": max_translation_error}}


def extract_entry(pak, checksum):
    data = pak.read_bytes()
    cursor, matches = 0, []
    while cursor + 32 <= len(data):
        kind, offset, size, name, _, _, _, flags = struct.unpack_from("<8I", data, cursor)
        if kind in (0xB524565F, 0x2CB3EF3B):
            break
        require(flags & ~0x33 == 0, "Unsupported PAK family")
        header_size = 192 if flags & 0x20 else 32
        region(data, cursor, header_size, "PAK header")
        if kind == qbkey(".ske") and name == checksum:
            matches.append((cursor + offset, size))
        cursor += header_size
    else:
        raise ValueError("Missing PAK table terminator")
    require(len(matches) == 1, f"Expected one SKE checksum 0x{checksum:08x}, found {len(matches)}")
    offset, size = matches[0]
    container = pak
    if offset + size > len(data):
        container = pak.with_name(pak.name.replace(".pak.", ".pab."))
        offset -= len(data)
        data = container.read_bytes()
    return bytes(region(data, offset, size, "SKE payload")), {
        "pak": str(pak.resolve()), "container": str(container.resolve()),
        "container_sha256": hashlib.sha256(data).hexdigest(), "entry_offset": offset,
        "entry_size": size, "entry_checksum": checksum}


def export(game, output):
    pak = game / "data/worlds/Global/global.pak.wpc"
    payload, source = extract_entry(pak, 0x39937D56)
    result = parse_skeleton(payload)
    wanted = {bone["checksum"] for bone in result["bones"]} | {0x39937D56}
    labels = {}
    debug = game / "data/pak/dbg.pak.wpc"
    debug_data = debug.read_bytes()
    for match in re.finditer(rb"0x([0-9a-fA-F]{8}) ([^\r\n\x00]+)", debug_data):
        checksum = int(match[1], 16)
        if checksum in wanted:
            try:
                label = match[2].decode("ascii")
                if qbkey(label) == checksum:
                    labels.setdefault(checksum, label)
            except UnicodeError:
                continue
    for bone in result["bones"]:
        bone["name"] = labels.get(bone["checksum"])
    source.update({"skeleton_name": labels.get(0x39937D56), "ske_sha256": hashlib.sha256(payload).hexdigest(),
                   "name_dictionary": str(debug.resolve()), "dictionary_sha256": hashlib.sha256(debug_data).hexdigest(),
                   "runtime_profile_match": "not yet verified; compare native bone count and checksums"})
    result["source"] = source
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as file:
        json.dump(result, file, separators=(",", ":"), allow_nan=False)
    return {"output": str(output.resolve()), "bone_count": result["bone_count"],
            "resolved_bone_names": sum(bone["name"] is not None for bone in result["bones"]),
            "source": source, "validation": result["validation"]}


def self_test(game=None):
    matrix = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 10., 20., 30., 1.]
    assert inverse(matrix)[12:15] == [-10, -20, -30]
    for data in (b"", bytes(48), bytes(300)):
        try:
            parse_skeleton(data)
        except ValueError:
            continue
        raise AssertionError("Malformed skeleton accepted")
    try:
        inverse([0.] * 16)
    except ValueError:
        pass
    else:
        raise AssertionError("Singular matrix accepted")
    if game is not None:
        payload, _ = extract_entry(game / "data/worlds/Global/global.pak.wpc", 0x39937D56)
        parsed = parse_skeleton(payload)
        assert parsed["bone_count"] == 52
        names, parents, _, _, _, _, matrices, _ = struct.unpack_from("<8I", payload, 16)
        first_name = struct.unpack_from("<I", payload, names)[0]
        bad_inputs = [payload[:-1]]
        for offset, value in ((names + 4, first_name), (parents, first_name),
                              (16, 0xFFFFFFFF), (matrices, 0x7FC00000)):
            bad = bytearray(payload)
            struct.pack_into("<I", bad, offset, value)
            bad_inputs.append(bad)
        bad = bytearray(payload)
        bad[matrices:matrices + 64] = bytes(64)
        bad_inputs.append(bad)
        for bad in bad_inputs:
            try:
                parse_skeleton(bad)
            except ValueError:
                continue
            raise AssertionError("Damaged source skeleton accepted")
        print("PASS: original 52-bone rig; rejects truncation, duplicate names, hierarchy cycles, bad offsets and NaN/singular matrices")
    print("PASS: known affine inverse, malformed headers, singular-matrix rejection")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game", type=Path, nargs="?")
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(args.game)
        return
    if args.game is None or args.output is None:
        parser.error("game directory and output JSON are required")
    try:
        print(json.dumps(export(args.game, args.output), indent=2))
    except (OSError, ValueError, struct.error) as error:
        print(f"Skeleton export failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
