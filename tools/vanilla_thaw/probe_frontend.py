"""Original FrontEnd heap, clip pool and ScreenElementManager prerequisites.

Run after genuine Game/Events and MemMan/VM bootstrap on the retained worker.
No scheduler, rendering, hardware polling or replacement UI callback is run.
The separate protected Frontend base constructor is not invoked by this module.
"""
from __future__ import annotations

import hashlib
import struct

import probe_loader as host

PINS = {
    "ClipPoolInit": ("ClipPoolInit", 0x5274d0, 154, "b4e73a5b04c68e84a41f88218be901c18ca11cd4d73ef7c19df55d4795329ba7"),
    "ScreenFactory": ("ScreenFactory", 0x4fb500, 77, "215db8049790c44aa37993601a20e14a52bff74415d01b8526c8bb4f21395290"),
    "ScreenRootInit": ("ScreenRootInit", 0x4f8e70, 138, "bb98eb8fbf92b165fcf5aeb12ce8c28af285c92aab354f72d52c578c6e3847e7"),
    "EventLookup": ("EventLookup", 0x433ae0, 41, "532a8e4cae13c3f2666af97b70cee472867970dcf3c8a72ab9a1cf6ea3dd1a30"),
}
ROOT_ID, ROOT_TYPE = 0x56a1eae3, 0xa13adbb7


def pane_chain(words, first):
    result = []
    while first:
        host.require(len(result) < 10 and first not in result, "Original clip pool is cyclic or exceeds ten panes")
        row = words(first, 6)
        host.require(row[0] == 0x6955a4 and row[1:5] == (0, 0, 640, 480), "Original free clip-pane layout differs")
        result.append(first)
        first = row[5]
    return result


def validate_root(words, manager, root, owned_panes, free_panes):
    m, r = words(manager, 0x6ac // 4), words(root, 0x188 // 4)
    head, node = manager + 4, root + 0x38
    host.require(m[0] == 0x6944bc and r[0] == 0x6946b4 and r[0x18 // 4] == ROOT_ID and
                 r[0x20 // 4] == ROOT_TYPE and r[0xc // 4] == manager and
                 r[0x44 // 4] == head and r[0x48 // 4] == head and m[4] == node and m[5] == node and
                 r[0x60 // 4] == 0 and r[0x6c // 4] == 0,
                 "Original RootWindow identity, owner or native list links differ")
    pane = r[0x184 // 4]
    host.require(pane in owned_panes and words(pane, 6)[1:5] == (0, 0, 640, 448) and
                 len(owned_panes) == 10 and len(set(owned_panes)) == 10 and
                 len(free_panes) == 9 and len(set(free_panes)) == 9 and
                 set(free_panes) == set(owned_panes) - {pane},
                 "Original RootWindow did not consume exactly one native clip pane")
    return pane


def run_screen(api, process, record, deadline, log):
    def read(address, count):
        return host.remote_read(api, process, address, count)

    def word(address):
        return struct.unpack("<I", read(address, 4))[0]

    arena_start, arena_end = word(0x84376c), word(0x843770)

    def words(address, count):
        host.require(address % 4 == 0 and arena_start <= address <= arena_end - count * 4,
                     "Original screen object escapes genuine allocator arena")
        return struct.unpack("<" + "I" * count, read(address, count * 4))

    def call(name, args=(), this=None):
        pin = PINS[name] if name in PINS else host.VM_PINS[name]
        return host.native_call(api, process, pin, list(args), record, deadline, log, this)

    def check_pins():
        for name, address, size, digest in PINS.values():
            host.require(hashlib.sha256(read(address, size)).hexdigest() == digest, "Screen entry differs: " + name)

    check_pins()
    manager, events = word(0x84388c), word(0x6dd6d0)
    host.require(manager == 0x842b18 and word(manager + 0x128) != 0 and
                 arena_end - arena_start == 256 * 1024 * 1024 and events != 0 and word(0x6dd6d4) == 1,
                 "Screen requires genuine MemMan, Script heap and Events")
    words(events, 0x20c // 4)
    host.require(all(word(address) == 0 for address in (0x76b818, 0x76b868, 0x76b86c)),
                 "Original screen manager or clip pool is already active")
    host.require(word(manager + 0x11c) == word(manager + 0x120) == 0 and read(0x844234, 1) == b"\0" and
                 read(0x6a22e0, 9) == b"FrontEnd\0", "Original ordinary FrontEnd heap is already owned or differs")
    count = word(manager + 0xc0)
    host.require(count < 0x30, "Native heap registry is full")
    arena = call("ArenaAllocate", (0x20,))
    words(arena, 8)
    host.require(call("OwnedArenaCtor", (0x100000,), arena) == arena, "Original FrontEnd arena construction failed")
    heap = call("CreateHeap", (arena, 1, 0x6a22e0), manager)
    words(heap, 1)
    host.require(word(manager + 0xc0) == count + 1 and word(manager + count * 4) == heap,
                 "Original FrontEnd heap was not registered by native factory")
    # Match original startup publication after genuine arena/heap construction.
    host.remote_write(api, process, manager + 0x11c, struct.pack("<2I", arena, heap))
    host.require(call("EventLookup", (ROOT_ID,), events) == 0, "RootWindow already exists in original event index")
    call("ClipPoolInit")
    owned = pane_chain(words, word(0x76b818))
    host.require(len(owned) == 10, "Original clip initializer did not create ten panes")
    screen = call("ScreenFactory", (1,))
    fields = words(screen, 0x6ac // 4)
    host.require(word(0x76b868) == screen and word(0x76b86c) == 1 and
                 fields[0] == 0x6944bc and fields[4] == fields[5] == screen + 4,
                 "Original screen factory did not publish its empty native manager")
    host.require(call("ScreenRootInit") & 0xff == 1, "Original screen root initialization failed")
    root = call("EventLookup", (ROOT_ID,), events)
    free = pane_chain(words, word(0x76b818))
    pane = validate_root(words, screen, root, owned, free)
    host.require(word(0x6dd6d0) == events and word(0x76b868) == screen and word(0x76b86c) == 1,
                 "Original screen/context singleton publication changed")
    check_pins()
    log("frontend_screen_pass", manager=screen, root=root, root_id=ROOT_ID, root_type=ROOT_TYPE,
        frontend_heap=heap, frontend_capacity=0x100000, native_panes=10, free_panes=9,
        root_pane=pane, root_bounds=[0, 0, 640, 448], original_factories=True,
        lifetime="child process owned", tasks_executed=False, frontend_base_initialized=False,
        hardware_input_initialized=False, gameplay_ready=False)
    return {"manager": screen, "root": root, "heap": heap}
