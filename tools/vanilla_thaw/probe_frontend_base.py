"""Bounded validation of one protected stock Frontend base entry candidate.

Only the existing private suspended-image child/retained worker may call run.
The six-byte entry is a candidate until its real object, task registration,
source-owned preferences and predicate pass. There is no address scan/fallback.
The whole stock image and the loader's job/deadline/fault handling stay in force.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys

import probe_loader as host

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assets_export import qbkey

PINS = {
    "construct": ("FrontendBaseConstructorCandidate", 0x4a69b0, 6, "82a2f3325af0b3e1eae899f9bfcb1ec2731782916c07efdaecfe4405a3dfc345"),
    "predicate": ("FrontendAnimationPredicate", 0x4a4b40, 58, "668d003f7c509ab30321e58772e0d4c8faa9d45e3b0c411f0e65bd8fa653666f"),
}
# Independent corroboration, not a claim to have decoded the protected body:
# matching full original base destructor stores 0x6927D8; its ctor's C++ EH
# handler/function-info/unwind table retain the original sixteen-state shape.
CORROBORATION = (
    (0x4a4980, 352, "f194bc60e8b5a39952e4e577df25667ec579241f143e539a3d205400eb761f46"),
    (0x67f713, 10, "6fa9279ed68ca0aacac71936efedf266e99427932cde08045193b1d0c4b8e328"),
    (0x6b0ea4, 28, "1bb36227d1335b2d4d10c8bf893e32676691b1a3e64caa8dfeac0311d6b4188e"),
    (0x6b0e24, 128, "eb0d5f5382a95fffc736b2044e38db814c82ed6852b4241927d850e2a0648396"),
    (0x6927d8, 28, "d11a6129710510e3b18acca9b6a1080a08af1b860a5838f020cae0707eb8440b"),
)
SOURCE_NAME = "scripts/game/net/netoptions.qb.wpc"
SOURCE_SHA = "637a9c1d7e200ff0c91177ed5062dfad7f2ccc9b0bb33345569006b584eab525"
SOURCE_BYTES = 20028
PREFERENCES_ID = 0x7d1c1c0f
TASK_OFFSETS = tuple(range(8, 49, 4))


def read_inputs(qb_manifest, image_manifest):
    host.require(qb_manifest.is_absolute() and image_manifest.is_absolute(), "Base manifests must be absolute")
    source = json.loads(qb_manifest.read_text(encoding="utf-8-sig"))
    image = json.loads(image_manifest.read_text(encoding="utf-8-sig"))
    host.require(source.get("format") == "stock-thaw-core-qb-cache-v1" and
                 source.get("sources_revalidated_before_publish") is True and
                 Path(source["source_directory"]).resolve() == Path(image["source"]).resolve(),
                 "Base source cache differs from selected original image")
    rows = [row for row in source["members"] if row.get("order") == 94]
    host.require(len(rows) == 1 and rows[0]["canonical_name"] == SOURCE_NAME and
                 rows[0]["cache_file"] == "members/0094.qb" and rows[0]["bytes"] == SOURCE_BYTES and
                 rows[0]["sha256"] == SOURCE_SHA, "Whole stock network preferences source pin differs")
    directory = qb_manifest.resolve().parent
    path = (directory / rows[0]["cache_file"]).resolve()
    host.require(path.is_relative_to(directory), "Base source escapes private QB cache")
    raw = path.read_bytes()
    host.require(len(raw) == SOURCE_BYTES and hashlib.sha256(raw).hexdigest() == SOURCE_SHA,
                 "Whole preferences source bytes differ")
    return raw


def task_queues(words, manager):
    fields, queues = words(manager, 0xcc // 4), []
    for offset in (4, 0x40, 0x7c):
        barrier = fields[(offset + 0x34) // 4]
        root = barrier + 4
        sentinel = words(root, 5)
        link, previous, links = sentinel[3], root, []
        while link != root:
            host.require(len(links) < 64 and link not in [item[0] for item in links], "Native task list is cyclic or exceeds scope")
            node = words(link, 5)
            host.require(node[4] == previous, "Native task back-link differs")
            links.append((link, *node))
            previous, link = link, node[3]
        host.require(sentinel[4] == previous, "Native task sentinel back-link differs")
        queues.append({"barrier": barrier, "root": root, "links": links})
    return queues


def validate_base(raw, address, source_head, words, barrier):
    host.require(len(raw) == 0xfe24, "Base allocation extent differs")
    fields = struct.unpack("<" + "I" * (len(raw) // 4), raw)
    host.require(fields[0] == 0x6927d8 and all(fields[offset // 4] == 0 for offset in (0x8188, 0x818c, 0x8190)),
                 "Candidate did not construct original Frontend base identity/predicate state")
    copied = fields[0x48 // 4]
    host.require(source_head != 0 and copied != 0 and source_head != copied, "Original base did not deep-copy source preferences")
    words(copied, 4)
    tasks, callbacks = [], set()
    for offset in TASK_OFFSETS:
        task = fields[offset // 4]
        row = words(task, 8)
        host.require(task not in [item["address"] for item in tasks] and row[0] == 0x69274c and
                     row[7] == address and row[3] == (barrier if offset == 8 else 0) and
                     0x401000 <= row[6] < 0x68e000 and row[6] not in callbacks,
                     "Original base task type, callback, registration or ownership differs")
        node = words(row[2], 5)
        host.require(node[1] == task, "Native task node does not refer to its owner")
        callbacks.add(row[6])
        tasks.append({"offset": offset, "address": task, "node": row[2], "callback": row[6], "registered": offset == 8})
    return copied, tasks


def run(api, process, record, deadline, log, source):
    host.require(len(source) == SOURCE_BYTES and hashlib.sha256(source).hexdigest() == SOURCE_SHA,
                 "Base preferences input changed after validation")

    def read(address, size):
        return host.remote_read(api, process, address, size)

    def word(address):
        return struct.unpack("<I", read(address, 4))[0]

    arena_start, arena_end = word(0x84376c), word(0x843770)

    def words(address, count):
        host.require(address % 4 == 0 and arena_start <= address <= arena_end - count * 4,
                     "Base dependency escapes original allocator arena")
        return struct.unpack("<" + "I" * count, read(address, count * 4))

    def call(name, args=(), this=None):
        pin = PINS[name] if name in PINS else host.QB_PINS[name]
        return host.native_call(api, process, pin, list(args), record, deadline, log, this)

    def check_pins():
        for _, address, size, digest in PINS.values():
            host.require(hashlib.sha256(read(address, size)).hexdigest() == digest, "Base candidate entry differs")
        for address, size, digest in CORROBORATION:
            host.require(hashlib.sha256(read(address, size)).hexdigest() == digest, "Base independent EH/vtable evidence differs")

    check_pins()
    task_manager = word(0x6dd8d8)
    host.require(word(0x84388c) == 0x842b18 and word(0x842b18 + 0x128) != 0 and task_manager != 0 and
                 word(0x7689e8) == word(0x83e468) == 0 and arena_end - arena_start == 256 * 1024 * 1024,
                 "Base requires genuine MemMan, Script heap, TaskManager and untouched platform aliases")
    before = task_queues(words, task_manager)
    symbol = call("lookup", (PREFERENCES_ID,))
    host.require(0x10000 <= symbol < 0x100000000 - 28, "Whole stock preferences were not loaded by core bootstrap")
    kind, checksum, owner, preferences = struct.unpack("<4I", read(symbol, 16))
    # Source member94 has its whole CStruct section at3932 and body at3952.
    host.require((kind, checksum, owner, preferences) == (0xa0400, PREFERENCES_ID, qbkey(SOURCE_NAME), symbol + 20),
                 "Native preferences source type, ownership or inline source structure differs")
    source_head = word(preferences + 4)
    # Unmodified private input storage, not a replacement platform singleton.
    base = host.success(api.VirtualAllocEx(process, None, 0xfe24, 0x3000, 4), "Allocate owned Frontend base storage")
    host.require(base % 16 == 0 and 0x10000 <= base <= 0xffffffff - 0xfe24, "Base storage exceeds x86 space")
    host.remote_write(api, process, base, bytes(0xfe24))
    log("frontend_base_candidate", entry=0x4a69b0, verified=False, corroborating_eh_states=16,
        storage=base, source_sha256=SOURCE_SHA, source_preferences=preferences)
    host.require(call("construct", this=base) == base, "Candidate constructor returned another object")
    copied, tasks = validate_base(read(base, 0xfe24), base, source_head, words, before[0]["barrier"])
    after = task_queues(words, task_manager)
    host.require([(row["barrier"], row["root"]) for row in after] ==
                 [(row["barrier"], row["root"]) for row in before], "Original task-list owners changed")
    old_links = {row[0] for queue in before for row in queue["links"]}
    new_links = {row[0] for queue in after for row in queue["links"]}
    host.require(old_links <= new_links and len(new_links - old_links) == 1 and
                 new_links - old_links == {tasks[0]["node"]},
                 "Original base did not add exactly its source task to existing native queues")
    host.require(call("predicate", this=base) & 0xff == 0, "Original base animation predicate is unexpectedly active")
    host.require(word(preferences + 4) == source_head, "Original preferences source was modified")
    check_pins()
    # The existing retained-skater adapter borrows this genuine base for the
    # platform/predicate consumers; this does not construct the platform subclass.
    host.remote_write(api, process, 0x7689e8, struct.pack("<I", base))
    host.remote_write(api, process, 0x83e468, struct.pack("<I", base))
    log("frontend_base_pass", entry=0x4a69b0, native_entry_validated=True, base=base, vtable=0x6927d8,
        tasks=tasks, registered_tasks_added=1, native_preferences_copy=copied,
        original_source_head=source_head, animation_predicate=False, borrowed_aliases=[0x7689e8, 0x83e468],
        platform_subclass_initialized=False, hardware_input_initialized=False,
        tasks_executed=False, lifetime="child process owned", gameplay_ready=False)
    return {"base": base, "tasks": tasks, "preferences_copy": copied}
