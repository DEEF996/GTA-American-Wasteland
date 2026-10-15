"""Prepare immutable original-board data for the bounded GTA pose preview.

CPU skinning uses the original four native palettes, weights, topology and UVs.
No process access, procedural geometry, game installation or native execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

from retarget_pose import decode_bits, prepare as calibration, require, rigid, transform
from waw.build_board import read_export_png

HEADER = struct.Struct("<8s6I32s32s32s16f")
MESH = struct.Struct("<8I")
TEXTURE = struct.Struct("<4I")
VERTEX = struct.Struct("<6fI2f4B4f")
PREFIX_SIZE = HEADER.size + 5*MESH.size + 5*TEXTURE.size
PIXEL_OFFSET = PREFIX_SIZE + 558*VERTEX.size + 1656*2
MAX_BYTES = 1048576
MODEL_SHA = "c2962d8dd78bf35c8badc4062773b3c9c9af2f059930400c2853107fe1a6c2dc"
PNG_HASHES = {
    "5b1931a5.png": "48cfacfa7d3f3ab65bd7854617a8f6e19d18006a9eb4eaabe49b10fbcc64c60f",
    "7f7b1e0b.png": "027fb6f97ef789b80601e7e326ced5578df15f70d7827096ae53d01f747bd54d",
    "e2165983.png": "e4a20d967a3a63c4d17570dea981a52aa7cb1deeb639c8d251b8150c68ce54df",
    "0132852a.png": "039509cb3a38d33cf4fbf7226d6355dcab2ef1657a9858419f57953b0f9de3b2",
    "2bf4827c.png": "6ed28d0771200a26b93689aa531a7c99cb8e1da8f94b978eaced8d9234c33840",
}


def sha(data):
    return hashlib.sha256(data).digest()


def triangle_indices(triangles, vertex_count):
    # assets_models already expands source strips into ordered triangle triplets.
    require(bool(triangles) and all(isinstance(t, list) and len(t) == 3 for t in triangles),
            "Expected exported triangle triplets")
    flat = [index for triangle in triangles for index in triangle]
    require(all(type(i) is int and 0 <= i < vertex_count for i in flat), "Invalid local triangle index")
    return flat


def skin_vertex(position, normal, bones, weights, palettes, basis, scale, source_origin, target_origin):
    require(len(position) == len(normal) == 3 and len(bones) == len(weights) == 4,
            "Invalid source vertex layout")
    require(all(math.isfinite(x) for x in position+normal+weights)
            and all(0 <= w <= 1 for w in weights) and abs(sum(weights)-1) < 1e-6,
            "Invalid source vertex weights")
    point, direction = [0., 0., 0.], [0., 0., 0.]
    for bone, weight in zip(bones, weights):
        if not weight:
            continue
        require(type(bone) is int and bone in (48, 49, 50, 51), "Unsupported weighted board bone")
        p = transform(position, palettes[bone])
        n = [sum(normal[k]*palettes[bone][k*4+c] for k in range(3)) for c in range(3)]
        for axis in range(3):
            point[axis] += p[axis]*weight
            direction[axis] += n[axis]*weight
    point = transform([point[i]-source_origin[i] for i in range(3)], basis)
    point = [point[i]*scale+target_origin[i] for i in range(3)]
    direction = transform(direction, basis)
    length = math.sqrt(sum(x*x for x in direction))
    require(length > 1e-6, "Degenerate skinned normal")
    return point, [x/length for x in direction]


def validate_bundle(data, bind_bytes, rig_hash):
    require(PIXEL_OFFSET <= len(data) <= MAX_BYTES, "Invalid board bundle size")
    h = HEADER.unpack_from(data)
    require(h[:7] == (b"GTABRD02", 2, len(data), 5, 558, 1656, 5), "Invalid board bundle header")
    require(h[7] == sha(bind_bytes) and h[8] == rig_hash and h[9].hex() == MODEL_SHA,
            "Board bundle belongs to a different bind, rig or source model")
    conversion = list(h[10:])
    scale = math.sqrt(sum(v*v for v in conversion[:3]))
    require(math.isfinite(scale) and .0001 <= scale <= 1, "Unbounded visual calibration scale")
    unscaled = [v/scale if i < 12 and i % 4 != 3 else v for i, v in enumerate(conversion)]
    rigid(unscaled)
    require(max(abs(v) for v in conversion[12:15]) <= 10, "Unbounded visual calibration origin")
    vfirst, ifirst = 0, 0
    indices = struct.unpack_from("<1656H", data, PREFIX_SIZE+558*VERTEX.size)
    for i in range(5):
        vf, vc, ix, ic, texture, au, av, cutoff = MESH.unpack_from(data, HEADER.size+i*MESH.size)
        require(vf == vfirst and ix == ifirst and 0 < vc <= 558-vf and 0 < ic <= 1656-ix
                and ic % 3 == 0 and texture < 5 and au in (1, 3) and av in (1, 3)
                and cutoff <= 1 and all(n < vc for n in indices[ix:ix+ic]), "Invalid board mesh range")
        vfirst += vc
        ifirst += ic
    require((vfirst, ifirst) == (558, 1656), "Incomplete board topology")
    for i in range(558):
        v = VERTEX.unpack_from(data, PREFIX_SIZE+i*VERTEX.size)
        require(all(math.isfinite(x) for x in v) and max(abs(x) for x in v[:3]) <= 10000
                and .000001 < sum(x*x for x in v[3:6]) < 4 and v[6] == 0x80808080
                and max(abs(x) for x in v[7:9]) <= 128 and all(0 <= w <= 1 for w in v[13:])
                and abs(sum(v[13:])-1) < 1e-6
                and all(w == 0 or bone in (48,49,50,51) for bone,w in zip(v[9:13],v[13:])),
                "Invalid original board vertex/weights")
    offset = PIXEL_OFFSET
    for i in range(5):
        width, height, start, size = TEXTURE.unpack_from(data, HEADER.size+5*MESH.size+i*TEXTURE.size)
        require(0 < width <= 256 and 0 < height <= 256 and width & (width-1) == height & (height-1) == 0
                and start == offset and size == width*height*4 and size <= len(data)-offset,
                "Invalid board texture range")
        offset += size
    require(offset == len(data), "Trailing board bundle data")
    return sha(data)


def build(model, textures, source, target, record, prepared, rig_hash):
    require(model["format"] == "thaw-model-v1" and len(model["meshes"]) == len(model["materials"]) == 5
            and not model["unresolved_texture_checksums"], "Unsupported board model")
    p = calibration(source, target)
    raw, values = decode_bits(record["palette_bits"], 832)
    frame = prepared["report"]["frames"][prepared["frame_index"]]
    require(sha(raw).hex() == record["palette_sha256"] == frame["source_palette_sha256"]
            and record["time"] == frame["time"] and record["active_bones"] == 52,
            "Board animation is not the selected original pose")
    palettes = [values[i:i+16] for i in range(0, 832, 16)]
    for bone in (48, 49, 50, 51):
        rigid(palettes[bone])
    scale = p["translation_transfer"][2]
    target_origin = p["target_bind"][p["translation_transfer"][0]][12:15]
    source_origin = p["source_bind"][1][12:15]
    materials = {m["checksum"]: m for m in model["materials"]}
    tex_lookup = {t["checksum"]: i for i, t in enumerate(model["textures"])}
    vertices, indices, meshes, points = bytearray(), [], [], []
    for mesh in model["meshes"]:
        count = len(mesh["positions"])
        mesh_indices = triangle_indices(mesh["indices"], count)
        require(all(len(mesh[k]) == count for k in ("normals", "bone_indices", "bone_weights", "colors_bgra"))
                and len(mesh["uv_sets"]) == 1 and len(mesh["uv_sets"][0]) == count, "Incomplete board channels")
        mat = materials[mesh["material_checksum"]]
        require(len(mat["passes"]) == 1 and not mat["double_sided"] and mat["use_alpha_cutoff"],
                "Unsupported board material")
        render_pass = mat["passes"][0]
        require(render_pass["blend_mode"] == render_pass["blend_extra"] == 0
                and render_pass["color"] == [.5, .5, .5, 0.] and render_pass["addressing"] in (0, 65537),
                "Unsupported board material pass")
        address = 3 if render_pass["addressing"] else 1
        meshes.append(MESH.pack(len(vertices)//VERTEX.size, count, len(indices), len(mesh_indices),
                               tex_lookup[render_pass["texture_checksum"]], address, address, mat["alpha_cutoff"]))
        for pos, normal, bones, weights, color, uv in zip(mesh["positions"], mesh["normals"], mesh["bone_indices"],
                mesh["bone_weights"], mesh["colors_bgra"], mesh["uv_sets"][0]):
            require(color == [128]*4 and len(uv) == 2, "Unsupported original color/UV")
            point, direction = skin_vertex(pos, normal, bones, weights, palettes, p["calibration"],
                                           scale, source_origin, target_origin)
            vertices.extend(VERTEX.pack(*pos, *normal, 0x80808080, *uv, *bones, *weights))
            points.append(point)
        indices.extend(mesh_indices)
    require(len(vertices) == 558*VERTEX.size and len(indices) == 1656, "Unexpected original topology")
    pixel_data, texture_headers = bytearray(), []
    for t in model["textures"]:
        width, height, rgba = textures[t["filename"]]
        require((width, height) == (t["width"], t["height"]), "Texture dimensions changed")
        texture_headers.append(TEXTURE.pack(width, height, PIXEL_OFFSET+len(pixel_data), len(rgba)))
        pixel_data.extend(rgba)
    body = b"".join(meshes+texture_headers)+vertices+struct.pack("<1656H", *indices)+pixel_data
    conversion = list(p["calibration"])
    for row in range(3):
        for col in range(3):
            conversion[row*4+col] *= scale
    origin = transform(source_origin, conversion)
    conversion[12:15] = [target_origin[i]-origin[i] for i in range(3)]
    data = HEADER.pack(b"GTABRD02", 2, HEADER.size+len(body), 5, 558, 1656, 5,
                       sha(prepared["bind_bytes"]), rig_hash, bytes.fromhex(MODEL_SHA), *conversion)+body
    digest = validate_bundle(data, prepared["bind_bytes"], rig_hash)
    # Diagnostic only: different human proportions do not guarantee board/foot contact.
    model_by_id = {n["bone_id"]: frame["model_row_major"][n["matrix_index"]] for n in prepared["report"]["nodes"]}
    nearest = {str(b): min(math.dist(model_by_id[b][12:15], v) for v in points) for b in (43, 44, 53, 54)}
    return data, dict(bundle_sha256=digest.hex(), bytes=len(data), vertices=558, triangles=552, textures=5,
        source_palette_sha256=sha(raw).hex(), weighted_source_bones=[48, 49, 50, 51],
        visual_scale=scale, cj_foot_to_nearest_vertex_distance=nearest,
        foot_contact_verified=False, material_fidelity_verified=False, native_execution=False)


def main():
    from preview_pose import prepare
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board-model", required=True, type=Path)
    parser.add_argument("--target-rig", required=True, type=Path)
    parser.add_argument("--pose-report", required=True, type=Path)
    parser.add_argument("--frame", type=int, default=1)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    model_bytes, rig_bytes = args.board_model.read_bytes(), args.target_rig.read_bytes()
    require(sha(model_bytes).hex() == MODEL_SHA, "Unverified original board export")
    p = prepare(rig_bytes, args.pose_report.read_bytes(), args.frame)
    inputs = []
    for item in p["report"]["inputs"]:
        raw = Path(item["path"]).read_bytes()
        require(len(raw) <= 2000000 and sha(raw).hex() == item["sha256"], "Retarget source changed")
        inputs.append(raw)
    records = [json.loads(line) for line in inputs[2].splitlines() if line.strip()]
    require(any(r.get("phase") == "animation_pass" and r.get("host_palette_writes") is False
                for r in records), "Original animation proof missing")
    records = [r for r in records if r.get("phase") == "animation_frame"]
    model, textures = json.loads(model_bytes), {}
    for filename, digest in PNG_HASHES.items():
        path = args.board_model.parent/filename
        require(sha(path.read_bytes()).hex() == digest, "Original PNG changed")
        textures[filename] = read_export_png(path)
    data, report = build(model, textures, json.loads(inputs[0]), p["target"], records[args.frame], p, sha(rig_bytes))
    with args.output.open("xb") as stream:
        stream.write(data)
    print(json.dumps(dict(report, output=str(args.output.resolve()), target_rig_sha256=sha(rig_bytes).hex(),
                          source_model_sha256=MODEL_SHA, selected_frame=args.frame)))


if __name__ == "__main__":
    main()
