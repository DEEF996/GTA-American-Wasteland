"""Bounded stock Ollie evaluator experiment; called only by probe_loader.

Original inputs remain private. This module contains metadata and ABI wiring,
not a clip decoder, skeleton solver, or GTA pose adapter.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct

import probe_loader as host

INPUTS = {
    "thps7_human.ske": (5376, "342629a90da89604285a1c95c3b899581d5b2f12fc9799199b06828c6c1c54c4"),
    "data/pak/perm_anims/perm_anims.pak.wpc": (1342848, "8e78795dcbd562093949685162d8584185adaaa76b0f5451fcdc02578feb1fd2"),
    "data/anims/standardkeyQ.bin": (2048, "07c5975374246679dd72c8cd1162c00ab01ef93c20342dbb385f710f5acdd876"),
    "data/anims/standardkeyT.bin": (2048, "504c8d229389c48841e2013dddc586765652fe65191ee53a99908f9b07b33548"),
}
CLIP_SHA = "721881cc4727796714b1a52acbde7a4baea5febfcf2682323da663ab1cd79d86"
BONES = 52
CANARY = bytes([0xa5]) * 16


# Region and routed-entry pins have the scopes documented in the audit.
PINS = {
    "info_ctor": ("skeleton_info_ctor", 0x4ef500, 17, "aa9e2ff13cc2d10300b76b27147e33c53b555f98a917a4433934c4986596c3de"),
    "info_load": ("skeleton_info_load_ram", 0x4f02d0, 43, "cdc6f514cad08cc91ecd9eadbb0fa12ef1889cbb7d8da202e00580e0c6582f27"),
    "skeleton_ctor": ("skeleton_ctor", 0x4f0360, 388, "65d9ddff4d279022a5d4d2c6d245c67bab8a0fd7d834e171460cca80f7d03a82"),
    "clip_ctor": ("ska_ctor", 0x4bf8c0, 6, "11a19120abbceb6d7f667642f1b9aa8be32be24895714a9f3dc2b39ac9bb9113"),
    "clip_load": ("ska_ram_load", 0x4c1a80, 179, "f2f0aac4b7f9a8134a197e3fddf03ce6aa752f2110b8f5bd12fdce15b3074516"),
    "evaluate": ("ska_evaluate", 0x4c1b40, 1792, "ce2b4691785cdd9364f5523369090ed67c03806bb739fcba4a2ecce967c24730"),
    "apply": ("skeleton_apply_pose", 0x4f0d80, 1293, "77966a9c29a10c2855b58421b7a0565fab159454b62631ef1641557d79f9ffd1"),
    "clip_dtor": ("ska_dtor", 0x4bf8f0, 166, "b4be786148a23b8584860957c8288599935d84f0f891c4a0f856b176a4b666de"),
    "cache_init": ("cache_manager_init", 0x4bcc10, 6, "da13f03a0efe763a52b21b00ae15a7ee5bf46c10d9f1b4ce45a0f2a88ef0c065"),
    "cache_release": ("cache_manager_release", 0x4bd2c0, 6, "e7a76cd793ca42f7bf3ce06b48f6e2b1fa4ed47ad41e5e07ff22a50bb32cc239"),
}


def read_assets(manifest_path: Path, image_manifest: Path):
    host.require(manifest_path.is_absolute(), "Asset manifest path must be absolute")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    native = json.loads(image_manifest.read_text(encoding="utf-8-sig"))
    directory = manifest_path.resolve().parent
    host.require(manifest.get("format") == "gta-thaw-private-stock-assets-v1", "Unsupported stock asset cache")
    host.require(Path(manifest["directory"]).resolve() == directory and Path(manifest["source"]).resolve() == Path(native["source"]).resolve(), "Asset directory or source differs")
    host.require(manifest.get("sources_revalidated_before_publish") is True and manifest.get("original_files_modified") is False, "Asset source verification is absent")
    rows = manifest["files"]
    data = {}
    for name, (size, digest) in INPUTS.items():
        matches = [row for row in rows if row["path"] == name]
        host.require(len(matches) == 1 and matches[0]["bytes"] == size and matches[0]["sha256"] == digest, "Pinned animation input is absent")
        path = directory / name
        host.require(path.resolve().is_relative_to(directory), "Animation input escapes private cache")
        contents = path.read_bytes()
        host.require(len(contents) == size and hashlib.sha256(contents).hexdigest() == digest, f"Animation input differs: {name}")
        data[name] = contents
    pak = data.pop("data/pak/perm_anims/perm_anims.pak.wpc")
    header = struct.unpack_from("<8I", pak, 8416)
    # SKA member keys are skeleton CRC + clip CRC, not readable filenames.
    host.require(header[0] == 0x745dcd45 and header[3] == 0xd4f9550e and header[2] == 2312 and header[1] + 8416 == 888864, "Original Ollie PAK member header differs")
    clip = pak[888864:888864 + 2312]
    host.require(hashlib.sha256(clip).hexdigest() == CLIP_SHA, "Original Ollie member hash differs")
    host.require(struct.unpack_from("<2I", clip) == (0x28, 0x06834000) and clip[13] == BONES and struct.unpack_from("<H", clip, 18)[0] == 0, "Original Ollie format differs")
    data["Ollie"] = clip
    return data


def sample_times(duration, sample_hz=None):
    host.require(math.isfinite(duration) and .3 < duration < 1, "Unexpected bounded Ollie duration")
    host.require(sample_hz in (None, 120), "Only the bounded 120 Hz native sample grid is supported")
    if sample_hz is None:
        return [0., .3, duration, .3], 3
    playback = [i/sample_hz for i in range(math.ceil(duration*sample_hz))]
    playback.append(duration)
    return playback+[.3, .3], len(playback)


def run(api, process, record, deadline, log, inputs, sample_hz=None):
    def read(address, size):
        return host.remote_read(api, process, address, size)

    def write(address, data):
        host.remote_write(api, process, address, data)

    def word(address):
        return struct.unpack("<I", read(address, 4))[0]

    def call(name, arguments=(), this=None):
        return host.native_call(api, process, PINS[name], list(arguments), record, deadline, log, this)

    guards = []

    def guarded(name, contents):
        size = (len(contents) + 15) & ~15
        base = host.success(api.VirtualAllocEx(process, None, size + 32, 0x3000, 4), "Allocate guarded original animation storage")
        host.require(base % 16 == 0 and base + size + 32 < 0x100000000, "Animation storage is not aligned x86 memory")
        write(base, CANARY + contents + bytes(size - len(contents)) + CANARY)
        guards.append((name, base, size))
        return base + 16

    def check_guards():
        for name, base, size in guards:
            host.require(read(base, 16) == CANARY and read(base + 16 + size, 16) == CANARY, f"Original animation call exceeded {name} storage")

    host.require(word(0x767410) == 0, "Original animation cache already initialized")
    key_q, key_t = inputs["data/anims/standardkeyQ.bin"], inputs["data/anims/standardkeyT.bin"]
    original_q, original_t = read(0x767438, 2048), read(0x767c38, 2048)
    host.require(not any(original_q) and not any(original_t), "Original standard-key tables were already initialized")
    # Data-only startup inputs: exact files read by original animation startup.
    write(0x767438, key_q)
    write(0x767c38, key_t)
    call("cache_init", (0x20000, 0x400, 4))
    cache = word(0x767410)
    host.require(cache >= 0x10000 and word(cache + 0xdc) == 0x400 and word(cache + 0xe0) == 4, "Original full-resolution animation cache was not initialized")
    ske = guarded("SKE", inputs["thps7_human.ske"])
    info = guarded("SkeletonInfo", bytes(16))
    skeleton = guarded("Skeleton", bytes(32))
    clip_data = guarded("Ollie", inputs["Ollie"])
    clip = guarded("SKA", bytes(32))
    q = guarded("quaternions", bytes(BONES * 16))
    t = guarded("translations", bytes(BONES * 16))
    active = guarded("active-bone flags", bytes(BONES))
    host.require(call("info_ctor", this=info) == info and word(info) == 0x694280, "Original SkeletonInfo constructor differs")
    host.require(call("info_load", (ske, len(inputs["thps7_human.ske"]), 1), this=info) & 0xff, "Original skeleton RAM load failed")
    host.require(call("skeleton_ctor", (info,), this=skeleton) == skeleton and word(skeleton + 0x14) == BONES, "Original skeleton constructor/bone count differs")
    bind_ptr = word(ske + 0x2c)
    host.require(ske <= bind_ptr and bind_ptr + BONES * 16 <= ske + len(inputs["thps7_human.ske"]), "Native bind translations escaped borrowed skeleton data")
    bind = read(bind_ptr, BONES * 16)
    host.require(call("clip_ctor", this=clip) == clip, "Original SKA constructor differs")
    host.require(call("clip_load", (clip_data, len(inputs["Ollie"]), 1), this=clip) & 0xff, "Original Ollie RAM load failed")
    host.require(word(clip) == 0x6933b4 and word(clip + 8) == clip_data, "Original loaded SKA object layout differs")
    expected_pointers = {0x14: 0, 0x20: clip_data + 48, 0x24: clip_data + 152, 0x18: clip_data + 256, 0x1c: clip_data + 2063}
    host.require(all(word(clip_data + offset) == value for offset, value in expected_pointers.items()), "Original compressed Ollie layout differs")
    check_guards()
    duration = struct.unpack_from("<f", inputs["Ollie"], 8)[0]
    times, playback_frames = sample_times(duration, sample_hz)
    frames = []
    # Every sample enters original Eval and Apply; no host interpolation is involved.
    for seconds in times:
        write(q, bytes(BONES * 16))
        write(t, bind)
        write(active, bytes(BONES))
        time_bits = struct.unpack("<I", struct.pack("<f", seconds))[0]
        call("evaluate", (q, t, active, time_bits, 0xffffffff, 0), this=clip)
        channels = read(q, BONES * 16) + read(t, BONES * 16)
        host.require(all(math.isfinite(value) for value in struct.unpack("<416f", channels)), "Native Ollie channels contain nonfinite values")
        host.require(read(active, BONES) == bytes([1]) * BONES and word(clip + 28) != 0, "Original Ollie did not produce 52 active bones and a native clip cache")
        call("apply", (q, t), this=skeleton)
        palette_ptr = word(skeleton + 4)
        host.require(palette_ptr >= 0x10000 and palette_ptr % 16 == 0, "Original skeleton palette is absent or unaligned")
        palette = read(palette_ptr, BONES * 64)
        host.require(all(math.isfinite(value) for value in struct.unpack("<832f", palette)), "Native skeleton palette contains nonfinite values")
        check_guards()
        frame = {"time": seconds, "time_f32_bits": time_bits, "active_bones": BONES,
                 "channel_sha256": hashlib.sha256(channels).hexdigest(),
                 "palette_sha256": hashlib.sha256(palette).hexdigest(),
                 "quaternion_bits": list(struct.unpack("<208I", channels[:BONES * 16])),
                 "translation_bits": list(struct.unpack("<208I", channels[BONES * 16:])),
                 "palette_bits": list(struct.unpack("<832I", palette))}
        frames.append(frame)
        log("animation_frame", **frame)
    first_repeat, second_repeat = (1, 3) if sample_hz is None else (-2, -1)
    host.require(frames[0]["palette_sha256"] != frames[first_repeat]["palette_sha256"], "Original Ollie pose did not change over time")
    host.require(frames[first_repeat]["channel_sha256"] == frames[second_repeat]["channel_sha256"]
                 and frames[first_repeat]["palette_sha256"] == frames[second_repeat]["palette_sha256"],
                 "Repeated original sample is not deterministic")
    call("clip_dtor", this=clip)
    call("cache_release")
    host.require(word(0x767410) == 0, "Original cache release did not clear its singleton")
    # The stock Skeleton destructor is not yet mapped from a verified caller.
    # Keep its Info and borrowed SKE alive until this bounded child terminates.
    check_guards()
    host.require(read(0x767438, 2048) == key_q and read(0x767c38, 2048) == key_t, "Native animation changed the standard-key input tables")
    log("animation_pass", native_frames=len(frames), native_active_bones=BONES,
        sample_hz=sample_hz, duration=duration, playback_frames=playback_frames,
        unique_sample_times=times[:playback_frames],
        native_skeleton_info_and_placements_retained_until_process_exit=True,
        native_clip_and_cache_destroyed=True, deterministic_repeat=True,
        host_clip_decoder=False, host_palette_writes=False,
        scope="one original Ollie clip and 52-bone CPU palette; no AnimTree, skater or GTA retargeting")
