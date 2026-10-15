"""Bounded, read-only CJ HAnim/skin inspection for the verified GTA V2 session.

No remote calls, suspension, injection, controls, geometry dump or memory writes.
Layout provenance and unresolved presentation work: docs/gta-pose-adapter.md.
"""
from __future__ import annotations

import argparse
from collections import Counter
import ctypes as C
from ctypes import wintypes as W
import json
import math
from pathlib import Path
import struct
import time

from native.inspect_state import Observer
from probe_world import Rejected, snapshot_stamp, validate_snapshot


MAX_BONES, MAX_ATOMICS = 128, 64
MAX_READ, MAX_TOTAL = 65536, 262144
FRAME_COUNTER = 0xB7CB4C
# Exact original getters/operands, verified against the Observer-pinned EXE.
GUARDS = {
    0x56E210: "8b44240485c07d070fb60574cdb70069c0900100008b8098cdb700c3",
    0x749B78: "8d78088b4008",
    0x749B8A: "8b3083c0c0",
    0x7C7540: "8b4424048b0da478c9008b0401c3",
    0x7C7550: "8b4424048b0da878c9008b0401c3",
    0x7C77E0: "8b4424048b00c3",
    0x7C7810: "8b4424048b400cc3",
    0x7C5120: "8b4424048b4008c3",
}
# GTA node IDs -> existing THAW source indices. These are anatomical candidates.
THAW_MAP = {0: 0, 1: 1, 2: 2, 3: 4, 4: 29, 5: 30,
            21: 17, 22: 18, 23: 19, 24: 20, 25: 23, 26: 24,
            31: 5, 32: 6, 33: 7, 34: 8, 35: 9, 36: 10,
            51: 39, 52: 40, 53: 41, 54: 42,
            41: 43, 42: 44, 43: 45, 44: 46}
PROVISIONAL_IDS = {2, 3, 25, 26, 35, 36}


def require(condition, reason):
    if not condition:
        raise Rejected(reason)


def address_range(address, size):
    require(type(address) is int and type(size) is int and 0 < size <= MAX_READ
            and 0x10000 <= address and address + size <= 0x80000000,
            "invalid_or_unsupported_read_range")


def u32(data, offset=0):
    return struct.unpack_from("<I", data, offset)[0]


class Memory:
    def __init__(self, observer):
        self.api, self.handle, self.total = observer.api, None, 0
        self.api.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p,
                                              C.c_size_t, C.POINTER(C.c_size_t)]
        self.api.ReadProcessMemory.restype = W.BOOL
        self.handle = self.api.OpenProcess(0x1000 | 0x10, False, observer.pid)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        try:
            times = [W.FILETIME() for _ in range(4)]
            if not self.api.GetProcessTimes(self.handle, *(C.byref(item) for item in times)):
                raise C.WinError(C.get_last_error())
            creation = times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
            require(creation == observer.created, "read_handle_process_lifetime_changed")
        except BaseException:
            self.close()
            raise

    def read(self, address, size):
        address_range(address, size)
        self.total += size
        require(self.total <= MAX_TOTAL, "read_budget_exceeded")
        data, got = C.create_string_buffer(size), C.c_size_t()
        if not self.api.ReadProcessMemory(self.handle, address, data, size, C.byref(got)):
            raise C.WinError(C.get_last_error())
        require(got.value == size, "partial_memory_read")
        return data.raw

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class Capture:
    def __init__(self, memory):
        self.memory, self.blocks = memory, {}

    def read(self, address, size):
        data = self.memory.read(address, size)
        key = address, size
        require(key not in self.blocks or self.blocks[key] == data, "rig_changed_during_read")
        self.blocks[key] = data
        return data

    def word(self, address):
        return u32(self.read(address, 4))

    def revalidate(self):
        for (address, size), expected in self.blocks.items():
            require(self.memory.read(address, size) == expected, "rig_changed_during_read")


def decode_nodes(data, count):
    require(type(count) is int and 0 < count <= MAX_BONES and len(data) == count * 16,
            "invalid_hanim_node_count_or_size")
    result, ids, indices = [], set(), set()
    for ordinal, (node_id, index, flags, frame) in enumerate(struct.iter_unpack("<iiiI", data)):
        require(0 <= node_id <= 65535 and node_id not in ids, "duplicate_or_invalid_bone_id")
        require(0 <= index < count and index not in indices, "duplicate_or_invalid_bone_index")
        require(0 <= flags <= 3, "unsupported_parent_flags")
        if frame:
            address_range(frame, 0xA4)
        ids.add(node_id)
        indices.add(index)
        result.append(dict(ordinal=ordinal, bone_id=node_id, matrix_index=index,
                           parent_flags=flags, push_parent=bool(flags & 2),
                           pop_parent=bool(flags & 1), frame_address=frame,
                           candidate_thaw_index=THAW_MAP.get(node_id),
                           provisional_mapping=node_id in PROVISIONAL_IDS))
    return result


def matrix_summary(data):
    require(len(data) == 64, "invalid_matrix_size")
    rows = [struct.unpack_from("<3f", data, at) for at in (0, 16, 32, 48)]
    require(all(math.isfinite(v) and abs(v) <= 100000 for row in rows for v in row),
            "nonfinite_or_unbounded_matrix")
    a, b, c, position = rows
    lengths = [math.sqrt(sum(v*v for v in row)) for row in rows[:3]]
    determinant = (a[0]*(b[1]*c[2]-b[2]*c[1]) - a[1]*(b[0]*c[2]-b[2]*c[0])
                   + a[2]*(b[0]*c[1]-b[1]*c[0]))
    require(all(1e-6 < v < 10000 for v in lengths) and abs(determinant) > 1e-8,
            "singular_or_degenerate_matrix")
    error = max(abs(sum(rows[i][k]*rows[j][k] for k in range(3)) / (lengths[i]*lengths[j]))
                for i, j in ((0, 1), (0, 2), (1, 2)))
    # Keep the twelve observed floats; RW flag/padding words are not floats.
    affine = [value for index, row in enumerate(rows)
              for value in (*row, float(index == 3))]
    return dict(position=list(position), affine_row_major=affine, axis_lengths=lengths,
                determinant=determinant, orthogonality_error=error, flags=u32(data, 12))


def read_rig(capture, ped):
    focus = capture.read(0xB7CD74, 1)[0]
    require(focus <= 1 and capture.word(0xB7CD98 + focus * 0x190) == ped,
            "native_player_identity_changed")
    clump = capture.word(ped + 0x18)
    header = capture.read(clump, 16)
    require(header[0] == 2, "player_rw_object_is_not_a_clump")
    frame = u32(header, 4)
    frame_data = capture.read(frame, 0x90)
    require(frame_data[0] == 0, "unsupported_clump_frame_type")
    atomic_plugin, geometry_plugin = struct.unpack("<ii", capture.read(0xC978A4, 8))
    require(0x70 <= atomic_plugin <= 4096 and atomic_plugin % 4 == 0
            and 0x60 <= geometry_plugin <= 4096 and geometry_plugin % 4 == 0,
            "unsupported_skin_plugin_offsets")
    head, link, previous = clump + 8, u32(header, 8), clump + 8
    atomics, hierarchies, skins, visited = [], {}, {}, set()
    while link != head:
        require(link not in visited and len(visited) < MAX_ATOMICS, "cyclic_or_excessive_atomic_list")
        visited.add(link)
        atomic = link - 0x40
        data = capture.read(atomic, 0x48)
        require(data[0] == 1 and u32(data, 0x3C) == clump and u32(data, 0x44) == previous,
                "invalid_atomic_type_owner_or_links")
        geometry = u32(data, 0x18)
        geo = capture.read(geometry, 32)
        require(geo[0] == 8, "unsupported_geometry_type")
        triangles, vertices, morphs, uv_sets = struct.unpack_from("<4i", geo, 16)
        require(0 < triangles <= 200000 and 0 < vertices <= 100000 and 0 < morphs <= 16
                and 0 <= uv_sets <= 8, "invalid_geometry_counts")
        hierarchy, skin = capture.word(atomic + atomic_plugin), capture.word(geometry + geometry_plugin)
        require(bool(hierarchy) == bool(skin), "partial_atomic_skin_binding")
        if hierarchy and hierarchy not in hierarchies:
            raw = capture.read(hierarchy, 36)
            flags, count, matrices, unaligned, nodes, parent_frame, parent_hierarchy, root_offset, anim = struct.unpack("<9I", raw)
            require(flags & ~0x7003 == 0 and not flags & 3, "unsupported_hierarchy_mode")
            require(0 < count <= MAX_BONES and matrices % 16 == 0, "invalid_hierarchy_count_or_alignment")
            hierarchies[hierarchy] = dict(flags=flags, count=count, matrix_address=matrices,
                node_address=nodes, parent_frame=parent_frame, parent_hierarchy=parent_hierarchy,
                root_parent_offset=root_offset, node_bytes=capture.read(nodes, count*16),
                matrix_bytes=capture.read(matrices, count*64))
        if skin and skin not in skins:
            raw = capture.read(skin, 16)
            count, inverse = u32(raw), u32(raw, 12)
            require(0 < count <= MAX_BONES and inverse % 16 == 0, "invalid_skin_count_or_alignment")
            skins[skin] = dict(count=count, inverse_address=inverse,
                               inverse_bytes=capture.read(inverse, count*64))
        if skin:
            require(skins[skin]["count"] == hierarchies[hierarchy]["count"], "skin_hierarchy_count_mismatch")
        atomics.append(dict(address=atomic, geometry_address=geometry, hierarchy_address=hierarchy,
                            skin_address=skin, vertices=vertices, triangles=triangles,
                            morph_targets=morphs, uv_sets=uv_sets))
        previous, link = link, u32(data, 0x40)
    require(previous == u32(header, 12) and atomics and hierarchies, "empty_or_broken_skinned_clump")
    return dict(clump_address=clump, frame_address=frame, frame_bytes=frame_data,
                plugin_offsets=dict(atomic_hierarchy=atomic_plugin, geometry_skin=geometry_plugin),
                atomics=atomics, hierarchies=hierarchies, skins=skins)


def describe_rig(raw):
    hierarchies = []
    for address, hierarchy in raw["hierarchies"].items():
        nodes = decode_nodes(hierarchy["node_bytes"], hierarchy["count"])
        for node in nodes:
            start = node["matrix_index"] * 64
            node["observed_transform"] = matrix_summary(hierarchy["matrix_bytes"][start:start+64])
        ids = {node["bone_id"] for node in nodes}
        hierarchies.append(dict(address=address, flags=hierarchy["flags"], count=hierarchy["count"],
            matrix_space="root_relative" if hierarchy["flags"] & 0x4000 else "world",
            update_modelling=bool(hierarchy["flags"] & 0x1000), update_ltm=bool(hierarchy["flags"] & 0x2000),
            matrix_address=hierarchy["matrix_address"], node_address=hierarchy["node_address"],
            parent_frame=hierarchy["parent_frame"], parent_hierarchy=hierarchy["parent_hierarchy"],
            root_parent_offset=hierarchy["root_parent_offset"], nodes=nodes,
            missing_candidate_bone_ids=sorted(THAW_MAP.keys()-ids),
            unmapped_bone_ids=sorted(ids-THAW_MAP.keys()),
            provisional_bone_ids=sorted(ids & PROVISIONAL_IDS)))
    skins = []
    for address, skin in raw["skins"].items():
        matrices = [matrix_summary(skin["inverse_bytes"][i*64:(i+1)*64]) for i in range(skin["count"])]
        skins.append(dict(address=address, count=skin["count"], inverse_bind_address=skin["inverse_address"],
                          inverse_bind_invariants=matrices, finite_and_invertible=True))
    return dict(clump_address=raw["clump_address"], frame_address=raw["frame_address"],
                frame_modelling=matrix_summary(raw["frame_bytes"][16:80]),
                frame_ltm=matrix_summary(raw["frame_bytes"][80:144]),
                plugin_offsets=raw["plugin_offsets"], atomics=raw["atomics"],
                hierarchies=hierarchies, skins=skins, calibrated_scale=None, retargeting_verified=False)


def inspect_once(observer, memory):
    memory.total = 0
    started = time.perf_counter()
    before = observer.read()
    validate_snapshot(observer, before)
    capture = Capture(memory)
    require(capture.word(FRAME_COUNTER) == before["frame_counter"], "frame_advanced_before_read")
    raw = read_rig(capture, before["ped_address"])
    capture.revalidate()
    after = observer.read()
    validate_snapshot(observer, after)
    require(snapshot_stamp(before) == snapshot_stamp(after), "scene_changed_during_read")
    require(before["sequence"] == after["sequence"] and before["frame_counter"] == after["frame_counter"]
            and before["game_timer"] == after["game_timer"]
            and memory.read(FRAME_COUNTER, 4) == struct.pack("<I", after["frame_counter"]),
            "frame_advanced_during_read")
    elapsed = (time.perf_counter()-started)*1000
    require(elapsed+before["age_ms"] <= 1000, "rig_snapshot_expired")
    return dict(accepted=True, scene=snapshot_stamp(after), sequence=after["sequence"],
                frame_counter=after["frame_counter"], game_timer=after["game_timer"],
                observed_player_position=after["position"], age_ms=after["age_ms"],
                capture_ms=elapsed, bytes_read=memory.total, rig=describe_rig(raw))


def inspect(observer, memory, attempts=30):
    require(type(attempts) is int and 1 <= attempts <= 120, "invalid_attempt_limit")
    for address, hexadecimal in GUARDS.items():
        expected = bytes.fromhex(hexadecimal)
        require(memory.read(address, len(expected)) == expected, f"getter_guard_mismatch_{address:08x}")
    failures = Counter()
    for attempt in range(attempts):
        try:
            result = inspect_once(observer, memory)
            return dict(result, attempts=attempt+1, rejections=dict(failures), getter_guards=len(GUARDS))
        except (Rejected, OSError, ValueError) as error:
            failures[str(error)[:180]] += 1
        if attempt+1 < attempts:
            time.sleep(.01)
    return dict(accepted=False, attempts=attempts, rejections=dict(failures), getter_guards=len(GUARDS))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--attempts", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(0 < args.pid <= 0xffffffff and 1 <= args.attempts <= 120, "invalid_cli_bounds")
    require(not args.output.exists(), "refusing_to_overwrite_report")
    observer = memory = None
    result = dict(format="gta-thaw-cj-rig-inspection-v1", read_only=True, physics_ready=False)
    try:
        observer = Observer(args.pid)
        result["executable"] = str(observer.exe)
        memory = Memory(observer)
        result.update(inspect(observer, memory, args.attempts))
    except (ValueError, OSError) as error:
        result.update(accepted=False, reason=str(error)[:300])
    finally:
        if memory:
            memory.close()
        if observer:
            observer.close()
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: value for key, value in result.items() if key != "rig"}, allow_nan=False))
    return 0 if result.get("accepted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
