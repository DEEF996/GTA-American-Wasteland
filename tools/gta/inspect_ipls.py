"""Bounded read-only conditional IPL inspection for the pinned GTA 1.0 US image.

Only existing pool records and streaming state are read. No native call, process
write, new protocol, or dynamic entity reconciliation is performed.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import struct
import time

from inspect_rig import Memory, address_range, require
from native.inspect_state import Observer
from probe_world import MAX_AGE_MS, Rejected, database_info, snapshot_stamp, validate_snapshot


POOL_POINTER = 0x8E3FB0
STREAMING_BASE = 0x8E4CC0
STREAMING_POINTER = 0x9654B4
IPL_MODEL_BASE = 25255
MAX_SLOTS, RECORD_SIZE = 256, 0x34
SUPPORTED_STREAMS = frozenset(("barriers1", "barriers2", "carter", "crack", "truthsfarm"))
# Original GetIplName proves pool/bitmap/stride/name offsets; LoadIpl proves
# loaded-byte offset. The complete executable is already pinned by Observer.
GUARDS = {
    0x404A60: bytes.fromhex("8b0db03f8e008b51048b442404803c1000790833c0b810000000c38b116bc03403c283c010c3"),
    0x406179: bytes.fromhex("c6452d01"),
}


def conditional_streams(database):
    """Require an exact, non-aliased mapping from private DB to native IPL names."""
    database_info(database)
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT stream,payload FROM instances WHERE conditional=1 LIMIT 10001").fetchall()
    require(0 < len(rows) <= 10000, "unsupported_conditional_instance_count")
    counts = {}
    for name, raw in rows:
        require(name in SUPPORTED_STREAMS, "unsupported_conditional_stream")
        instance = json.loads(raw)
        require(instance.get("source") == f"models/gta3.img::{name}.ipl"
                and instance.get("stream") == name and instance.get("conditional") is True,
                "ambiguous_conditional_stream_source")
        counts[name] = counts.get(name, 0) + 1
    require(set(counts) == SUPPORTED_STREAMS, "conditional_stream_catalogue_mismatch")
    return dict(sorted(counts.items()))


def word(memory, address):
    return struct.unpack("<I", memory.read(address, 4))[0]


def read_pool(memory, names, cache=None):
    """Read at most 256 definitions, then five exact model load-state bytes."""
    pool = word(memory, POOL_POINTER)
    address_range(pool, 20)
    require(pool % 4 == 0, "unaligned_ipl_pool")
    objects, bitmap, size = struct.unpack("<IIi", memory.read(pool, 12))
    require(0 < size <= MAX_SLOTS, "unsupported_ipl_pool_size")
    address_range(objects, size * RECORD_SIZE)
    address_range(bitmap, size)
    require(objects % 4 == 0, "unaligned_ipl_objects")
    require(word(memory, STREAMING_POINTER) == STREAMING_BASE, "unsupported_streaming_array")
    flags = memory.read(bitmap, size)
    definitions = memory.read(objects, size * RECORD_SIZE)
    # Compare every freshly read byte, including generation/flags/unused slots.
    # Only parsing is cached; original guards and streaming reads remain live.
    identity = pool, objects, bitmap, size, flags, definitions, tuple(sorted(names))
    if cache is not None and cache.get('identity') == identity:
        records = cache['records']
    else:
        found = {}
        for slot, flag in enumerate(flags):
            if flag & 0x80:
                continue
            record = definitions[slot*RECORD_SIZE:(slot+1)*RECORD_SIZE]
            raw_name = record[0x10:0x22]
            require(b"\0" in raw_name, "unterminated_ipl_name")
            raw_name = raw_name.split(b"\0", 1)[0]
            require(raw_name and all(32 <= char < 127 for char in raw_name), "invalid_ipl_name")
            name = raw_name.decode("ascii").lower()
            if name not in names:
                continue
            require(name not in found, "duplicate_conditional_ipl")
            values = record[0x2C:0x31]
            require(all(value in (0, 1) for value in values), "invalid_ipl_flags")
            interior, loaded, requested, disabled, ignored = values
            found[name] = dict(name=name, slot=slot, generation=flag & 0x7F,
                loaded=bool(loaded), load_requested=bool(requested),
                disable_dynamic_streaming=bool(disabled), ignore_when_deleted=bool(ignored),
                is_interior=bool(interior), active=bool(loaded))
        require(set(found) == set(names), "missing_conditional_ipl")
        records = [found[name] for name in sorted(found)]
        if cache is not None:
            cache.update(identity=identity, records=records)
    streams = []
    for record in records:
        slot = record['slot']
        state = memory.read(STREAMING_BASE + (IPL_MODEL_BASE+slot)*20 + 16, 1)[0]
        require(state <= 4, "invalid_ipl_streaming_state")
        # A load can set IplDef.loaded before entity construction has finished.
        # Conversely removal can clear it while streaming still says LOADED.
        require(record['loaded'] == (state == 1), "ipl_load_transition")
        streams.append(dict(record, streaming_state=state))
    return dict(pool_address=pool, objects_address=objects, bitmap_address=bitmap,
                pool_size=size, streams=streams,
                active_streams=[item["name"] for item in streams if item["active"]])


class IplObserver:
    def __init__(self, observer, database):
        self.catalogue = conditional_streams(database)
        self.memory = Memory(observer)
        self._pool_cache = {}

    def read(self):
        self.memory.total = 0
        for address, expected in GUARDS.items():
            require(self.memory.read(address, len(expected)) == expected, "ipl_layout_guard_mismatch")
        first = read_pool(self.memory, self.catalogue, self._pool_cache)
        require(first == read_pool(self.memory, self.catalogue, self._pool_cache), "ipl_changed_during_read")
        return first

    def close(self):
        self.memory.close()


def inspect_once(observer, ipls, *, clock=time.perf_counter):
    started = clock()
    before = observer.read()
    validate_snapshot(observer, before)
    state = ipls.read()
    after = observer.read()
    validate_snapshot(observer, after)
    require(snapshot_stamp(before) == snapshot_stamp(after), "scene_changed_during_ipl_read")
    require(after["sequence"] >= before["sequence"] and after["game_timer"] >= before["game_timer"]
            and after["frame_counter"] >= before["frame_counter"], "snapshot_clock_rolled_back")
    elapsed = (clock()-started)*1000
    require(elapsed + before["age_ms"] <= MAX_AGE_MS, "ipl_snapshot_expired")
    return dict(format="gta-conditional-ipl-inspection-v1", scene=snapshot_stamp(before),
                sequence_begin=before["sequence"], sequence_end=after["sequence"],
                elapsed_ms=round(elapsed, 3), catalogue=ipls.catalogue, **state,
                physics_ready=False, dynamic_entities_reconciled=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.pid <= 0xffffffff or args.output.exists():
        raise ValueError("Positive PID and a new report path are required")
    observer, ipls = Observer(args.pid), None
    try:
        ipls = IplObserver(observer, args.database)
        report = inspect_once(observer, ipls)
    finally:
        if ipls is not None:
            ipls.close()
        observer.close()
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(dict(output=str(args.output), active_streams=report["active_streams"],
                          elapsed_ms=report["elapsed_ms"], physics_ready=False)))


if __name__ == "__main__":
    main()
