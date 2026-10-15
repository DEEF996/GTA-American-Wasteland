"""Run the whole stock HUD source through its original retained Script API.

Inputs remain the complete private original QB member. No source tokens,
screen objects, script flags or native callbacks are supplied by this module.
"""
import hashlib
import json
from pathlib import Path
import struct

import probe_core
import probe_frontend
import probe_loader as host
import probe_native

SOURCE_NAME = "scripts/game/menu/panelstuff.qb.wpc"
SOURCE_BYTES = 13672
SOURCE_SHA = "22a85fbdc804e0c27344a939a1f81b5e7fc74dfdc1c9b20e47efcdfe0664de22"
SCRIPT_KEY = 0xd10c7f04
BODY_BYTES = 10277
BALANCE_ID = 0xa4db8a4b
TAG_TURNED_ON = 0xc22eb9a0
SPAWN = ("OriginalRetainedSpawnScript", 0x413380, 441, "99e895c6c399f12bc2af13fd1a0b586a464d4bee6a4e3d84dc1c6a356c5952cb")


def validate_source(source):
    host.require(len(source) == SOURCE_BYTES and hashlib.sha256(source).hexdigest() == SOURCE_SHA,
                 "Whole stock panelstuff source differs")
    host.require(struct.unpack_from("<I", source, 1040)[0] == SCRIPT_KEY and
                 struct.unpack_from("<2I", source, 1060) == (BODY_BYTES, 2941),
                 "Whole stock create_panel_stuff section/header differs")
    return source


def read_inputs(qb_manifest, image_manifest):
    value = json.loads(qb_manifest.read_text(encoding="utf-8-sig"))
    image = json.loads(image_manifest.read_text(encoding="utf-8-sig"))
    host.require(value.get("format") == "stock-thaw-core-qb-cache-v1" and
                 value.get("sources_revalidated_before_publish") is True and
                 Path(value["source_directory"]).resolve() == Path(image["source"]).resolve(),
                 "Original HUD source does not belong to the selected stock cache")
    rows = [row for row in value["members"] if row["order"] == 136]
    host.require(len(rows) == 1 and rows[0]["canonical_name"] == SOURCE_NAME and
                 rows[0]["bytes"] == SOURCE_BYTES and rows[0]["sha256"] == SOURCE_SHA,
                 "Stock HUD archive member differs")
    directory = qb_manifest.resolve().parent
    source = (directory / rows[0]["cache_file"]).resolve()
    host.require(source.is_relative_to(directory), "Stock HUD member escapes its private cache")
    return validate_source(source.read_bytes())


def run(api, process, record, deadline, log, source, preloaded):
    validate_source(source)
    pins = {**host.QB_PINS, **host.VM_PINS, **probe_native.PINS, **probe_frontend.PINS,
            "SpawnScript": SPAWN}

    def read(address, size):
        return host.remote_read(api, process, address, size)

    def word(address):
        return struct.unpack("<I", read(address, 4))[0]

    def call(name, args=(), this=None):
        return host.native_call(api, process, pins[name], list(args), record, deadline, log, this)

    start, end = word(0x84376c), word(0x843770)

    def words(address, count):
        host.require(address % 4 == 0 and start <= address and address + count * 4 <= end,
                     "Retained HUD Script leaves its original arena")
        return struct.unpack(f"<{count}I", read(address, count * 4))

    def registry():
        expected = word(0x6dcf90)
        host.require(expected <= 4096, "Original retained Script registry exceeds its bound")
        pointer, previous, result, identities = word(0x6dcf8c), 0, {}, set()
        while pointer:
            host.require(pointer not in result and len(result) < expected, "Original retained Script registry cycles")
            state = words(pointer, 63)
            host.require(state[2] == previous and state[0xb0 // 4] not in identities,
                         "Original retained Script links or IDs differ")
            result[pointer] = state
            identities.add(state[0xb0 // 4])
            previous, pointer = pointer, state[1]
        host.require(len(result) == expected, "Original retained Script count differs")
        return result

    source_pointer = probe_core.reuse(api, process, preloaded, SOURCE_NAME, source)
    symbol = call("lookup", (SCRIPT_KEY,))
    host.require(symbol == source_pointer + 1036 and
                 struct.unpack("<4I", read(symbol, 16)) ==
                 (0x70400, SCRIPT_KEY, probe_native.qbkey(SOURCE_NAME), source_pointer + 1056),
                 "Whole original HUD symbol/provenance differs")
    host.require(struct.unpack("<2I", read(source_pointer + 1060, 8)) == (BODY_BYTES, 2941),
                 "Whole original HUD compressed source header changed")
    events, screen = word(0x6dd6d0), word(0x76b868)
    host.require(events and screen and call("EventLookup", (BALANCE_ID,), events) == 0,
                 "Stock balance meter already exists or its original managers are absent")
    host.require(word(0x6dcf84) == 0 and read(0x6dcf98, 2) == bytes(2) and read(0x6dd4a4, 1) == b"\0",
                 "Whole authored HUD must start between original gameplay phases")
    before = registry()
    cache = word(0x6dcfac)
    cached = call("ScriptCacheLoad", (SCRIPT_KEY,), cache)
    host.require(start <= cached and cached + BODY_BYTES <= end,
                 "Whole original HUD cache body leaves its native arena")
    cached_sha = hashlib.sha256(read(cached, BODY_BYTES)).hexdigest()
    script = call("SpawnScript", (SCRIPT_KEY,) + (0,) * 9)
    spawned = registry()
    host.require(script in spawned and len(spawned) == len(before) + 1, "Original HUD spawn did not publish exactly one CScript")
    state = spawned[script]
    host.require(state[0xc4 // 4] == 0 and state[0xd0 // 4] == SCRIPT_KEY and state[0xd4 // 4] == 1 and
                 state[0xe0 // 4] == state[0xe4 // 4] == 0 and cached <= state[4] < cached + BODY_BYTES,
                 "Original HUD spawn has the wrong source, owner, flags or program counter")
    log("hud_source_begin", source=SOURCE_NAME, source_bytes=SOURCE_BYTES, source_sha256=SOURCE_SHA,
        script_checksum=SCRIPT_KEY, whole_body_bytes=BODY_BYTES, stored_body_bytes=2941,
        cached_body=cached, cached_body_sha256=cached_sha, original_spawned_script=script, baseline_scripts=len(before),
        host_script_flags_written=False, host_source_writes=False)
    result = call("ScriptUpdate", this=script)
    current = registry()
    host.require(script in current and word(0x6dcf84) == 0, "Original HUD update lost its live Script or current context")
    state = current[script]
    completed = result == 0
    host.require(state[0xc4 // 4] == 0 and state[0xd4 // 4] & 1 and
                 ((completed and state[4] == 0) or
                  (result in (1, 2) and state[4] and state[0xa8 // 4] & 15 and state[0xbc // 4] & 1)),
                 "Whole original HUD did not return its native completion/wait state")
    for pointer, original in before.items():
        host.require(pointer in current and current[pointer][0xb0 // 4] == original[0xb0 // 4] and
                     current[pointer][0xc4 // 4] == original[0xc4 // 4],
                     "Whole original HUD replaced an existing owner Script")
    if completed:
        call("ScriptDtor", this=script)
        call("ScriptFree", (script,))
        call("ScriptCacheReleaseReference", (SCRIPT_KEY,), cache)
    after = registry()
    host.require(len(after) + int(completed) == len(current) and (not completed or script not in after) and
                 all(pointer in current and state[0xb0 // 4] == current[pointer][0xb0 // 4] and
                     state[0xc4 // 4] == current[pointer][0xc4 // 4] for pointer, state in after.items()),
                 "Original completed HUD Script did not clean up its registry entry")
    balance = call("EventLookup", (BALANCE_ID,), events)
    balance_words = words(balance, 0x30 // 4)
    host.require(0x68e000 <= balance_words[0] < 0x6bc000 and balance_words[3] == screen and
                 balance_words[6] == BALANCE_ID and balance_words[0x2c // 4] != 0,
                 "Whole authored HUD did not create its genuine balance meter and tags")
    output = record + 72
    host.remote_write(api, process, output, struct.pack("<I", 0xdeadbeef))
    host.require(call("StructGetInteger", (TAG_TURNED_ON, output, 0), balance_words[0x2c // 4]) & 0xff and word(output) == 0,
                 "Original HUD balance tag_turned_on was not created with its source default")
    log("hud_source_pass", whole_authored_script=True, script_checksum=SCRIPT_KEY,
        original_updates=1, completed=completed, result=result, pending_wait_kind=state[0xa8 // 4] & 15,
        balance_meter=balance, tag_turned_on=0, baseline_scripts_preserved=len(before),
        retained_original_scripts=len(after), host_screen_objects_created=0, host_source_writes=False,
        original_ui_renderer_executed=False, gta_hud_published=False, backend_ready=False)
