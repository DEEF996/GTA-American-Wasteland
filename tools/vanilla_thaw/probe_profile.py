"""Bounded original stock ProfileManager and ten native profile statistics.

The original factory owns the profile objects. This proof never constructs a
replacement CProfile, CSkater, or renderer. Sources stay alive until child exit.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys

import probe_loader as host

CUSTOM = 0x4b49b79f
STATS = (0x439f4704, 0xaf895b3f, 0x9b65d7b8, 0xf0d90109, 0xedf5db70,
         0x6dcb497c, 0x9016b4e7, 0xf73a13e3, 0xae798769, 0xb1fc0722)
STAT_NAMES = ("air", "run", "ollie", "speed", "spin", "flip_speed", "switch",
              "rail_balance", "lip_balance", "manual_balance")
# The native key is a startup literal, not the checksum of its display label.
INFO_HEAP = 0x08682d24
INPUTS = {
    "scripts/game/skater/skater_profile.qb.wpc": (30004, "2079462ef4376a217dc2b58a760b0d5db641cf7fdee8cfd7d14b48c27f673aea"),
    "scripts/game/skater/protricks.qb.wpc": (4312, "dee8327433ddb614db57104014d02f3f80f16090b0666fe7886ed7a1be63a40b"),
    "pak/cas/cas_wpc_scripts.qb.wpc": (338260, "9b2bb670a86eb48609fa635c4c29a34c77273030fe3e860bd55a6a9358e67dd5"),
}

PINS = {
    "ManagerFactory": ("ProfileManagerFactory", 0x58a050, 74, "3cf539ac3ac0e1869fa72bd3282c1e3bd246077d4d05d45c0976be6d69504f82"),
    "NamedHeapCreate": ("ProfileNamedHeapCreate", 0x626dc0, 230, "7ff8c3762d9ebde7d37646f4266ba2e4557396220a46ce6805dd0a654fbc3bb6"),
    "NamedHeapLookup": ("ProfileNamedHeapLookup", 0x6261a0, 25, "da8219a55cc86596535a5163f4352dfcb3db6fb2d7700b974baa12ad29c83726"),
    "ManagerAdd": ("ProfileManagerAdd", 0x589fa0, 174, "87366629b0a32f48e0ead05b6a44b8a75600635a14e1f3be7dd2c800eb0257a6"),
    "ManagerSelect": ("ProfileManagerSelect", 0x589f30, 109, "7f1bf3c8f33d72b80f587b16e270b76c810651c2be8b8f001672e841806f5ae0"),
    "Current": ("ProfileCurrent", 0x589880, 8, "2dc6dc21534041556b3066d81af44150bde094fb58142e5c83d5710903bfa4be"),
    "SetStat": ("ProfileSetStat", 0x5974a0, 37, "31873c0487739f3b0402245673ad5ecb630ff930949e21907535af05a0a13985"),
    "GetStat": ("ProfileGetStat", 0x597480, 27, "67dae8b697dc88d359ee81408b4df6a2bed04a47213e8265fd50cf50060d5986"),
    "ArrayPointer": ("ProfileArrayPointer", 0x40a360, 19, "9265d1038aff2504422cdb41e174ee200a9d7d967d2db721bd5fe0b1ae06b7e9"),
    "HeapFreeBytes": ("ProfileHeapFreeBytes", 0x6255e0, 46, "81db25c30a118d749a9fc8adc051ad84cc166a47db698ac5ee831cdca2dfd885"),
    "SkaterInfoHeap": ("ProfileSkaterInfoHeap", 0x6262f0, 21, "8f783da2feb63991f85f8e912473dafcca0bb8bbadc903a04c86ab5aa6eff60c"),
    "GetChecksum": ("ProfileGetChecksum", 0x415120, 20, "7d5d42b748f990203728b54e5b7f63fd17af6690ae3b5abc6840f397a8a45a69"),
    "GlobalStructure": ("ProfileGlobalStructure", 0x4174c0, 30, "1a2622133ceebd65d1b3a9bd1af92ddbd77235b842deaba82009f3812d5b443a"),
    "GlobalArray": ("ProfileGlobalArray", 0x4174e0, 30, "a02c7f3b25c54f55949c43b4ecc8505b9a6aef6afc96f2b5ce163a69e879bfa6"),
}


def source_rows(rows, directory):
    host.require(len(rows) == len(INPUTS), "Profile cache must contain exactly three pinned sources")
    selected = []
    for name, (size, digest) in INPUTS.items():
        matches = [row for row in rows if row["canonical_name"] == name]
        host.require(len(matches) == 1 and matches[0]["bytes"] == size and matches[0]["sha256"] == digest, "Pinned profile source is absent or duplicated")
        path = Path(matches[0]["path"])
        host.require(path.is_absolute() and path.resolve().is_relative_to(directory), "Profile source escapes private cache")
        selected.append((path.resolve(), name, size, digest))
    host.require(len({row[0] for row in selected}) == len(INPUTS), "Profile inputs share a path")
    return selected


def read_inputs(manifest_path: Path, image_manifest: Path):
    host.require(manifest_path.is_absolute(), "Profile manifest path must be absolute")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    native = json.loads(image_manifest.read_text(encoding="utf-8-sig"))
    directory = manifest_path.resolve().parent
    host.require(manifest.get("format") == "gta-thaw-private-stock-profile-v1", "Unsupported stock profile cache")
    host.require(Path(manifest["directory"]).resolve() == directory and Path(manifest["source"]).resolve() == Path(native["source"]).resolve(), "Profile directory or source differs")
    host.require(manifest.get("sources_revalidated_before_publish") is True and manifest.get("original_files_modified") is False, "Profile source verification is absent")
    inputs = []
    for path, name, size, digest in source_rows(manifest["sources"], directory):
        contents = path.read_bytes()
        host.require(len(contents) == size and hashlib.sha256(contents).hexdigest() == digest, f"Original profile input differs: {name}")
        inputs.append((contents, name))
    return inputs


def run(api, process, record, deadline, log, inputs, existing_manager=None, preloaded=None):
    def read(address, size):
        return host.remote_read(api, process, address, size)

    def word(address):
        return struct.unpack("<I", read(address, 4))[0]

    def call(name, arguments=(), this=None):
        pin = PINS.get(name) or host.VM_PINS.get(name) or host.QB_PINS[name]
        return host.native_call(api, process, pin, list(arguments), record, deadline, log, this)

    manager = 0x842b18
    preloaded = preloaded or {}
    if existing_manager is None:
        host.require(not preloaded and word(0x83e894) == 0 and word(0x83e898) == 0, "Original profile manager must initially be absent")
    else:
        host.require(existing_manager >= 0x10000 and word(0x83e894) == existing_manager and
                     word(0x83e898) == 1 and "scripts/game/skater/skater_profile.qb.wpc" in preloaded and
                     set(preloaded) <= {"scripts/game/skater/skater_profile.qb.wpc", "scripts/game/skater/protricks.qb.wpc"},
                     "Combined Game profile requires its original empty manager and existing original core profile sources")
    host.require(word(0x84388c) == manager, "Original allocator singleton differs")
    host.require(all(word(address) == 0 for address in (0x6dcf84, 0x6dcf8c, 0x6dcf90)), "Profile proof requires an idle original VM")
    host.require(all(word(address) == 0 for address in (0x6dd0ec, 0x6dd0f4, 0x6dd0f8, 0x6dbd8c, 0x6dbd94, 0x6dbd98)), "Original fixed structure/component pools must remain inactive")
    host.require(read(0x6a22cc, 9) == b"skt_info\0" and call("NamedHeapLookup", (INFO_HEAP, 0), this=manager) == 0, "Original SkaterInfo heap must initially be absent")
    host.require(word(manager + 0xc0) < 0x30, "Original heap registry is full")
    host.require(any(read(manager + 0x18c + index * 0x50 + 0x4c, 1) == b"\0" for index in range(32)), "Original named heap slots are full")
    heap = call("NamedHeapCreate", (INFO_HEAP, 0x30d40, 0x6a22cc, 0), this=manager)
    host.require(heap >= 0x10000 and call("NamedHeapLookup", (INFO_HEAP, 0), this=manager) == heap and call("SkaterInfoHeap", this=manager) == heap, "Original named SkaterInfo heap factory failed")
    host.require(call("HeapFreeBytes", this=heap) == 0x30d40, "Original SkaterInfo heap capacity differs")
    profile_manager = call("ManagerFactory", (1,)) if existing_manager is None else existing_manager
    host.require(profile_manager >= 0x10000 and word(0x83e894) == profile_manager and word(0x83e898) == 1, "Original ProfileManager factory failed")
    host.require(all(word(profile_manager + offset) == 0 for offset in (0, 0x14, 0x18, 0x1c)), "Original ProfileManager is not initially empty")
    source_buffers = []
    for source, filename in inputs:
        if filename in preloaded:
            owned, size = preloaded[filename]
            host.require(size == len(source) and owned >= 0x10000 and read(owned, 1) == b"\1", "Preloaded original profile source extent/state differs")
            source_buffers.append((owned, size))
            log("profile_source_reused", filename=filename, bytes=size, sha256=hashlib.sha256(source).hexdigest(), already_loaded_by_original_game_bootstrap=True)
            continue
        name_offset = (len(source) + 15) & ~15
        encoded = filename.encode("ascii") + b"\0"
        owned = host.success(api.VirtualAllocEx(process, None, name_offset + len(encoded), 0x3000, 4), "Allocate owned original profile source")
        host.remote_write(api, process, owned, source)
        host.remote_write(api, process, owned + name_offset, encoded)
        call("load", (owned + name_offset, owned, 0))
        host.require(read(owned, 1) == b"\1", "Original profile source was not processed")
        source_buffers.append((owned, len(source)))
        log("profile_source_loaded", filename=filename, bytes=len(source), sha256=hashlib.sha256(source).hexdigest())
    array = call("GlobalArray", (0xf31e8ff8, 0))
    host.require(array >= 0x10000 and word(array) >> 16 & 0xff == 10 and word(array + 4) == 16, "Original stock master profile array shape differs")
    matches = []
    for index in range(16):
        source_profile = call("ArrayPointer", (index,), this=array)
        host.require(source_profile >= 0x10000, "Original master profile has a null record")
        host.remote_write(api, process, record + 72, bytes(4))
        present = call("GetChecksum", (0xa1dc81f9, record + 72, 0), this=source_profile) & 0xff
        if present and word(record + 72) == CUSTOM:
            matches.append((index, source_profile))
    host.require(len(matches) == 1 and matches[0][0] == 0, "Original source must define exactly one custom_classic profile at its audited index")
    for checksum in (0x33d0acde, 0xfe54486d, 0x5713e9cb):
        host.require(call("GlobalStructure", (checksum, 0)) >= 0x10000, "Original profile appearance or trick structure is absent")
    graphics = call("GlobalArray", (0xa1e730ac, 0))
    host.require(graphics >= 0x10000 and word(graphics + 4) == 10, "Original appearance graphic array differs")
    host.require(call("ManagerAdd", (matches[0][1],), this=profile_manager) & 0xff, "Original manager rejected the complete source profile")
    call("ManagerSelect", (CUSTOM,), this=profile_manager)
    profile = call("Current", this=profile_manager)
    host.require(profile >= 0x10000 and profile % 4 == 0, "Original manager returned no aligned profile")
    vtable = word(profile)
    host.require(vtable % 4 == 0 and 0x68e000 <= vtable < 0x6bc000, "Original appearance vtable is outside the pinned read-only image section")
    host.require(word(profile_manager) == 1 and word(profile_manager + 0x14) == profile and word(profile_manager + 0x1c) == 0 and word(profile + 0x68) == 0, "Original profile selection or texture ownership differs")
    # CProfile embeds its info CStruct at +0x70; it is not a pointer field.
    host.remote_write(api, process, record + 72, bytes(4))
    host.require(call("GetChecksum", (0xa1dc81f9, record + 72, 0), this=profile + 0x70) & 0xff and word(record + 72) == CUSTOM, "Original profile did not copy the selected source Name")
    before = [call("GetStat", (key,), this=profile) for key in STATS]
    host.require(before == [3] * 10, "Original custom_classic source statistics differ")
    for key in STATS:
        host.require(call("SetStat", (key, 10), this=profile) & 0xff, "Original profile rejected a maximum statistic")
    after = [call("GetStat", (key,), this=profile) for key in STATS]
    host.require(after == [10] * 10, "Original profile did not retain ten maximum statistics")
    host.require(call("SetStat", (STATS[2], 11), this=profile) & 0xff == 0 and call("GetStat", (STATS[2],), this=profile) == 10, "Original profile did not reject an out-of-range statistic")
    host.require(all(word(address) == 0 for address in (0x6dcf84, 0x6dcf8c, 0x6dcf90)), "Profile proof changed active VM state")
    log("profile_pass", profile_manager=profile_manager, profile=profile, source_index=matches[0][0], source_checksum=CUSTOM, native_appearance_vtable=vtable, native_info_heap=heap, native_info_heap_key=INFO_HEAP, stat_names=STAT_NAMES, stats_before=before, stats_after=after, rejected_stat_11=True, source_files=len(source_buffers), source_bytes=sum(size for _, size in source_buffers), selected_after_game_construction=existing_manager is not None, retained_until_child_exit=True, native_destructors_tested=False, backend_ready=False, scope="original ProfileManager/factory/source profile selection and ten native 10/10 statistics; no CSkater or gameplay")
    return profile


def self_test():
    directory = Path(__file__).resolve().parent
    rows = [{"canonical_name": name, "bytes": size, "sha256": digest, "path": str(directory / f"{index}.qb")} for index, (name, (size, digest)) in enumerate(INPUTS.items())]
    host.require(len(source_rows(rows, directory)) == 3, "Valid metadata selection failed")
    invalid_rows = [rows[:-1], rows + [rows[0]], [rows[0], rows[0], rows[2]]]
    for field, value in (("bytes", 1), ("sha256", "0" * 64), ("path", str(directory.parent / "outside.qb")), ("path", "relative.qb"), ("path", rows[1]["path"])):
        invalid_rows.append([{**rows[0], field: value}, *rows[1:]])
    for invalid in invalid_rows:
        try:
            source_rows(invalid, directory)
        except RuntimeError:
            continue
        raise RuntimeError("Profile input validator accepted malformed metadata")
    host.require(len(STATS) == len(set(STATS)) == len(STAT_NAMES) == 10 and sum(size for size, _ in INPUTS.values()) == 372576, "Profile proof constants differ")
    print("PASS: profile source selection and eight malformed input controls; no child created")


if __name__ == "__main__":
    host.require(sys.argv[1:] == ["--self-test"], "Use probe_loader.py --profile for the bounded native test")
    self_test()
