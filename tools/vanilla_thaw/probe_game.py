"""Original retained Game/Events/Mode/Replay context, then a 10/10 profile.

This constructs original objects from complete original QB inputs. It neither
creates a CSkater nor runs task callbacks, a game frame or a replacement world.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import sys

import probe_loader as host
import probe_profile as profile
import probe_resources as resources

METADATA_SHA = "51d8854734632455c46d664803338b052338b508ec7b549f2835e713177ad2e1"
PROFILE_SOURCE = "scripts/game/skater/skater_profile.qb.wpc"


def read_metadata():
    raw = Path(__file__).with_name("game_context_bootstrap.json").read_bytes()
    host.require(hashlib.sha256(raw).hexdigest() == METADATA_SHA, "Game context metadata differs")
    value = json.loads(raw)
    host.require(value["format"] == "stock-game-context-proof-pins-v1" and value["stock_exe_sha256"] == host.FILES["THAW.exe"][1] and
                 len(value["functions"]) == 23 and len(value["sources"]) == 6,
                 "Game context metadata shape differs")
    return value


def source_rows(rows, directory, metadata):
    selected = []
    for expected in metadata["sources"]:
        matches = [row for row in rows if row["order"] == expected["order"]]
        host.require(len(matches) == 1 and all(matches[0][key] == expected[key] for key in expected), "Game whole-source pin differs or is duplicated")
        path = (directory / expected["cache_file"]).resolve()
        host.require(path.is_relative_to(directory), "Game source escapes private cache")
        selected.append((path, expected))
    return selected


def read_inputs(qb_manifest, profile_manifest, image_manifest):
    host.require(qb_manifest.is_absolute() and profile_manifest.is_absolute(), "Game input manifests must be absolute")
    metadata = read_metadata()
    qb = json.loads(qb_manifest.read_text(encoding="utf-8-sig"))
    native = json.loads(image_manifest.read_text(encoding="utf-8-sig"))
    host.require(qb.get("format") == "stock-thaw-core-qb-cache-v1" and qb.get("member_count") == 268 and
                 Path(qb["source_directory"]).resolve() == Path(native["source"]).resolve() and
                 qb.get("sources_revalidated_before_publish") is True, "Game QB cache source differs")
    inputs = []
    for path, expected in source_rows(qb["members"], qb_manifest.resolve().parent, metadata):
        contents = path.read_bytes()
        host.require(len(contents) == expected["bytes"] and hashlib.sha256(contents).hexdigest() == expected["sha256"], "Game original source bytes differ")
        inputs.append((contents, expected["canonical_name"]))
    profile_inputs = profile.read_inputs(profile_manifest, image_manifest)
    shared = [source for source, name in inputs if name == PROFILE_SOURCE]
    shared_profile = [source for source, name in profile_inputs if name == PROFILE_SOURCE]
    host.require(len(shared) == len(shared_profile) == 1 and shared[0] == shared_profile[0], "Game and final profile must share the identical original profile source")
    return {"metadata": metadata, "sources": inputs, "profile": profile_inputs}


def run(api, process, record, deadline, log, inputs, preloaded=None):
    metadata, global_ = inputs["metadata"], inputs["metadata"]["globals"]
    pins = {row[0]: row for row in metadata["functions"]}
    resource_pins = {row[0]: row for row in resources.read_metadata()["functions"]}

    def read(address, size):
        return host.remote_read(api, process, address, size)

    def word(address):
        return struct.unpack("<I", read(address, 4))[0]

    arena_start, arena_end = word(0x84376c), word(0x843770)

    def inside(address, size):
        return address % 4 == 0 and arena_start <= address and address + size <= arena_end

    def words(address, count):
        host.require(inside(address, count * 4), "Game native object escapes original arena")
        return struct.unpack("<" + "I" * count, read(address, count * 4))

    def vtable(address):
        value = word(address)
        host.require(value % 4 == 0 and 0x68e000 <= value < 0x6bc000, "Original context vtable is outside pinned read-only image")
        return value

    def call(name, arguments=(), this=None):
        pin = pins.get(name) or profile.PINS.get(name) or resource_pins.get(name) or host.QB_PINS[name]
        return host.native_call(api, process, pin, list(arguments), record, deadline, log, this)

    def create(name, key):
        value = call(name, (1,))
        slot, refs = global_[key][:2]
        host.require(inside(value, metadata["factory_sizes"][key]) and word(slot) == value and word(refs) == 1,
                     "Original context singleton differs: " + key)
        log("game_context_factory", name=key, address=value, original_factory=pins.get(name, profile.PINS.get(name))[1], bytes=metadata["factory_sizes"][key])
        return value

    for name, address, size, digest in metadata["functions"]:
        host.require(hashlib.sha256(read(address, size)).hexdigest() == digest, "Original Game context entry differs: " + name)
    host.require(word(0x6dd6b0) != 0 and word(0x6dd8d8) != 0 and word(0x7ecd04) != 0 and word(0x83e87c) != 0 and
                 word(0x76b2a8) != 0 and word(0x842b18 + 0x128) != 0,
                 "Combined Game bootstrap requires genuine task/component/resource/Script services")
    for key in ("game", "profile", "tags", "players", "history", "events", "replay", "mode"):
        host.require(all(word(address) == 0 for address in global_[key]), "Game context singleton is already owned: " + key)
    host.require(all(word(address) == 0 for address in (0x6dcf84, 0x6dcf8c, 0x6dcf90)) and
                 not any(read(global_["animation_maps"][1], 56)), "Game context requires idle VM and untouched animation maps")
    shared = {}
    for source, filename in inputs["sources"]:
        if preloaded is not None:
            import probe_core
            owned = probe_core.reuse(api, process, preloaded, filename, source)
            if filename == PROFILE_SOURCE:
                shared[filename] = (owned, len(source))
            log("game_source_reused", filename=filename, bytes=len(source), sha256=hashlib.sha256(source).hexdigest())
            continue
        name_offset = (len(source) + 15) & ~15
        encoded = filename.encode("ascii") + b"\0"
        owned = host.success(api.VirtualAllocEx(process, None, name_offset + len(encoded), 0x3000, 4), "Allocate owned whole Game source")
        host.remote_write(api, process, owned, source)
        host.remote_write(api, process, owned + name_offset, encoded)
        call("load", (owned + name_offset, owned, 0))
        host.require(read(owned, 1) == b"\1", "Whole Game source did not load")
        if filename == PROFILE_SOURCE:
            shared[filename] = (owned, len(source))
        log("game_source_loaded", filename=filename, bytes=len(source), sha256=hashlib.sha256(source).hexdigest())
    for checksum in (0x0076eae3, 0x0486fd79, 0x9dbe44b1, 0x535837ae):
        host.require(call("GlobalStructure", (checksum, 0)) >= 0x10000, "Whole Game/FreeSkate source structure is absent")
    for checksum in (0xd648faae, 0xc33bda9c, 0xca5f7ffb):
        host.require(call("GlobalArray", (checksum, 0)) >= 0x10000, "Whole Game/FreeSkate source array is absent")
    host.require(call("OriginalGlobalGetInteger", (0xbf46c767, 0)) == 10000, "Original career combo source differs")

    events = create("eventmanager_factory", "events")
    event_words = words(events, 0x20c // 4)
    events_vtable = vtable(events)
    host.require(event_words[8] == event_words[9] == 0, "Original Events dispatch state is not empty")
    for offset, bits in ((8, 8), (12, 4), (16, 8)):
        table = words(event_words[offset // 4], 7)
        host.require(table[0] == bits and table[2] == 0 and inside(table[1], (1 << bits) * 12), "Original Events map extent differs")
        host.require(not any(read(table[1], (1 << bits) * 12)), "Original Events map is not empty")
    for address in global_["animation_maps"]:
        host.require(call("animation_maps_ctor", (8, 0), this=address) == address, "Original animation-map ctor returned another object")
        bits, buckets, count = struct.unpack("<3I", read(address, 12))
        host.require(bits == 8 and count == 0 and inside(buckets, 256 * 12) and not any(read(buckets, 256 * 12)), "Original animation map is not empty")
    profile_manager = create("ManagerFactory", "profile")
    tags = create("tags_factory", "tags")
    players = create("players_factory", "players")
    history = create("history_factory", "history")
    game = create("game_factory", "game")
    game_words = words(game, 0x9c // 4)
    career = game_words[0x20 // 4]
    context = game_words[0x24 // 4]
    career_words = words(career, 0x264 // 4)
    context_words = words(context, 0xe8 // 4)
    history_words = words(history, 3)
    host.require(game_words[0] == 0x69807c and career_words[0] == 0x698a50 and
                 not any(context_words[:41]) and history_words[0] == 40 and
                 inside(history_words[2] - 4, (1 + 40 * 6) * 4) and
                 word(global_["component_manager_alias"]) == word(0x6dd6b0) and
                 career_words[0x258 // 4] == 10000 and game_words[0x44 // 4] == 0x34861a16,
                 "Original Game/Career/input topology differs")
    host.require(word(profile_manager) == 0 and word(profile_manager + 0x14) == 0,
                 "Game constructor did not preserve the empty profile manager")
    sentinel = players + 0x1f8
    player_list = words(sentinel, 5)
    host.require(0x68e000 <= player_list[0] < 0x6bc000 and player_list[1:] == (0xffffffff, 0, sentinel, sentinel),
                 "Original Players list must remain empty")
    log("game_context_constructed", game=game, career=career, input_context=context, history_records=40,
        source_minimum_combo=career_words[0x258 // 4], players=players, tags=tags,
        events=events, events_vtable=events_vtable, profile_manager_empty=True, registered_skaters=0)

    mode = create("mode_factory", "mode")
    mode_words = words(mode, 8)
    mode_vtable = vtable(mode)
    host.require(0x68e000 <= mode_words[3] < 0x6bc000 and mode_words[4:] == (0xffffffff, 0, mode + 12, mode + 12),
                 "Original GameMode list topology differs")
    host.remote_write(api, process, record + 72, bytes(4))
    host.require(call("GetChecksum", (0xa1dc81f9, record + 72, 0), this=mode + 4) & 0xff and
                 word(record + 72) == 0x34861a16, "Original Mode constructor did not copy the actual FreeSkate Name")
    log("game_mode_constructed", mode=mode, native_vtable=mode_vtable, name_checksum=word(record + 72), mode_tasks_executed=0)

    replay = create("replay_singleton", "replay")
    replay_words = words(replay, 0x7c // 4)
    host.require(replay_words[0] == 0x6a15bc and replay_words[0x40 // 4] == 0xd443a2bc and
                 replay_words[0x14 // 4] == 0x6a15b0 and replay_words[0x1c // 4] == 0 and
                 read(replay + 0x28, 1) == b"\1", "Original Replay Off state/observer differs")
    for offset, size, task_vtable, callback_offset, callback in ((0x34, 0x20, 0x6a16a4, 0x18, 0x610170),
                                                                (0x38, 0x20, 0x6a16a4, 0x18, 0x51acd0),
                                                                (0x3c, 0x2c, 0x6a16b0, 0x24, 0x60f010)):
        task = words(replay_words[offset // 4], size // 4)
        host.require(task[0] == task_vtable and all(task[index] == 0 for index in (1, 3, 4, 5)) and
                     task[callback_offset // 4] == callback and task[callback_offset // 4 + 1] == replay,
                     "Original Replay task callback/owner/list differs")
    replay_record = word(global_["replay"][2])
    host.require(inside(replay_record, 0x177d4) and inside(word(replay_record), 0x474), "Original Replay record/auxiliary extent differs")
    log("game_replay_constructed", replay=replay, state_checksum=0xd443a2bc, unregistered_tasks=3,
        native_observer_queued=True, replay_record=replay_record, native_recording_started=False)

    if preloaded is not None:
        source, filename = next((source, filename) for source, filename in inputs["profile"] if filename == "scripts/game/skater/protricks.qb.wpc")
        import probe_core
        shared[filename] = (probe_core.reuse(api, process, preloaded, filename, source), len(source))
    final_profile = profile.run(api, process, record, deadline, log, inputs["profile"],
                                existing_manager=profile_manager, preloaded=shared)
    host.require(word(profile_manager + 0x14) == final_profile and all(word(address) == 0 for address in (0x6dcf84, 0x6dcf8c, 0x6dcf90)),
                 "Combined Game/profile bootstrap lost its selection or idle VM")
    host.require(words(sentinel, 5) == player_list, "Combined profile construction registered an unexpected skater")
    log("game_pass", game=game, events=events, mode=mode, replay=replay, profile=final_profile,
        selected_profile="custom_classic", stats=[10] * 10, maximum_stats_applied_after_game=True,
        game_sources=len(inputs["sources"]), source_profile_loaded_once=True, registered_skaters=0,
        task_callbacks_executed=0, game_frames_executed=0, native_destructors_tested=False,
        retained_until_child_exit=True, backend_ready=False,
        scope="original Game/Career/Events/FreeSkate/Replay context and native 10/10 profile; no CSkater or gameplay")
    return {"game": game, "profile": final_profile, "events": events, "mode": mode, "replay": replay}


def self_test():
    metadata = read_metadata()
    directory = Path(__file__).resolve().parent
    rows = metadata["sources"]
    host.require(len(source_rows(rows, directory, metadata)) == 6, "Game source selection failed")
    for invalid in (rows[:-1], rows + [rows[0]], [{**rows[0], "sha256": "0" * 64}, *rows[1:]], [{**rows[0], "canonical_name": "wrong.qb"}, *rows[1:]]):
        try:
            source_rows(invalid, directory, metadata)
        except RuntimeError:
            pass
        else:
            raise RuntimeError("Game source selection accepted malformed input")
    host.require(metadata["globals"]["players"] == [0x6ea804, 0x6ea808] and
                 {row[0]: row[1] for row in metadata["functions"]}["players_factory"] == 0x4a05c0,
                 "Game Players mapping regressed to the unrelated Events template")
    print("PASS: Game context pins, actual Players provenance and source-selection guards; no child created")


if __name__ == "__main__":
    host.require(sys.argv[1:] == ["--self-test"], "Use probe_loader.py for native execution")
    self_test()
