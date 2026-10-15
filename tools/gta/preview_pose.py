"""Publish audited native THAW samples for a bounded, opt-in CJ render preview.

Only the ASI-owned pose mapping is writable. GTA process handles stay read-only.
The renderer restores its own complete HAnim matrix block after every draw.
"""
from __future__ import annotations

import argparse
import bisect
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import math
from pathlib import Path
import struct
import time

from inspect_rig import Memory, matrix_summary, u32
from native.inspect_state import Observer
from probe_world import snapshot_stamp, validate_snapshot
from atomic_store import load as load_atomic_store
from retarget_pose import IDENTITY, animation_timeline, decode_bits, inverse, parents_from_nodes, require, rigid

HEADER = struct.Struct("<8s4I3Q16I")
NODE = struct.Struct("<iiiI")
MATRICES = struct.Struct("<512f")
SOURCE_CLOCK = struct.Struct("<3Q")
VISUAL = struct.Struct('<16fI')
DETACHED = struct.Struct('<16fI')
PACKET_SIZE = HEADER.size + 32*NODE.size + 2*MATRICES.size + 32 + 256 + SOURCE_CLOCK.size + VISUAL.size + DETACHED.size
MAPPING_SIZE = PACKET_SIZE + 8
assert HEADER.size == 112 and PACKET_SIZE == 5168 and MAPPING_SIZE == 5176


def matrix_bytes(matrices):
    require(len(matrices) == 32, "Preview requires exactly 32 CJ matrices")
    for matrix in matrices:
        rigid(matrix)
        require(max(abs(v) for v in matrix[12:15]) <= 10, "Unbounded model-space pose")
    return MATRICES.pack(*(v for matrix in matrices for v in matrix))


def checked_model(frame, root_bind):
    models = frame["model_row_major"]
    data = matrix_bytes(models)
    model_hash = hashlib.sha256(struct.pack("<512d", *(v for m in models for v in m))).hexdigest()
    require(model_hash == frame["target_model_sha256"], "Pose data hash mismatch")
    require(math.dist(models[0][12:15], root_bind[12:15]) < .0001, "Pose moved the anchored root")
    return data


def prepare_rig(rig_bytes):
    """Validate the immutable target binding independently of a recorded clip."""
    require(len(rig_bytes) <= 2000000, "Oversized target rig")
    target = json.loads(rig_bytes)
    require(target.get("format") == "gta-thaw-cj-rig-inspection-v1" and target.get("accepted") is True,
            "A successful fresh target rig inspection is required")
    rig = target["rig"]
    require(len(rig["atomics"]) == len(rig["hierarchies"]) == len(rig["skins"]) == 1,
            "First preview supports only the observed single-atomic CJ rig")
    atomic, hierarchy, skin = rig["atomics"][0], rig["hierarchies"][0], rig["skins"][0]
    require(hierarchy["count"] == skin["count"] == 32 and hierarchy["flags"] == 0x3000
            and hierarchy["matrix_space"] == "world"
            and atomic["hierarchy_address"] == hierarchy["address"]
            and atomic["skin_address"] == skin["address"], "Unsupported rig binding")
    nodes = hierarchy["nodes"]
    parents, _ = parents_from_nodes(nodes)
    require(parents[0] == -1 and nodes[0]["bone_id"] == nodes[0]["matrix_index"] == 0,
            "Unsupported root node")
    binds = [m["affine_row_major"] for m in skin["inverse_bind_invariants"]]
    bind_bytes = matrix_bytes(binds)
    root_bind = inverse(binds[0])
    return dict(target=target, rig=rig, atomic=atomic,
                hierarchy=hierarchy, skin=skin,
                nodes=b"".join(NODE.pack(n["bone_id"], n["matrix_index"], n["parent_flags"], n["frame_address"])
                               for n in nodes),
                bind_bytes=bind_bytes, root_bind=root_bind)


def prepare(rig_bytes, report_bytes, frame_index):
    require(len(report_bytes) <= 8000000, "Oversized preview input")
    prepared = prepare_rig(rig_bytes)
    report = json.loads(report_bytes)
    require(report.get("format") == "gta-thaw-offline-retarget-v1"
            and report.get("offline_checks_passed") is True and report.get("target_bones") == 32,
            "An audited offline retarget report is required")
    require(len(report.get("inputs", [])) == 3
            and report["inputs"][1]["sha256"] == hashlib.sha256(rig_bytes).hexdigest(),
            "Retarget report was generated for a different target rig; regenerate it")
    frames = report.get("frames", [])
    require(type(frame_index) is int and 0 <= frame_index < len(frames), "Invalid pose frame index")
    nodes = prepared["hierarchy"]["nodes"]
    require([(n["bone_id"], n["matrix_index"]) for n in nodes] ==
            [(n["bone_id"], n["matrix_index"]) for n in report["nodes"]],
            "Report node identities differ from inspected rig")
    prepared.update(report=report, frame_index=frame_index,
                    model_bytes=checked_model(frames[frame_index], prepared["root_bind"]))
    return prepared


def prepare_samples(prepared, records, motion, with_board):
    source, timeline, _ = animation_timeline(records)
    report = prepared["report"]
    require(len(source) == len(report["frames"]), "Source/report frame count differs")
    if motion:
        require(timeline["sample_hz"] == 120 and all(report.get(k) == v for k,v in timeline.items()),
                "Motion requires an audited complete native 120 Hz timeline")
    selected = range(timeline["playback_frames"]) if motion else [prepared["frame_index"]]
    samples = []
    for index in selected:
        frame, native = report["frames"][index], source[index]
        raw, values = decode_bits(native["palette_bits"],832)
        require(hashlib.sha256(raw).hexdigest() == native["palette_sha256"] == frame["source_palette_sha256"]
                and frame["time"] == native["time"] and native["active_bones"] == 52,
                "Native palette and target frame are not the same original sample")
        palette = raw[48*64:52*64] if with_board else bytes(256)
        if with_board:
            for bone in range(48,52):
                matrix = values[bone*16:(bone+1)*16]
                rigid(matrix)
                require(max(abs(v) for v in matrix[12:15]) <= 10000, "Unbounded board palette")
        samples.append(dict(index=index, time=frame["time"], model_bytes=checked_model(frame,prepared["root_bind"]),
                            board_palette=palette))
    return samples, timeline


def sample_index(times, elapsed, duration, hz):
    require(math.isfinite(elapsed) and elapsed >= 0, "Invalid playback time")
    # Bounded preview replay: hold the exact endpoint for one sample, then restart the clip.
    time_in_clip = min(elapsed % (duration+1/hz),duration)
    return max(0,bisect.bisect_right(times,time_in_clip)-1)


def validate_scene(observer, state, prepared):
    validate_snapshot(observer, state)
    require(state["age_ms"] <= 100 and state["flags"] & 8, "Preview requires a fresh foreground game")
    require(snapshot_stamp(state) == prepared["target"]["scene"],
            "Rig process/scene changed; inspect and retarget again")
    require(Path(prepared["target"]["executable"]).resolve() == observer.exe.resolve(),
            "Rig belongs to a different GTA installation")


def live_identity(memory, p):
    """Read only stable identities/binds; animated matrices are checked by the render thread."""
    rig, atomic, hierarchy, skin = p["rig"], p["atomic"], p["hierarchy"], p["skin"]
    ped, clump = p["target"]["scene"]["ped_address"], rig["clump_address"]
    expected = {
        ped: 0x86D168, ped+0x18: clump,
        clump+8: atomic["address"]+0x40, clump+12: atomic["address"]+0x40,
        atomic["address"]+0x3C: clump,
        atomic["address"]+0x40: clump+8, atomic["address"]+0x44: clump+8,
        atomic["address"]+0x18: atomic["geometry_address"],
        0xC978A4: rig["plugin_offsets"]["atomic_hierarchy"],
        0xC978A8: rig["plugin_offsets"]["geometry_skin"],
        atomic["address"]+rig["plugin_offsets"]["atomic_hierarchy"]: hierarchy["address"],
        atomic["geometry_address"]+rig["plugin_offsets"]["geometry_skin"]: skin["address"],
        hierarchy["address"]: 0x3000, hierarchy["address"]+4: 32,
        hierarchy["address"]+8: hierarchy["matrix_address"],
        hierarchy["address"]+16: hierarchy["node_address"],
        skin["address"]: 32, skin["address"]+12: skin["inverse_bind_address"],
    }
    for address, value in expected.items():
        require(u32(memory.read(address, 4)) == value, "Live CJ rig identity changed")
    require(memory.read(hierarchy["node_address"], 512) == p["nodes"], "Live CJ node identity changed")
    raw = memory.read(skin["inverse_bind_address"], 2048)
    binding = (raw, p["bind_bytes"])
    if p.get("_live_bind_validation") != binding:
        matrices = [matrix_summary(raw[i*64:(i+1)*64])["affine_row_major"] for i in range(32)]
        require(matrix_bytes(matrices) == p["bind_bytes"], "Live CJ inverse binds changed")
    require(u32(memory.read(ped+0x18, 4)) == clump, "CJ clump changed during validation")
    # Only reuse validation of identical immutable bytes after all fresh reads.
    p["_live_bind_validation"] = binding


def packet(prepared, state, sequence, now_ms, *, request=0, source_tick=0, source_time_us=0):
    p, rig = prepared, prepared["rig"]
    atomic, hierarchy, skin = p["atomic"], p["hierarchy"], p["skin"]
    board_hash = p.get("board_hash", bytes(32))
    require(all(type(v) is int and 0 <= v < 2**64 for v in (request, source_tick, source_time_us))
            and (request != 0 or source_tick == source_time_us == 0)
            and (source_tick == 0) == (source_time_us == 0), "Invalid pose source clock")
    header = HEADER.pack(b"GTAPOSE5", 5, PACKET_SIZE, state["pid"], state["epoch"],
        state["creation_100ns"], sequence, now_ms, state["area"], state["ped_area"],
        state["ped_address"], rig["clump_address"], atomic["address"], atomic["geometry_address"],
        hierarchy["address"], skin["address"], hierarchy["matrix_address"], hierarchy["node_address"],
        skin["inverse_bind_address"], rig["plugin_offsets"]["atomic_hierarchy"],
        rig["plugin_offsets"]["geometry_skin"], 32, 0, int(any(board_hash)))
    board_palette = p.get("board_palette",bytes(256))
    require(len(board_hash) == 32 and len(board_palette) == 256, "Invalid board packet fields")
    visual = p.get('visual_delta', IDENTITY)
    rigid(visual)
    require(max(abs(v) for v in visual[12:15]) <= 10, 'Unbounded CJ visual pivot')
    visible = p.get('board_visible', True)
    require(type(visible) is bool, 'Invalid original board visibility')
    board_world, board_object_id = p.get('board_world', IDENTITY), p.get('board_object_id', 0)
    rigid(board_world)
    require(type(board_object_id) is int and 0 <= board_object_id <= 0xffffffff and
            max(abs(v) for v in board_world[12:15]) <= 20000 and
            (board_object_id != 0 or list(board_world) == list(IDENTITY)) and
            (board_object_id == 0 or (visible and any(board_hash))), 'Invalid detached board presentation')
    return (header + p["nodes"] + p["bind_bytes"] + p["model_bytes"] + board_hash + board_palette
            + SOURCE_CLOCK.pack(request, source_tick, source_time_us)
            + VISUAL.pack(*visual, int(visible and any(board_hash)))
            + DETACHED.pack(*board_world, board_object_id))


class Publisher:
    def __init__(self, observer, *, transport="POSE_V5", packet_size=PACKET_SIZE):
        require((transport, packet_size) in (("POSE_V5", PACKET_SIZE), ("HOLD_V1", 72), ("MOVE_V1", 104), ("MOVE_V3", 144), ("CAMERA_V2", 140), ("HUD_V1", 73860), ("STATUS_V1", 64), ("MENU_V1", 2472), ("ATTACK_V1", 104)),
                "Unsupported bounded adapter transport")
        self.packet_size = packet_size
        self.api, self.mapping, self.view, self.mutex = observer.api, None, None, None
        # Resolve and validate our helper before taking any publisher ownership.
        self.exchange32 = load_atomic_store()
        self.api.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
        self.api.CreateMutexW.restype = W.HANDLE
        try:
            C.set_last_error(0)
            kind = transport.split("_")[0]
            self.mutex = self.api.CreateMutexW(None, False, f"Local\\THAW_GTA_{kind}_PUBLISHER_{observer.pid}")
            error = C.get_last_error()
            require(bool(self.mutex) and error != 183, "Another preview publisher owns this session")
            self.mapping = self.api.OpenFileMappingW(2, False, f"Local\\THAW_GTA_{transport}_{observer.pid}")
            if not self.mapping:
                raise C.WinError(C.get_last_error())
            self.view = self.api.MapViewOfFile(self.mapping, 2, 0, 0, self.packet_size+8)
            if not self.view:
                raise C.WinError(C.get_last_error())
        except BaseException:
            self.close()
            raise

    def store(self, offset, value):
        require(offset in (0,4+self.packet_size) and self.view and
                (self.view+offset) % 4 == 0, "Unaligned or external seqlock word")
        require(type(value) is int and 0 <= value < 0x80000000, "Invalid seqlock value")
        require(self.exchange32(self.view+offset,value) == 1, "Atomic seqlock store failed")

    def publish(self, body, sequence):
        require(len(body) == self.packet_size and type(sequence) is int and
                0 < sequence < 0x3fffffff, "Invalid packet length/generation")
        generation = sequence*2
        self.store(0, generation-1)
        # Each interlocked store is a full fence, matching the native readers.
        C.memmove(self.view+4, body, len(body))
        self.store(4+self.packet_size, generation)
        self.store(0, generation)

    def clear(self):
        """Release native ownership while retaining the exclusive publisher."""
        if self.view:
            self.store(0, 0)

    def close(self):
        if self.view:
            self.clear()
            self.api.UnmapViewOfFile(self.view)
            self.view = None
        for name in ("mapping", "mutex"):
            handle = getattr(self, name)
            if handle:
                self.api.CloseHandle(handle)
                setattr(self, name, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--target-rig", required=True, type=Path)
    parser.add_argument("--pose-report", required=True, type=Path)
    parser.add_argument("--frame", type=int, default=1)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--board-bundle", type=Path)
    parser.add_argument("--motion", action="store_true", help="Replay only original 120 Hz samples; no interpolation")
    args = parser.parse_args()
    require(0 < args.pid <= 0xffffffff and math.isfinite(args.seconds) and 0 < args.seconds <= 10,
            "Preview requires a positive PID and 0 < seconds <= 10")
    p = prepare(args.target_rig.read_bytes(), args.pose_report.read_bytes(), args.frame)
    # Revalidate the private source provenance as well as the linked target rig.
    inputs = []
    for item in p["report"]["inputs"]:
        raw = Path(item["path"]).read_bytes()
        require(len(raw) <= 2000000 and hashlib.sha256(raw).hexdigest() == item["sha256"],
                "Retarget input changed since the offline proof")
        inputs.append(raw)
    samples,timeline = prepare_samples(p,[json.loads(line) for line in inputs[2].splitlines() if line.strip()],
                                      args.motion,bool(args.board_bundle))
    times = [sample["time"] for sample in samples]
    observer, memory, publisher = Observer(args.pid), None, None
    count, total_read, started = 0, 0, time.monotonic()
    published_samples = set()
    try:
        if args.board_bundle:
            from board_preview import validate_bundle
            require(args.board_bundle.resolve() == (observer.exe.parent/"thaw_gta"/"board-preview.bin").resolve(),
                    "Board bundle must be the isolated adapter's fixed private file")
            p["board_hash"] = validate_bundle(args.board_bundle.read_bytes(), p["bind_bytes"],
                                             hashlib.sha256(args.target_rig.read_bytes()).digest())
        state = observer.read()
        validate_scene(observer, state, p)
        memory = Memory(observer)
        publisher = Publisher(observer)
        playback_started = time.monotonic()
        deadline = playback_started+args.seconds
        hz = 120 if args.motion else 30
        while time.monotonic() < deadline:
            state = observer.read()
            validate_scene(observer, state, p)
            memory.total = 0  # Each bounded iteration has its own read budget.
            live_identity(memory, p)
            total_read += memory.total
            after = observer.read()
            validate_scene(observer, after, p)
            selected = sample_index(times,time.monotonic()-playback_started,timeline["duration"],hz) if args.motion else 0
            current = samples[selected]
            p["model_bytes"],p["board_palette"] = current["model_bytes"],current["board_palette"]
            published_samples.add(current["index"])
            count += 1
            publisher.publish(packet(p, after, count, observer.api.GetTickCount64()), count)
            time.sleep(max(0, min(1/hz,playback_started+count/hz-time.monotonic())))
    finally:
        if publisher:
            publisher.close()
        if memory:
            memory.close()
        observer.close()
    print(json.dumps(dict(format="gta-thaw-pose-preview-v1", published=count,
        elapsed_seconds=time.monotonic()-started, read_process_bytes=total_read,
        selected_frame=args.frame, clip_time=p["report"]["frames"][args.frame]["time"],
        motion=args.motion, original_sample_count=len(samples), published_sample_indices=sorted(published_samples),
        interpolated=False,
        packet_cleared=True, gameplay_enabled=False,
        board_bundle_sha256=p.get("board_hash", b"").hex(),
        rig_sha256=hashlib.sha256(args.target_rig.read_bytes()).hexdigest(),
        pose_report_sha256=hashlib.sha256(args.pose_report.read_bytes()).hexdigest())))


if __name__ == "__main__":
    main()
