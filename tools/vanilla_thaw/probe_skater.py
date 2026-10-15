"""Whole original local CSkater and ProfileInit, retained by the owned child.

No replacement actor, component, animation tree or model is constructed here.
This stops after initialization and inspection; it does not tick gameplay.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import struct

import import_qb
import import_resources
import probe_loader as host
import probe_profile as profile
import probe_resources as resources

METADATA_SHA = "1359f062d1f7e2b4afa01aa982be963c42dbbce1198e5327fa5ebb4ad9f786e6"


def read_metadata():
    raw = Path(__file__).with_name("skater_bootstrap.json").read_bytes()
    host.require(hashlib.sha256(raw).hexdigest() == METADATA_SHA, "Full skater metadata differs")
    value = json.loads(raw)
    host.require(value["format"] == "stock-full-skater-proof-pins-v1" and value["stock_exe_sha256"] == host.FILES["THAW.exe"][1], "Full skater image metadata differs")
    return value


def verify_common_resources(resource_manifest, image_manifest):
    """Recheck the coherent private copy before any native file access."""
    manifest = json.loads(resource_manifest.read_text(encoding="utf-8-sig"))
    image = json.loads(image_manifest.read_text(encoding="utf-8-sig"))
    original = Path(image["source"]).resolve()
    directory = resource_manifest.resolve().parent
    host.require(manifest.get("format") == import_resources.FORMAT and
                 manifest.get("sources_revalidated_before_publish") is True and
                 Path(manifest["source"]).resolve() == original and
                 Path(manifest["directory"]).resolve() == directory and not directory.is_relative_to(original),
                 "Full skater requires the coherent private original resource cache")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='thaw-resources') as pool:
        expected, copied = pool.map(import_resources.verified_files, (original, directory))
    names = [row["path"] for row in expected]
    host.require(names == [row["path"] for row in copied] and
                 names == [row["path"] for row in manifest["files"]], "Common resource inventory changed or is incomplete")
    host.require(expected == manifest["files"] == copied, "Common resource bytes differ from the selected original game")
    return {"directory": directory, "files": len(names), "bytes": sum(row["bytes"] for row in expected)}


def viewport_aspect(system, *, display_window=None, display_pid=None):
    """Use the real presentation window without replacing the hidden D3D window."""
    window = system.window if display_window is None else display_window
    if display_window is not None:
        host.require(type(window) is int and window > 0 and type(display_pid) is int and display_pid > 0,
                     "External viewport needs a verified process and window")
        owner = system.allocate(bytes(4))
        host.require(system.call("user32.dll", "GetWindowThreadProcessId", (window, owner)) != 0
                     and system.word(owner) == display_pid
                     and system.call("user32.dll", "IsWindowVisible", (window,)) != 0,
                     "Viewport window no longer belongs to the visible target game")
    else:
        host.require(display_pid is None, "Viewport process requires an external window")
    rectangle = system.allocate(bytes(16))
    host.require(system.call("user32.dll", "GetClientRect", (window, rectangle)) != 0,
                 "Cannot read the viewport client dimensions")
    left, top, right, bottom = struct.unpack("<4i", system.read(rectangle, 16))
    host.require(right > left and bottom > top, "Viewport has invalid client dimensions")
    aspect = (right - left) / (bottom - top)
    host.require(0.1 <= aspect <= 10, "Viewport aspect exceeds original scope")
    return window, aspect


def viewports(api, process, record, deadline, log, system, *, display_window=None, display_pid=None):
    metadata = read_metadata()
    pins = {row[0]: row for row in metadata["functions"]}
    read, word = system.read, system.word
    host.require(not any(read(0x76b6d0, 40)), "Original viewports already own objects")
    window, aspect = viewport_aspect(system, display_window=display_window, display_pid=display_pid)
    for name, args in (("ViewportAspectSet", [struct.unpack("<I", struct.pack("<f", aspect))[0]]),
                       ("ViewportInitializeAllTen", []), ("ViewportModeSet", [0])):
        host.native_call(api, process, pins[name], args, record, deadline, log)
    pointers = struct.unpack("<10I", read(0x76b6d0, 40))
    start, end = word(0x84376c), word(0x843770)
    host.require(len(set(pointers)) == 10 and all(start <= pointer <= end - 36 for pointer in pointers), "Original viewport initializer did not create ten distinct native objects")
    for pointer in pointers:
        values = struct.unpack("<9I", read(pointer, 36))
        host.require(values[0] % 4 == 0 and 0x68e000 <= values[0] < 0x6bc000 and values[5:] == (0, 0, 0, 0), "Original viewport class/observer state differs")
    host.require(word(0x76b6f8) == 0 and word(0x76b704) == 1 and word(0x6c0ee0) == 0x694128, "Original single-screen viewport mapping differs")
    log("skater_viewports", native_objects=10, original_single_camera_mode=0, aspect=aspect, camera_bindings=0,
        window=window, hidden_resource_window=system.window, display_pid=display_pid)
    return pointers


def run(api, process, record, deadline, log, system, context):
    metadata = read_metadata()
    pins = {row[0]: row for row in metadata["functions"]}
    resource_pins = {row[0]: row for row in resources.read_metadata()["functions"]}
    read, word = system.read, system.word

    def call(name, arguments=(), this=None):
        pin = pins.get(name) or profile.PINS.get(name) or host.VM_PINS.get(name) or resource_pins.get(name) or host.QB_PINS[name]
        return host.native_call(api, process, pin, list(arguments), record, deadline, log, this)

    start, end = word(0x84376c), word(0x843770)

    def words(pointer, count):
        host.require(pointer % 4 == 0 and start <= pointer and pointer + count * 4 <= end, "Original skater object escapes its native arena")
        return struct.unpack("<" + "I" * count, read(pointer, count * 4))

    def original_class(pointer):
        value = words(pointer, 1)[0]
        host.require(value % 4 == 0 and 0x68e000 <= value < 0x6bc000, "Skater dependency has no original read-only class table")
        return value

    for name, address, size, digest in metadata["functions"]:
        host.require(hashlib.sha256(read(address, size)).hexdigest() == digest, "Original skater entry differs: " + name)
    host.require(all(word(a) == 0 for a in (0x6dcf84, 0x6dcf8c, 0x6dcf90)), "Whole CSkater requires an idle original script registry")
    manager = word(0x6dd6b0)
    host.require(manager != 0 and word(0x83e81c) == context["game"] and
                 word(word(0x83e894) + 0x14) == context["profile"] and
                 word(0x767410) != 0 and word(0x76b868) != 0 and
                 word(0x7689e8) != 0 and word(0x7689e8) == word(0x83e468),
                 "Whole CSkater requires actual Game/profile/animation/frontend prerequisites")
    host.require(call("ObjectLookup", (0x967c138c,), manager) == 0, "Player camera already exists before original ProfileInit")
    native_heap_manager = 0x842b18
    host.require(word(native_heap_manager + 0xc0) <= 0x2e, "Original heap registry lacks room for both skater heaps")
    heaps = []
    for label, capacity in (("skaterheap0", 0x24000), ("skatergeomheap0", 0x118000)):
        name = system.allocate(label.encode("ascii") + b"\0")
        key = call("crc", (name,))
        host.require(key == import_qb.qbkey(label) and call("NamedHeapLookup", (key, 0), native_heap_manager) == 0, "Original skater heap is already owned or has another name")
        heap = call("NamedHeapCreate", (key, capacity, name, 0), native_heap_manager)
        words(heap, 1)
        host.require(heap >= start and call("NamedHeapLookup", (key, 0), native_heap_manager) == heap and call("HeapFreeBytes", this=heap) == capacity, "Original skater heap factory/capacity differs")
        heaps.append(heap)
    career = word(context["game"] + 0x20)
    for key in profile.STATS:
        host.require(call("career_set_stat", (key, 10), career) & 0xff, "Original Career rejected the maximum statistic")
    depth, current = word(native_heap_manager + 0x10c), word(native_heap_manager + 0xc8)
    host.require(0 < depth < 16 and current == native_heap_manager + 0xcc + (depth - 1) * 4, "Native heap context stack is inconsistent")
    call("HeapContextPush", (heaps[0],), native_heap_manager)
    actor = call("ArenaAllocate", (0x35c,))
    host.require(actor % 16 == 0 and start <= actor <= end - 0x35c and not any(read(actor, 0x35c)), "Original operator new did not provide its zeroed CSkater storage")
    log("skater_before_whole_constructor", actor=actor, profile=context["profile"], native_bytes=0x35c, replacement_actor=False)
    returned = call("skater_ctor", (0, manager, 1, 0, 0, 1), actor)
    host.require(returned == actor, "Whole original CSkater constructor returned another object")
    call("skater_original_profile_init", (context["profile"],), actor)
    call("async_file_wait_all", (1,))
    call("HeapContextPop", this=native_heap_manager)
    host.require(word(native_heap_manager + 0x10c) == depth and word(native_heap_manager + 0xc8) == current, "Original ProfileInit unbalanced its heap stack")
    owner = words(actor, 0x35c // 4)
    host.require(owner[0] == 0x6989e8 and owner[3] == manager and owner[0x2c8 // 4:0x2f0 // 4] == (0x41200000,) * 10, "Original skater identity or ten initialized statistics differ")
    components, seen = {}, set()
    pointer = owner[0x11c // 4]
    while pointer:
        host.require(pointer not in seen and len(seen) < 64, "Original component chain cycles or exceeds bounds")
        seen.add(pointer)
        node = words(pointer, 6)
        original_class(pointer)
        host.require(node[1] not in components and node[3] == actor and call("composite_get_component", (node[1],), actor) == pointer, "Original component owner/type/lookup differs")
        components[node[1]] = pointer
        pointer = node[4]
    host.require(len(components) >= 33, "Full original actor has an incomplete component chain")
    for key, offset in ((0x8536dba5, 0x284), (0x286a8d26, 0x288), (0x8a897dd2, 0x28c), (0xd262802f, 0x2b0), (0x5bd63b29, 0x2b8)):
        host.require(key in components and owner[offset // 4] == components[key], "ProfileInit did not bind a required native component")
    skeleton_component = components[0x222756d5]
    skeleton = words(word(skeleton_component + 0x18), 7)
    host.require(skeleton[5] == 52 and skeleton[1] % 16 == 0, "Original skater did not retain its genuine 52-bone skeleton")
    words(skeleton[1], 52 * 16)
    model = word(components[0x286a8d26] + 0x208)
    model_words = words(model, 0x4c // 4)
    original_class(model)
    count = model_words[0x44 // 4] >> 16
    physical, asset = model_words[0x48 // 4] & 0xffff, model_words[0x48 // 4] >> 16
    host.require(0 < count <= 32 and physical + asset == count and model_words[5] == count, "Whole native ModelBuilder produced no valid original geometry parts")
    root = model_words[4]
    sentinel = words(root, 3)
    pointer, previous, parts = sentinel[1], root, []
    seen = set()
    while pointer != root:
        host.require(pointer not in seen and len(parts) < count, "Original model list cycles or exceeds its declared count")
        seen.add(pointer)
        node = words(pointer, 3)
        host.require(node[2] == previous, "Original model list backward link differs")
        entry = words(node[0], 6)
        geom = words(entry[0], 0x68 // 4)
        original_class(entry[0])
        instance, resource = words(geom[0x50 // 4], 0x7c // 4), words(geom[0x54 // 4], 0xc4 // 4)
        original_class(geom[0x54 // 4])
        host.require(instance[0x68 // 4] == model and entry[1] == resource[1] and entry[2] and
                     ((entry[3] == geom[0x54 // 4] and entry[4] == 0) or (entry[3] == 0 and entry[4] != 0)), "Native model part ownership/resource differs")
        scene = words(resource[0xc0 // 4], 0x9c // 4)
        original_class(resource[0xc0 // 4])
        decoded = words(scene[0x94 // 4], 0xa8 // 4)
        host.require(scene[3] != 0 and decoded[0] == 0xac0001 and 0 < decoded[0x80 // 4] <= 10000 and
                     0 < decoded[3] <= 10000 and decoded[0x6c // 4] != 0, "Native appearance part lacks original decoded geometry/texture dictionary")
        parts.append({"part_checksum": entry[2], "resource_checksum": entry[1], "sectors": decoded[0x80 // 4], "meshes": decoded[3]})
        previous, pointer = pointer, node[1]
    host.require(len(parts) == count and sentinel[2] == previous, "Original model part list/count differs")
    camera = call("ObjectLookup", (0x967c138c,), manager)
    words(camera, 0x124 // 4)
    camera_component = call("composite_get_component", (0xc4e311fa,), camera)
    camera_worker = words(camera_component, 7)[6]
    original_class(camera_worker)
    viewport = word(0x76b6d0)
    host.require(words(viewport, 9)[6] == camera_worker, "Whole ProfileInit did not bind its original player camera to the viewport")
    host.require([call("GetStat", (key,), context["profile"]) for key in profile.STATS] == [10] * 10, "ProfileInit changed the selected maximum profile statistics")
    log("skater_pass", actor=actor, camera=camera, profile=context["profile"], components=len(components),
        component_checksums=list(components), skeleton_bones=52, native_model=model, model_parts=parts,
        stats=[10] * 10, whole_original_constructor=True, whole_original_profile_init=True,
        original_model_builder=True, native_camera_bound=True, original_source_files_modified=False,
        gameplay_updates=0, task_callbacks_executed=0, active_original_scripts=word(0x6dcf90),
        lifetime="all actor/resource/source objects retained until child termination", backend_ready=False)
    return {"actor": actor, "camera": camera, "model": model, "components": components}
