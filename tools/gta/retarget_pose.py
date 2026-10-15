"""Offline stock THAW -> observed CJ pose audit. No process access or game writes.

Uses actual inverse binds and native palettes, preserving CJ segment offsets.
Output is private inspection data, not a renderer packet or gameplay backend.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assets_skeleton import inverse, multiply
from waw.retarget import IDENTITY, align_direction, rotation, transform, validate_matrix

SKE_SHA = "342629a90da89604285a1c95c3b899581d5b2f12fc9799199b06828c6c1c54c4"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def source_rotation(matrix):
    """Read animated affine source axes without stretching the target skeleton."""
    validate_matrix(matrix)
    require(all(abs(v) < 100000 for v in matrix), "Unbounded transform")
    result = rotation(matrix)
    require(sum(result[8+i]*matrix[8+i] for i in range(3)) > 1e-8,
            "Degenerate source bone transform")
    return result


def rigid(matrix):
    result = source_rotation(matrix)
    # Only remove measured floating-point drift, never silently discard scale/shear.
    require(max(abs(matrix[4*r+c]-result[4*r+c]) for r in range(3) for c in range(3)) < .001,
            "Scaled, sheared or reflected bone transform")
    return result


def parents_from_nodes(nodes):
    """HAnim pushes the previous parent, then pops after the current node.

    Primary traversal: github.com/aap/librw/blob/master/src/hanim.cpp.
    IDs and matrix indices are distinct; neither determines the parent.
    """
    require(0 < len(nodes) <= 128, "Unsupported HAnim node count")
    parents, order, ids, stack, parent = {}, [], set(), [-1], -1
    for ordinal, node in enumerate(nodes):
        index, bone_id, flags = node["matrix_index"], node["bone_id"], node["parent_flags"]
        require(node["ordinal"] == ordinal and type(index) is int and 0 <= index < len(nodes)
                and index not in parents and type(bone_id) is int and bone_id not in ids
                and type(flags) is int and 0 <= flags <= 3, "Invalid HAnim node identity/flags")
        require(parent == -1 or parent in parents, "Forward or cyclic HAnim parent")
        parents[index] = parent
        order.append(index)
        ids.add(bone_id)
        if flags & 2:
            stack.append(parent)
        if flags & 1:
            require(bool(stack), "HAnim parent stack underflow")
            parent = stack.pop()
        else:
            parent = index
    require(not stack and list(parents.values()).count(-1) == 1,
            "Unbalanced or multiple-root HAnim hierarchy")
    return [parents[i] for i in range(len(nodes))], order


def anatomical_basis(pelvis, head, left_hip, right_hip):
    def normalized(values):
        length = math.sqrt(sum(x*x for x in values))
        require(length > 1e-6, "Degenerate anatomical calibration anchors")
        return [x/length for x in values]
    lateral = normalized([right_hip[i]-left_hip[i] for i in range(3)])
    up = [head[i]-pelvis[i] for i in range(3)]
    projection = sum(up[i]*lateral[i] for i in range(3))
    up = normalized([up[i]-projection*lateral[i] for i in range(3)])
    forward = [lateral[1]*up[2]-lateral[2]*up[1], lateral[2]*up[0]-lateral[0]*up[2],
               lateral[0]*up[1]-lateral[1]*up[0]]
    return rigid(lateral+[0.]+up+[0.]+forward+[0.]+[0., 0., 0., 1.])


def retarget(source_bind, target_bind, parents, order, mapping, calibration, palettes,
             native_local_rotations=None, rest_corrections=None,
             collapsed_pairs=(), translation_transfer=None):
    return Retargeter(source_bind, target_bind, parents, order, mapping, calibration,
        native_local_rotations, rest_corrections, collapsed_pairs, translation_transfer).evaluate(palettes)


class Retargeter:
    """Own a validated rig snapshot; only the evaluated palette changes per frame."""
    def __init__(self, source_bind, target_bind, parents, order, mapping, calibration,
                 native_local_rotations=None, rest_corrections=None,
                 collapsed_pairs=(), translation_transfer=None):
        self.source_bind = tuple(tuple(matrix) for matrix in source_bind)
        self.target_bind = tuple(tuple(matrix) for matrix in target_bind)
        self.parents, self.order, self.mapping = tuple(parents), tuple(order), dict(mapping)
        self.calibration = tuple(calibration)
        self.translation_transfer = tuple(translation_transfer) if translation_transfer is not None else None
        require(len(target_bind) == len(parents), "Pose/bind count mismatch")
        require(sorted(order) == list(range(len(target_bind))), "Incomplete target traversal")
        rigid(calibration)
        self.calibration_inverse = inverse(list(self.calibration))
        self.source_inverse = [inverse(rigid(bind)) for bind in self.source_bind]
        self.local, self.rest, self.unmapped, self.collapsed = {}, {}, {}, {}
        seen = set()
        for index in self.order:
            parent, bind = self.parents[index], self.target_bind[index]
            require(parent == -1 or 0 <= parent < len(target_bind) and parent in seen,
                    "Target parent is not ready")
            seen.add(index)
            rigid(bind)
            local = multiply(bind, inverse(list(self.target_bind[parent]))) if parent >= 0 else bind
            self.local[index] = local
            if index in self.mapping:
                source_index = self.mapping[index]
                require(type(source_index) is int and 0 <= source_index < len(source_bind),
                        "Invalid source mapping")
                rest = rigid(bind)
                if rest_corrections is not None and index in rest_corrections:
                    rest = multiply(rest, rigid(rest_corrections[index]))
                self.rest[index] = rest
                children = []
                for start, child in collapsed_pairs:
                    if start == index:
                        require(self.parents[child] == index and child in self.mapping,
                                "Invalid collapsed target segment")
                        children.append((self.mapping[child], multiply(self.target_bind[child], inverse(list(bind)))))
                self.collapsed[index] = children
            else:
                current = native_local_rotations[index] if native_local_rotations is not None else rigid(local)
                self.unmapped[index] = rigid(current) if parent >= 0 else list(current)
            if self.translation_transfer is not None and index == self.translation_transfer[0]:
                _, source_index, scale = self.translation_transfer
                require(type(source_index) is int and 0 <= source_index < len(source_bind)
                        and math.isfinite(scale) and scale > 0, "Invalid visual translation transfer")
        self.mapped_sources = frozenset(self.mapping[index] for index in self.rest)

    def evaluate(self, palettes):
        require(len(self.source_bind) == len(palettes), "Pose/bind count mismatch")
        source_deltas, source_models = {}, []
        for index, (bind, bind_inverse, palette) in enumerate(zip(self.source_bind, self.source_inverse, palettes)):
            validate_matrix(palette)
            model = multiply(bind, palette)
            source_models.append(model)
            # Unmapped bones still cross every source guard, but need no CJ rotation.
            model_rotation = source_rotation(model)
            if index in self.mapped_sources:
                delta = multiply(bind_inverse, model_rotation)
                source_deltas[index] = multiply(multiply(self.calibration_inverse, delta), self.calibration)
        output = [None] * len(self.target_bind)
        for index in self.order:
            parent, bind, local = self.parents[index], self.target_bind[index], self.local[index]
            if index in self.mapping:
                source_index = self.mapping[index]
                current = multiply(self.rest[index], source_deltas[source_index])
                # Preserve the original collapsed-chain swing after its mapped twist.
                for source_child, child_local in self.collapsed[index]:
                    desired = transform([source_models[source_child][12+i]-source_models[source_index][12+i]
                                         for i in range(3)], self.calibration)
                    predicted = transform(child_local[12:15], current)
                    current = multiply(current, align_direction(predicted, desired))
            else:
                current = self.unmapped[index]
                current = multiply(current, rigid(output[parent])) if parent >= 0 else list(current)
            position = transform(local[12:15], output[parent]) if parent >= 0 else bind[12:15]
            if self.translation_transfer is not None and index == self.translation_transfer[0]:
                _, source_index, scale = self.translation_transfer
                displacement = transform([source_models[source_index][12+i]-self.source_bind[source_index][12+i]
                                          for i in range(3)], self.calibration)
                position = [position[i]+displacement[i]*scale for i in range(3)]
            current = list(current)
            current[12:15] = position
            rigid(current)
            output[index] = current
        return output


def decode_bits(bits, count):
    require(len(bits) == count and all(type(x) is int and 0 <= x <= 0xffffffff for x in bits),
            "Invalid native float-bit buffer")
    raw = struct.pack(f"<{count}I", *bits)
    values = list(struct.unpack(f"<{count}f", raw))
    require(all(math.isfinite(x) for x in values), "Nonfinite native source buffer")
    return raw, values


def matrix_error(a, b):
    return max(abs(x-y) for x, y in zip(a, b))


def segment_error(models, binds, parents, excluded=()):
    def length(a, b):
        return math.dist(a[12:15], b[12:15])
    return max((abs(length(models[i], models[p])-length(binds[i], binds[p]))
                for i, p in enumerate(parents) if p >= 0 and i not in excluded), default=0.)


def direction_error(a, b):
    lengths = math.sqrt(sum(x*x for x in a))*math.sqrt(sum(x*x for x in b))
    require(lengths > 1e-10, "Degenerate anatomical segment")
    cosine = max(-1., min(1., sum(x*y for x, y in zip(a, b))/lengths))
    return math.degrees(math.acos(cosine))


def limb_corrections(source_bind, target_bind, mapping, calibration, pairs, terminal_pairs=()):
    corrections = {}
    for parent, child in pairs:
        source_parent, source_child = mapping[parent], mapping[child]
        source_direction = [source_bind[source_child][12+i]-source_bind[source_parent][12+i] for i in range(3)]
        target_direction = [target_bind[child][12+i]-target_bind[parent][12+i] for i in range(3)]
        direction_error(source_direction, target_direction)  # Validate nonzero segments.
        corrections[parent] = align_direction(target_direction, transform(source_direction, calibration))
    # A terminal joint must inherit its parent's rest correction; otherwise
    # the calibration alone introduces a bend at the wrist, head or fingertip.
    for parent, child in terminal_pairs:
        require(parent in corrections and child not in corrections and child in mapping,
                "Terminal calibration must inherit one mapped limb correction")
        corrections[child] = list(corrections[parent])
    return corrections


def limb_errors(source_models, target_models, mapping, calibration, pairs):
    errors = []
    for parent, child in pairs:
        a, b = mapping[parent], mapping[child]
        source = [source_models[b][12+i]-source_models[a][12+i] for i in range(3)]
        target = [target_models[child][12+i]-target_models[parent][12+i] for i in range(3)]
        errors.append(direction_error(transform(source, calibration), target))
    return errors


def prepare(source, target):
    require(source["format"] == "thaw-ske-v1" and source["bone_count"] == 52
            and source["source"]["ske_sha256"] == SKE_SHA, "Unverified stock source rig")
    require(target["accepted"] is True and target["read_only"] is True,
            "Target rig is not an accepted read-only observation")
    rig = target["rig"]
    require(len(rig["hierarchies"]) == len(rig["skins"]) == len(rig["atomics"]) == 1,
            "First proof requires one compatible observed skin/hierarchy")
    hierarchy, skin, atomic = rig["hierarchies"][0], rig["skins"][0], rig["atomics"][0]
    require(hierarchy["matrix_space"] == "world" and hierarchy["count"] == skin["count"] == 32
            and atomic["hierarchy_address"] == hierarchy["address"]
            and atomic["skin_address"] == skin["address"], "Unsupported or mismatched target binding")
    nodes = hierarchy["nodes"]
    require(len(nodes) == 32 and len(skin["inverse_bind_invariants"]) == 32,
            "Incomplete target binding")
    parents, order = parents_from_nodes(nodes)
    by_id = {n["bone_id"]: n["matrix_index"] for n in nodes}
    source_bind = []
    for index, bone in enumerate(source["bones"]):
        require(bone["index"] == index, "Source indices changed")
        bind = inverse(bone["inverse_bind_row_major"])
        require(matrix_error(bind, bone["bind_model_row_major"]) < 1e-6, "Source bind disagrees with inverse bind")
        source_bind.append(bind)
    require(len(source_bind) == 52, "Incomplete source bones")
    target_bind = [inverse(m["affine_row_major"]) for m in skin["inverse_bind_invariants"]]
    for matrix in source_bind + target_bind:
        rigid(matrix)
    source_basis = anatomical_basis(*(source_bind[i][12:15] for i in (1, 30, 43, 39)))
    target_basis = anatomical_basis(*(target_bind[by_id[i]][12:15] for i in (1, 5, 41, 51)))
    calibration = multiply(inverse(source_basis), target_basis)
    mapping = {n["matrix_index"]: n["candidate_thaw_index"] for n in nodes
               if n["candidate_thaw_index"] is not None and not n["provisional_mapping"]}
    # The original hierarchy explicitly contains lower -> upper -> chest.
    # Test its two-joint reduction before accepting the stock correspondence.
    require(source["bones"][3]["parent_index"] == 2 and source["bones"][4]["parent_index"] == 3,
            "Original collapsed torso chain differs")
    mapping.update({by_id[2]: 2, by_id[3]: 4,
                    by_id[35]: 9, by_id[36]: 10, by_id[25]: 23, by_id[26]: 24})
    hand_pairs = [(by_id[a], by_id[b]) for a, b in
                  ((34, 35), (35, 36), (24, 25), (25, 26))]
    require(all(parents[b] == a and source["bones"][mapping[b]]["parent_index"] == mapping[a]
                for a, b in hand_pairs), "Finger source or target hierarchy differs")
    pairs = [(by_id[a], by_id[b]) for a, b in
             ((21, 22), (22, 23), (23, 24), (31, 32), (32, 33), (33, 34),
              (51, 52), (52, 53), (53, 54), (41, 42), (42, 43), (43, 44),
              (2, 3), (3, 4), (4, 5))] + hand_pairs
    require(all(parents[b] == a and a in mapping and b in mapping for a, b in pairs),
            "Anatomical limb pair is not a directly mapped target segment")
    terminal_pairs = [(by_id[4], by_id[5]), (by_id[35], by_id[36]), (by_id[25], by_id[26])]
    require(all(parents[b] == a and source["bones"][mapping[b]]["parent_index"] == mapping[a]
                for a, b in terminal_pairs), "Terminal source or target hierarchy differs")
    corrections = limb_corrections(source_bind, target_bind, mapping, calibration, pairs, terminal_pairs)
    collapsed = [(by_id[2], by_id[3])]
    observed = [None]*32
    for node in nodes:
        observed[node["matrix_index"]] = node["observed_transform"]["affine_row_major"]
    for matrix in observed:
        rigid(matrix)
    native_local = [rigid(multiply(matrix, inverse(observed[parents[i]]))) if parents[i] >= 0
                    else rigid(matrix) for i, matrix in enumerate(observed)]
    # THAW has no compatible CJ facial channels. Keep a neutral face relative
    # to the animated head instead of freezing the expression seen at startup.
    for bone_id in (6, 7, 8):
        index = by_id[bone_id]
        require(parents[index] == by_id[5], "Facial helper is not attached to the head")
        native_local[index] = rigid(multiply(target_bind[index], inverse(target_bind[parents[index]])))
    root = next(i for i, parent in enumerate(parents) if parent == -1)
    anchor = multiply(inverse(target_bind[root]), observed[root])
    # A visual proportion measurement, not a gameplay/metre conversion constant.
    ratios = [math.dist(target_bind[by_id[a]][12:15], target_bind[by_id[b]][12:15]) /
              math.dist(source_bind[c][12:15], source_bind[d][12:15])
              for a, b, c, d in ((41, 42, 43, 44), (42, 43, 44, 45),
                                (51, 52, 39, 40), (52, 53, 40, 41))]
    return dict(source_bind=source_bind, target_bind=target_bind, parents=parents, order=order,
                mapping=mapping, calibration=calibration, native_local_rotations=native_local,
                rest_corrections=corrections, anatomical_pairs=pairs,
                terminal_rest_pairs=terminal_pairs,
                collapsed_pairs=collapsed, translation_transfer=(by_id[1], 1, statistics.median(ratios)),
                world_anchor=anchor, nodes=nodes, visual_segment_ratios=ratios,
                root_index=root)


def animation_timeline(records):
    passed = [r for r in records if r.get("phase") == "animation_pass"]
    require(len(passed) == 1 and passed[0].get("host_palette_writes") is False
            and passed[0].get("deterministic_repeat") is True, "Native animation proof did not pass")
    proof = passed[0]
    frames = [r for r in records if r.get("phase") == "animation_frame"]
    require(4 <= len(frames) <= 123 and all(math.isfinite(f["time"]) for f in frames), "Unbounded native samples")
    rate = proof.get("sample_hz")
    if rate is None:
        require(len(frames) == 4 and frames[0]["time"] == 0 and frames[1]["time"] == frames[3]["time"] == .3,
                "Expected complete four-frame native proof")
        count, duration, repeat = 3, frames[2]["time"], (1, 3)
    else:
        duration, count = proof.get("duration", 0), proof.get("playback_frames", 0)
        require(rate == 120 and math.isfinite(duration) and .3 < duration < 1, "Unsupported native sample rate/duration")
        expected = [i/rate for i in range(math.ceil(duration*rate))]+[duration]
        require(count == len(expected) and proof.get("native_frames") == count+2 == len(frames)
                and proof.get("unique_sample_times") == expected
                and [f["time"] for f in frames] == expected+[.3, .3], "Native sample grid is incomplete or reordered")
        repeat = (count, count+1)
    require(.3 < duration < 1, "Unexpected bounded original clip duration")
    return frames, dict(sample_hz=rate, duration=duration, playback_frames=count), repeat


def build_report(source, target, records):
    frames, timeline, repeat = animation_timeline(records)
    p = prepare(source, target)
    arguments = {k: p[k] for k in ("source_bind", "target_bind", "parents", "order", "mapping", "calibration")}
    # Explicit synthetic identity palette checks the binding equations, not a game pose.
    rest = retarget(**arguments, palettes=[IDENTITY]*52)
    rest_error = max(matrix_error(a, b) for a, b in zip(rest, p["target_bind"]))
    require(rest_error < 1e-5, "Calibration does not preserve the target bind pose")
    calibrated_rest = retarget(**arguments, palettes=[IDENTITY]*52, rest_corrections=p["rest_corrections"],
                               collapsed_pairs=p["collapsed_pairs"], translation_transfer=p["translation_transfer"])
    calibrated_errors = limb_errors(p["source_bind"], calibrated_rest, p["mapping"],
                                   p["calibration"], p["anatomical_pairs"])
    require(max(calibrated_errors) < .001, "Anatomical rest directions are not aligned")
    terminal_rest_error = max(matrix_error(
        rigid(multiply(calibrated_rest[b], inverse(calibrated_rest[a]))),
        rigid(multiply(p["target_bind"][b], inverse(p["target_bind"][a]))))
        for a, b in p["terminal_rest_pairs"])
    require(terminal_rest_error < 1e-6, "Rest calibration introduced an artificial terminal-joint bend")
    results = []
    for frame in frames:
        raw, values = decode_bits(frame["palette_bits"], 52*16)
        qraw, _ = decode_bits(frame["quaternion_bits"], 52*4)
        traw, _ = decode_bits(frame["translation_bits"], 52*4)
        require(hashlib.sha256(raw).hexdigest() == frame["palette_sha256"]
                and hashlib.sha256(qraw+traw).hexdigest() == frame["channel_sha256"]
                and frame["active_bones"] == 52, "Native frame hash/active count mismatch")
        palettes = [values[i:i+16] for i in range(0, len(values), 16)]
        models = retarget(**arguments, palettes=palettes,
                          native_local_rotations=p["native_local_rotations"],
                          rest_corrections=p["rest_corrections"], collapsed_pairs=p["collapsed_pairs"],
                          translation_transfer=p["translation_transfer"])
        pelvis = p["translation_transfer"][0]
        error = segment_error(models, p["target_bind"], p["parents"], excluded=(pelvis,))
        require(error < 1e-5, "Retarget changed target segment lengths")
        source_models = [multiply(bind, palette) for bind, palette in zip(p["source_bind"], palettes)]
        angle_errors = limb_errors(source_models, models, p["mapping"], p["calibration"], p["anatomical_pairs"])
        require(max(angle_errors) < .01, "Observed anatomical direction transfer differs")
        root = p["root_index"]
        # Anchor the position, not all 16 elements: the genuine clip retains a
        # small nonidentity root rotation, which must not be clamped away.
        root_position_error = math.dist(models[root][12:15], p["target_bind"][root][12:15])
        require(root_position_error < 1e-8, "Retarget moved the anchored root position")
        source_displacement = [source_models[1][12+i]-p["source_bind"][1][12+i] for i in range(3)]
        visual_displacement = [x*p["translation_transfer"][2] for x in transform(source_displacement, p["calibration"])]
        world = [multiply(matrix, p["world_anchor"]) for matrix in models]
        for matrix in world:
            rigid(matrix)
        output = struct.pack("<512d", *(v for matrix in models for v in matrix))
        results.append(dict(time=frame["time"], source_palette_sha256=frame["palette_sha256"],
                            target_model_sha256=hashlib.sha256(output).hexdigest(),
                            limb_direction_errors_degrees=angle_errors[:12],
                            max_limb_direction_error_degrees=max(angle_errors[:12]),
                            torso_direction_errors_degrees=angle_errors[12:15],
                            max_torso_direction_error_degrees=max(angle_errors[12:15]),
                            hand_direction_errors_degrees=angle_errors[15:],
                            max_hand_direction_error_degrees=max(angle_errors[15:]),
                            source_pelvis_displacement=source_displacement,
                            target_visual_pelvis_displacement=visual_displacement,
                            root_position_error=root_position_error,
                            root_model_row_major=models[root],
                            max_segment_length_error=error, model_row_major=models, world_row_major=world))
    require(results[0]["target_model_sha256"] != results[repeat[0]]["target_model_sha256"]
            and results[repeat[0]]["target_model_sha256"] == results[repeat[1]]["target_model_sha256"],
            "Output is unchanged or repeated native frame is nondeterministic")
    return dict(format="gta-thaw-offline-retarget-v1", offline_checks_passed=True,
                game_memory_written=False, visual_fidelity_verified=False,
                source_frames=len(frames), source_bones=52, target_bones=32, **timeline,
                rest_calibration_max_error=rest_error, calibration_row_major=p["calibration"],
                anatomical_rest_max_direction_error_degrees=max(calibrated_errors),
                terminal_rest_local_rotation_max_error=terminal_rest_error,
                terminal_rest_inheritance=p["terminal_rest_pairs"],
                anatomical_pairs=p["anatomical_pairs"],
                anatomical_rest_corrections={str(i): matrix for i, matrix in p["rest_corrections"].items()},
                rest_policy="Target segment lengths retained; 12 limb, 3 torso and 4 hand segments anatomically aligned; root-pelvis is a visual translation channel",
                world_anchor_row_major=p["world_anchor"],
                visual_segment_scale_median=statistics.median(p["visual_segment_ratios"]),
                visual_segment_ratios=p["visual_segment_ratios"], scale_applied=True,
                scale_scope="Visual pelvis displacement only; not world/gameplay units",
                length_check_excluded_motion_joint_ids=[1],
                collapsed_source_chains=[dict(target_bone_ids=[2, 3], source_indices=[2, 3, 4])],
                nodes=[dict(bone_id=n["bone_id"], matrix_index=n["matrix_index"],
                            parent_index=p["parents"][n["matrix_index"]],
                            source_index=p["mapping"].get(n["matrix_index"]),
                            provisional_mapping_disabled=n["provisional_mapping"] and n["matrix_index"] not in p["mapping"],
                            provisional_mapping_evaluated=n["provisional_mapping"] and n["matrix_index"] in p["mapping"])
                       for n in p["nodes"]],
                unmapped_policy="Neutral target facial helpers; other unmapped helpers retain observed native parent-relative rotation; original target bind offsets",
                root_policy="Observed native root position anchored; source root rotation retained; pelvis displacement transferred using measured visual proportions",
                limitations=["Bone direction tests do not prove twist or skinned-mesh fidelity; CJ's two finger joints cannot reproduce THAW's separate thumb and index channels.",
                             "No gameplay movement or visual object flip/roll; not complete THAW presentation.",
                             "World output is numeric affine data, not raw RenderWare matrices or a live renderer packet."],
                frames=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-rig", required=True, type=Path)
    parser.add_argument("--target-rig", required=True, type=Path)
    parser.add_argument("--animation-log", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    paths = [args.source_rig, args.target_rig, args.animation_log]
    inputs = [path.read_bytes() for path in paths]
    require(all(len(data) <= 2000000 for data in inputs), "Oversized offline proof input")
    source, target = (json.loads(data) for data in inputs[:2])
    records = [json.loads(line) for line in inputs[2].splitlines() if line.strip()]
    report = build_report(source, target, records)
    report["inputs"] = [dict(path=str(path.resolve()), sha256=hashlib.sha256(data).hexdigest())
                        for path, data in zip(paths, inputs)]
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({k: v for k, v in report.items() if k not in ("frames", "nodes", "world_anchor_row_major")}))


if __name__ == "__main__":
    main()
