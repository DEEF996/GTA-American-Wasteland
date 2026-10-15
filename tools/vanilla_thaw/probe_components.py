"""One bounded original descriptor, TaskManager and ComponentManager bootstrap.

Metadata contains addresses/hashes only. Original functions own all objects and
registration; no task callback, component factory or gameplay update is invoked.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys

import probe_loader as host

METADATA_SHA = "c9ea0b674b224ea4e3cb9ea5fd8ac4ab2463d40aba4e10a2b900143c6c554515"


def read_metadata():
    data = Path(__file__).with_name("component_bootstrap.json").read_bytes()
    host.require(hashlib.sha256(data).hexdigest() == METADATA_SHA, "Component proof metadata differs")
    value = json.loads(data)
    host.require(value["format"] == "stock-component-proof-pins-v1" and value["stock_exe_sha256"] == host.FILES["THAW.exe"][1], "Component proof image differs")
    host.require(len(value["initializers"]) == 60 and len(value["types"]) == 93 and len(value["tables"]) == 69 and len(value["descriptors"]) == 842 and len(value["written_addresses"]) == 3608, "Component proof extents differ")
    host.require(len({row[0] for row in value["initializers"]}) == 60 and len({row[1] for row in value["descriptors"]}) == 842, "Component proof has duplicate entries")
    return value


def verify_descriptor_changes(before, after, start, writable):
    host.require(len(before) == len(after), "Descriptor snapshot extent differs")
    changed = 0
    for index, (old, new) in enumerate(zip(before, after)):
        if old != new:
            host.require((start + index) & ~3 in writable, "Initializer changed bytes outside audited descriptor words")
            changed += 1
    host.require(changed != 0, "Original initializers did not populate descriptor callbacks")
    return changed


def run(api, process, record, deadline, log, metadata):
    def read(address, size):
        return host.remote_read(api, process, address, size)

    def word(address):
        return struct.unpack("<I", read(address, 4))[0]

    pins = {row[0]: row for row in metadata["functions"]}

    def call(name, arguments=(), this=None):
        return host.native_call(api, process, pins[name], list(arguments), record, deadline, log, this)

    def verify_region(address, size, digest):
        data = read(address, size)
        host.require(hashlib.sha256(data).hexdigest() == digest, f"Pinned component region differs at {address:#x}")
        return data

    arena_start, arena_end = word(0x84376c), word(0x843770)

    def inside(pointer, size):
        return pointer % 4 == 0 and arena_start <= pointer and pointer + size <= arena_end

    buckets = word(0x6dd0fc)
    host.require(inside(buckets, 0x4000), "Original symbol buckets are outside their arena")

    def symbols(expected_count):
        result, seen = {}, set()
        for pointer in struct.unpack("<4096I", read(buckets, 0x4000)):
            while pointer:
                host.require(inside(pointer, 20) and pointer not in seen and len(result) < expected_count, "Original symbol graph is invalid or cyclic")
                seen.add(pointer)
                values = struct.unpack("<5I", read(pointer, 20))
                host.require(values[1] not in result, "Original symbol graph contains duplicate names")
                result[values[1]] = (pointer, values)
                pointer = values[4]
        host.require(len(result) == expected_count, "Original symbol graph count differs")
        return result

    host.require(word(0x84388c) == 0x842b18 and word(0x6dd3f4) != 0, "Original allocator/ScriptContext bootstrap is absent")
    zero_globals = (0x6dd8d8, 0x6dd8dc, 0x6dd6b0, 0x6dd6b4, 0x6dd6b8, 0x6e80d4,
                    0x6dd104, 0x6dd10c, 0x6dd110, 0x6dcf84, 0x6dcf8c, 0x6dcf90)
    host.require(all(word(address) == 0 for address in zero_globals), "Original component/task state must initially be inactive")
    host.require(not any(read(0x6dd4b0, 512)), "Original component chains must be empty")
    baseline = symbols(1450)
    descriptors = dict(metadata["descriptors"])
    host.require(not (set(descriptors.values()) & set(baseline)), "Original descriptors collide with existing native names")
    for address, size, digest in metadata["tables"]:
        verify_region(address, size, digest)
    crt = verify_region(*metadata["crt_table"])
    for va, size, digest, index in metadata["initializers"]:
        host.require(struct.unpack_from("<I", crt, index * 4)[0] == va, "Selected descriptor initializer is absent from the original CRT list")
        verify_region(va, size, digest)
    for _, address, size, digest in pins.values():
        verify_region(address, size, digest)
    writable = set(metadata["written_addresses"])
    start, end = min(writable), max(writable) + 4
    original = read(start, end - start)
    for index, (va, size, digest, _) in enumerate(metadata["initializers"]):
        host.native_call(api, process, (f"DescriptorInitializer{index:02d}", va, size, digest), [], record, deadline, log)
    initialized = read(start, end - start)
    changed = verify_descriptor_changes(original, initialized, start, writable)
    host.require(all(word(address) == checksum for address, checksum in descriptors.items()), "Original initialization changed descriptor name checksums")
    log("component_descriptors_initialized", original_initializers=60, original_crt_list_executed=False, checked_span_bytes=len(original), allowed_word_destinations=len(writable), changed_bytes=changed, initialized_sha256=hashlib.sha256(initialized).hexdigest())

    tasks = call("TaskManagerFactory", (1,))
    host.require(inside(tasks, 0xcc) and word(0x6dd8d8) == tasks and word(0x6dd8dc) == 1, "Original TaskManager factory did not publish its singleton")
    barriers = {offset: word(tasks + offset + 0x34) for offset in (4, 0x40, 0x7c)}
    host.require(len(set(barriers.values())) == 3 and all(inside(pointer, 0x24) and 0x68e000 <= word(pointer + 4) < 0x6bc000 for pointer in barriers.values()), "Original three task-list barriers differ")
    manager = call("ComponentManagerFactory", (1,))
    host.require(inside(manager, 0x448) and word(0x6dd6b0) == manager and word(0x6dd6b4) == 1 and word(manager + 0x44) == 0, "Original ComponentManager factory state differs")
    symbols(1502)  # The original constructor publishes its 52 base descriptors.
    call("ComponentTypeBootstrap", this=manager)
    host.require(word(manager + 0x44) == 93, "Original ComponentManager did not register all 93 types")
    for index, expected in enumerate(metadata["types"]):
        host.require(struct.unpack("<2I", read(manager + 0x48 + index * 8, 8)) == tuple(expected), "Original ordered component type/factory pair differs")
    complete = symbols(2292)
    for checksum, previous in baseline.items():
        host.require(complete.get(checksum) == previous, "Component bootstrap changed an existing native symbol")
    for address, checksum in descriptors.items():
        _, (flags, key, owner, payload, _) = complete[checksum]
        host.require(flags >> 16 & 0xff == 9 and key == checksum and owner == 0 and payload == address, "Original member descriptor publication differs")
    host.require(word(0x6e6620) == word(0x690650) == 0x42820000, "Original render-model descriptor callback did not copy its startup constant")
    auxiliary = word(0x6e80d4)
    host.require(inside(auxiliary, 4), "Original Suspend holder was not created")
    expected_tasks = [(word(manager + 0x40), manager, 4, -900),
                      (word(manager + 0x3c), manager, 0x7c, 1000),
                      (word(auxiliary), auxiliary, 4, -1100)]
    task_report = []
    for task, owner, list_offset, priority in expected_tasks:
        host.require(inside(task, 0x20), "Original registered task lies outside its arena")
        node, callback = word(task + 8), word(task + 0x18)
        host.require(inside(node, 0x14) and word(task + 0xc) == barriers[list_offset] and word(task + 0x1c) == owner and word(node + 4) == task and word(node + 8) == priority & 0xffffffff, "Original task ownership, list or priority differs")
        host.require(0x400000 <= callback < 0x68e000, "Original task callback lies outside stock code")
        task_report.append(dict(task=task, owner=owner, callback=callback, list_offset=list_offset, priority=priority))
    host.require(not any(read(0x6dd4b0, 512)) and all(word(address) == 0 for address in (0x6dcf84, 0x6dcf8c, 0x6dcf90)), "Bootstrap unexpectedly created live components or scripts")
    log("components_pass", task_manager=tasks, component_manager=manager, original_initializers=60, original_types=93, original_descriptors=842, existing_native_symbols_preserved=1450, final_symbols=2292, tasks=task_report, task_callbacks_executed=0, component_factories_executed=0, owners_retained_until_child_exit=True, native_destructors_tested=False, backend_ready=False, scope="original task/component managers, complete type registration and member descriptors; no skater or task updates")


def self_test():
    read_metadata()
    host.require(verify_descriptor_changes(bytes(12), b"\0\0\0\0\1\0\0\0\0\0\0\0", 0x1000, {0x1004}) == 1, "Valid descriptor write control failed")
    for after in (bytes(12), bytes(11), b"\1" + bytes(11)):
        try:
            verify_descriptor_changes(bytes(12), after, 0x1000, {0x1004})
        except RuntimeError:
            continue
        raise RuntimeError("Descriptor validator accepted missing, truncated or out-of-range changes")
    print("PASS: exact component metadata and three invalid descriptor-write controls; no child created")


if __name__ == "__main__":
    host.require(sys.argv[1:] == ["--self-test"], "Use probe_loader.py --components for the bounded native test")
    self_test()
