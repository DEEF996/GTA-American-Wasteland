"""Retarget captured original THAW palettes to an unchanged retail WaW rig.

Offline only: emits rig/pose data; never accesses a game process. Matrices are
row-major, row-vector affine matrices, translation at indices 12..14.
"""
import argparse
import base64
import hashlib
import json
import math
import struct
from pathlib import Path

IDENTITY = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]
# OAT glTF is (CoD x, CoD z, -CoD y); THAW world uses this same axis order.
WORLD_TO_COD = [1., 0., 0., 0., 0., 0., 1., 0., 0., -1., 0., 0., 0., 0., 0., 1.]
# Model forward is THAW +Z, WaW +X. THAW left +X becomes WaW left +Y.
MODEL_TO_COD = [0., 1., 0., 0., 0., 0., 1., 0., 1., 0., 0., 0., 0., 0., 0., 1.]
BASE_MAP = {
    "tag_origin": 0, "j_mainroot": "Bone_Pelvis", "pelvis": "Bone_Pelvis",
    "j_spinelower": "Bone_Stomach_Lower", "j_spineupper": "Bone_Stomach_Upper",
    "j_spine4": "Bone_chest", "j_neck": "Bone_Neck", "j_head": "Bone_Head",
    "j_head_end": "Bone_Head_Top_Scale",
}
ARM_CHILD = {f"j_{parent}_{side}": f"j_{child}_{side}" for side in ("le", "ri")
             for parent, child in (("clavicle", "shoulder"), ("shoulder", "elbow"), ("elbow", "wrist"))}
for side in ("le", "ri"):
    ARM_CHILD[f"j_wrist_{side}"] = f"j_mid_{side}_1"
    ARM_CHILD[f"j_wristtwist_{side}"] = f"j_wrist_{side}"
for side, thaw in [("le", "L"), ("ri", "R")]:
    for target, source in [("hip", "Thigh"), ("knee", "knee"), ("ankle", "ankle"),
                           ("ball", "toe"), ("clavicle", "Collar"), ("shoulder", "bicep"),
                           ("elbow", "forearm"), ("wrist", "Palm"), ("wristtwist", "wrist")]:
        BASE_MAP[f"j_{target}_{side}"] = f"Bone_{source}_{thaw}"
    for finger, base, tip in [("index", "forefinger_base", "forefinger_tip"),
                               ("mid", "fingers_Base", "Fingers_Tip"),
                               ("ring", "fingers_Base", "Fingers_Tip"),
                               ("pinky", "fingers_Base", "Fingers_Tip"),
                               ("thumb", "thumb", "thumb")]:
        BASE_MAP[f"j_{finger}_{side}_1"] = f"Bone_{base}_{thaw}"
        BASE_MAP[f"j_{finger}_{side}_2"] = f"Bone_{tip}_{thaw}"
        # THAW has fewer finger segments; the final WaW segment keeps its rest offset.


def multiply(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def inverse(a):
    rows = [a[r * 4:r * 4 + 4] + IDENTITY[r * 4:r * 4 + 4] for r in range(4)]
    for c in range(4):
        pivot = max(range(c, 4), key=lambda r: abs(rows[r][c]))
        if abs(rows[pivot][c]) < 1e-10:
            raise ValueError("Singular affine transform")
        rows[c], rows[pivot] = rows[pivot], rows[c]
        divisor = rows[c][c]
        rows[c] = [v / divisor for v in rows[c]]
        for r in range(4):
            if r != c:
                factor = rows[r][c]
                rows[r] = [x - factor * y for x, y in zip(rows[r], rows[c])]
    return [v for row in rows for v in row[4:]]


def transform(point, matrix):
    return [sum(point[k] * matrix[k * 4 + c] for k in range(3)) + matrix[12 + c] for c in range(3)]


def rotation(matrix):
    """Discard affine scale; Gram-Schmidt removes native floating-point drift."""
    def normal(v):
        length = math.sqrt(sum(x * x for x in v))
        if length < 1e-8:
            raise ValueError("Degenerate joint rotation")
        return [x / length for x in v]
    x = normal(matrix[:3])
    projection = sum(x[i] * matrix[4 + i] for i in range(3))
    y = normal([matrix[4 + i] - projection * x[i] for i in range(3)])
    z = [x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0]]
    if sum(z[i] * matrix[8 + i] for i in range(3)) <= 0:
        raise ValueError("Reflected joint transforms are not supported")
    return x + [0.] + y + [0.] + z + [0.] + [0., 0., 0., 1.]


def quaternion_matrix(q, position=(0., 0., 0.)):
    length = math.sqrt(sum(v * v for v in q))
    if length < 1e-8:
        raise ValueError("Zero quaternion")
    x, y, z, w = [v / length for v in q]
    return [1 - 2*y*y - 2*z*z, 2*x*y + 2*z*w, 2*x*z - 2*y*w, 0.,
            2*x*y - 2*z*w, 1 - 2*x*x - 2*z*z, 2*y*z + 2*x*w, 0.,
            2*x*z + 2*y*w, 2*y*z - 2*x*w, 1 - 2*x*x - 2*y*y, 0., *position, 1.]


def align_direction(start, end):
    start = [v/math.sqrt(sum(x*x for x in start)) for v in start]
    end = [v/math.sqrt(sum(x*x for x in end)) for v in end]
    dot = sum(a*b for a,b in zip(start, end))
    if dot < -.999999:
        axis = min(range(3), key=lambda i: abs(start[i]))
        end = [float(i == axis) for i in range(3)]
        w = 0.
    else:
        w = 1+dot
    cross = [start[1]*end[2]-start[2]*end[1], start[2]*end[0]-start[0]*end[2], start[0]*end[1]-start[1]*end[0]]
    return quaternion_matrix([*cross, w])


def quaternion(matrix):
    m = rotation(matrix)
    trace = m[0] + m[5] + m[10]
    if trace > 0:
        s = math.sqrt(trace + 1) * 2
        q = [(m[6] - m[9])/s, (m[8] - m[2])/s, (m[1] - m[4])/s, s/4]
    else:
        axis = max(range(3), key=lambda i: m[5*i])
        j, k = (axis + 1) % 3, (axis + 2) % 3
        s = math.sqrt(max(0., 1 + m[5*axis] - m[5*j] - m[5*k])) * 2
        q = [0., 0., 0., 0.]
        q[axis] = s / 4
        q[j] = (m[4*axis+j] + m[4*j+axis]) / s
        q[k] = (m[4*axis+k] + m[4*k+axis]) / s
        q[3] = (m[4*j+k] - m[4*k+j]) / s
    length = math.sqrt(sum(v*v for v in q))
    return [v / length for v in q]


def validate_matrix(matrix):
    if len(matrix) != 16 or not all(math.isfinite(v) for v in matrix):
        raise ValueError("Invalid finite 4x4 matrix")
    if max(abs(matrix[i]) for i in (3, 7, 11)) > .001 or abs(matrix[15] - 1) > .001:
        raise ValueError("Expected an affine matrix")


def read_gltf_rig(path, thaw_rig):
    path = path.resolve()
    doc = json.loads(path.read_text(encoding="utf-8"))
    if len(doc.get("skins", [])) != 1:
        raise ValueError("Expected one original retail skin")
    nodes = doc["nodes"]
    skin = doc["skins"][0]
    joints = skin["joints"]
    joint_index = {n: i for i, n in enumerate(joints)}
    parents = {}
    for i, node in enumerate(nodes):
        for child in node.get("children", []):
            if child in parents:
                raise ValueError("Multiple glTF parents")
            parents[child] = i
    world = {}
    visiting = set()
    def global_matrix(index):
        if index in world:
            return world[index]
        if index in visiting:
            raise ValueError("Cyclic skeleton")
        visiting.add(index)
        node = nodes[index]
        if "matrix" in node:
            local = node["matrix"]  # Column-major glTF equals row-major transpose.
        else:
            local = quaternion_matrix(node.get("rotation", [0., 0., 0., 1.]), node.get("translation", [0., 0., 0.]))
            for r, scale in enumerate(node.get("scale", [1., 1., 1.])):
                for c in range(3):
                    local[4*r+c] *= scale
        validate_matrix(local)
        world[index] = multiply(local, global_matrix(parents[index])) if index in parents else local
        visiting.remove(index)
        return world[index]
    accessor = doc["accessors"][skin["inverseBindMatrices"]]
    if accessor["componentType"] != 5126 or accessor["type"] != "MAT4" or accessor["count"] != len(joints) or "sparse" in accessor:
        raise ValueError("Unexpected retail inverse-bind accessor")
    view = doc["bufferViews"][accessor["bufferView"]]
    uri = doc["buffers"][view["buffer"]]["uri"]
    if uri.startswith("data:"):
        data = base64.b64decode(uri.split(",", 1)[1], validate=True)
    else:
        external = (path.parent / uri).resolve()
        if not external.is_relative_to(path.parent):
            raise ValueError("External glTF buffer leaves the model directory")
        data = external.read_bytes()
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride", 64)
    source_names = {b["name"]: b["index"] for b in thaw_rig["bones"] if b["name"]}
    bones = []
    worst = 0.
    for index, node_index in enumerate(joints):
        bind_gltf = global_matrix(node_index)
        inverse_gltf = struct.unpack_from("<16f", data, start + index * stride)
        worst = max(worst, max(abs(a-b) for a, b in zip(multiply(inverse_gltf, bind_gltf), IDENTITY)))
        parent_node = parents.get(node_index)
        while parent_node is not None and parent_node not in joint_index:
            parent_node = parents.get(parent_node)
        name = nodes[node_index]["name"]
        source = BASE_MAP.get(name)
        if isinstance(source, str):
            source = source_names[source]
        bind = multiply(bind_gltf, WORLD_TO_COD)
        parent = joint_index.get(parent_node, -1)
        parent_bind = multiply(global_matrix(parent_node), WORLD_TO_COD) if parent >= 0 else IDENTITY
        bones.append({"index": index, "name": name, "parent_index": parent,
                      "source_index": source, "bind_model_row_major": bind,
                      "local_bind_row_major": multiply(bind, inverse(parent_bind))})
    # Retail short-quaternion quantization and OAT node normalization differ by
    # a few thousandths of an inch in the exported inverse-bind translations.
    if worst > .02:
        raise ValueError(f"Retail glTF hierarchy disagrees with inverse binds: {worst}")
    return {"format": "waw-thaw-retarget-rig-v1", "model": path.stem.removesuffix("_lod0"),
            "source_gltf": str(path), "source_gltf_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bone_count": len(bones), "bones": bones,
            "material_names": [m["name"] for m in doc.get("materials", [])],
            "geometry_policy": "Original soldier model, textures and skin weights remain unchanged",
            "coordinates": "CoD model-space, row-major row-vector matrices; translation indices 12..14",
            "validation": {"max_inverse_bind_error": worst, "mapped_bones": sum(b["source_index"] is not None for b in bones)},
            "limitations": "Unmapped helper/final finger bones inherit their original local bind transform; no new animation is synthesized."}


def retarget(frame, target, source_rig):
    skin = frame.get("skin_pose")
    if not skin:
        raise ValueError("Capture lacks full original skin_pose matrices; joint points alone are insufficient")
    palettes = skin["palette_row_major"]
    inverse_binds = skin["inverse_bind_row_major"]
    if len(palettes) != len(source_rig["bones"]) or len(inverse_binds) != len(palettes):
        raise ValueError("Original live bone count differs from the source rig")
    if frame.get("bone_ids") != [b["checksum"] for b in source_rig["bones"]]:
        raise ValueError("Original live bone identities differ from the source rig")
    source_bind, source_pose = [], []
    for bind, palette in zip(inverse_binds, palettes):
        validate_matrix(bind)
        validate_matrix(palette)
        source_bind.append(inverse(bind))
        source_pose.append(multiply(source_bind[-1], palette))
    align_inverse = inverse(MODEL_TO_COD)
    deltas = [multiply(multiply(align_inverse, multiply(inverse(rotation(bind)), rotation(pose))), MODEL_TO_COD)
              for bind, pose in zip(source_bind, source_pose)]
    # Keep native original body tilt/spin while the WaW entity already applies yaw.
    basis = frame.get("basis")
    if not basis or len(basis) != 3 or any(len(row) != 3 for row in basis):
        raise ValueError("Original object basis is required")
    object_matrix = [*basis[0], 0., *basis[1], 0., *basis[2], 0., 0., 0., 0., 1.]
    validate_matrix(object_matrix)
    yaw = math.atan2(-basis[2][2], basis[2][0])
    entity_yaw = quaternion_matrix([0., 0., math.sin(yaw/2), math.cos(yaw/2)])
    residual = multiply(multiply(multiply(align_inverse, rotation(object_matrix)), WORLD_TO_COD), inverse(entity_yaw))
    model_scale = skin["model_scale"]
    if len(model_scale) != 3 or not all(math.isfinite(v) and v > 0 for v in model_scale):
        raise ValueError("Invalid original model scale")
    models = {}
    bones_by_id = {b["index"]: b for b in target["bones"]}
    # A native DObj concatenates models. Head models store binds relative to
    # their shared chest root, whereas the body model stores global binds.
    binds, model_frames, first_names = {}, {}, {}
    duplicate = {}
    for bone in target["bones"]:
        index, parent = bone["index"], bone["parent_index"]
        name = bone["name"].casefold()
        raw = bone["bind_model_row_major"]
        if name in first_names:
            duplicate[index] = first_names[name]
        if parent >= 0:
            model_frames[index] = model_frames[parent]
        elif name in first_names:
            model_frames[index] = multiply(inverse(raw), binds[first_names[name]])
        else:
            model_frames[index] = IDENTITY
        binds[index] = multiply(raw, model_frames[index])
        first_names.setdefault(name, index)
    visiting = set()
    def pose(index):
        if index in models:
            return models[index]
        if index in visiting:
            raise ValueError("Cyclic target rig")
        visiting.add(index)
        bone = bones_by_id[index]
        parent = bone["parent_index"]
        if index in duplicate:
            models[index] = pose(duplicate[index])[:]
            visiting.remove(index)
            return models[index]
        inherited = multiply(multiply(binds[index], inverse(binds[parent])), pose(parent)) if parent >= 0 else binds[index][:]
        source = bone["source_index"]
        if source is not None:
            rest = rotation(binds[index])
            if bone["name"] in ARM_CHILD and ARM_CHILD[bone["name"]] in first_names:
                child = first_names[ARM_CHILD[bone["name"]]]
                source_child = bones_by_id[child]["source_index"]
                native_direction = [binds[child][12+i]-binds[index][12+i] for i in range(3)]
                source_direction = [source_bind[source_child][12+i]-source_bind[source][12+i] for i in range(3)]
                rest = multiply(rest, align_direction(native_direction, transform(source_direction, MODEL_TO_COD)))
            oriented = multiply(rest, deltas[source])
            inherited[:12] = oriented[:12]
            if bone["name"] == "j_mainroot":
                displacement = [(source_pose[source][12+i] - source_bind[source][12+i]) * model_scale[i] for i in range(3)]
                displacement = transform(displacement, MODEL_TO_COD)
                for i in range(3):
                    inherited[12+i] += displacement[i]
        models[index] = inherited
        visiting.remove(index)
        return inherited
    result = []
    for bone in target["bones"]:
        matrix = multiply(pose(bone["index"]), residual)
        result.append({"index": bone["index"], "name": bone["name"],
                       "model_row_major": matrix, "quaternion_xyzw": quaternion(matrix),
                       "translation": matrix[12:15]})
    return {"format": "waw-thaw-retarget-pose-v1", "model": target["model"],
            "t_us": frame.get("t_us"), "entity_yaw_degrees": math.degrees(yaw),
            "coordinates": "CoD entity-local model-space; entity supplies yaw and world origin separately",
            "bones": result}


def self_check(target, source):
    frame = {"basis": [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]],
             "bone_ids": [b["checksum"] for b in source["bones"]],
             "skin_pose": {"palette_row_major": [IDENTITY[:] for _ in source["bones"]],
                           "inverse_bind_row_major": [b["inverse_bind_row_major"] for b in source["bones"]],
                           "model_scale": [1., 1., 1.]}}
    rest = retarget(frame, target, source)
    def arm_branch(index):
        while index >= 0:
            bone = target["bones"][index]
            if bone["name"] in ARM_CHILD:
                return True
            index = bone["parent_index"]
        return False
    error = max(abs(a-b) for output, bone in zip(rest["bones"], target["bones"]) if not arm_branch(bone["index"])
                for a,b in zip(output["model_row_major"], bone["bind_model_row_major"]))
    assert error < .001, error
    for bone in rest["bones"]:
        roundtrip = quaternion_matrix(bone["quaternion_xyzw"], bone["translation"])
        assert max(abs(a-b) for a,b in zip(roundtrip, bone["model_row_major"])) < .001
    # An actual transform input changes only the mapped limb branch. No trick is authored.
    source_index = next(b["index"] for b in source["bones"] if b["name"] == "Bone_forearm_L")
    frame["skin_pose"]["palette_row_major"][source_index] = quaternion_matrix([0., 0., math.sin(.1), math.cos(.1)])
    changed = retarget(frame, target, source)
    by_name = {b["name"]: b for b in changed["bones"]}
    before = {b["name"]: b for b in rest["bones"]}
    assert by_name["j_elbow_le"]["quaternion_xyzw"] != before["j_elbow_le"]["quaternion_xyzw"]
    assert by_name["j_elbow_ri"]["model_row_major"] == before["j_elbow_ri"]["model_row_major"]
    return {"unchanged_torso_rest_error": error, "quaternion_roundtrip": True, "limb_isolation": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True, help="Unmodified OAT soldier LOD0 glTF")
    parser.add_argument("--source-rig", type=Path, default=Path(__file__).resolve().parents[2] / "research/assets/rig_thps7_human.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture", type=Path, help="Optional original-runtime JSONL with skin_pose")
    args = parser.parse_args()
    source = json.loads(args.source_rig.read_text(encoding="utf-8"))
    target = read_gltf_rig(args.model, source)
    target["validation"].update(self_check(target, source))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.capture:
        count = 0
        with args.capture.open(encoding="utf-8") as capture, args.output.open("x", encoding="utf-8") as out:
            for line in capture:
                frame = json.loads(line)
                if "position" not in frame:
                    continue
                out.write(json.dumps(retarget(frame, target, source), separators=(",", ":")) + "\n")
                count += 1
        print(json.dumps({"poses": count, "output": str(args.output)}))
    else:
        with args.output.open("x", encoding="utf-8") as out:
            json.dump(target, out, separators=(",", ":"))
        print(json.dumps({"model": target["model"], "bones": target["bone_count"], **target["validation"]}))
