"""Synthetic native IPL memory; never opens or writes to a game process."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch

import inspect_ipls as i
from test_probe_world import FakeObserver, snapshot


class FakeMemory:
    def __init__(self):
        self.total = 0
        self.blocks = {**i.GUARDS, i.POOL_POINTER: struct.pack("<I", 0x100000),
                       0x100000: struct.pack("<IIi", 0x200000, 0x300000, 256),
                       i.STREAMING_POINTER: struct.pack("<I", i.STREAMING_BASE),
                       0x300000: bytes([0]*5 + [0x80]*251)}
        definitions = bytearray(256*i.RECORD_SIZE)
        for slot, name in enumerate(sorted(i.SUPPORTED_STREAMS)):
            start = slot*i.RECORD_SIZE
            definitions[start+16:start+16+len(name)] = name.encode()
            definitions[start+0x2f] = 1
            self.blocks[i.STREAMING_BASE+(i.IPL_MODEL_BASE+slot)*20+16] = b"\0"
        self.blocks[0x200000] = definitions

    def read(self, address, size):
        self.total += size
        for base, data in self.blocks.items():
            if base <= address and address+size <= base+len(data):
                return bytes(data[address-base:address-base+size])
        raise OSError("unmapped fixture address")

    def active(self, slot=0, loaded=1, state=1):
        self.blocks[0x200000][slot*i.RECORD_SIZE+0x2d] = loaded
        self.blocks[i.STREAMING_BASE+(i.IPL_MODEL_BASE+slot)*20+16] = bytes([state])


def reader(memory):
    result = i.IplObserver.__new__(i.IplObserver)
    result.memory, result.catalogue = memory, dict.fromkeys(i.SUPPORTED_STREAMS, 1)
    result._pool_cache = {}
    return result


class IplTests(unittest.TestCase):
    def test_warm_parse_still_observes_streaming_bytes_and_does_not_share_mutable_results(self):
        memory = FakeMemory(); observed = reader(memory)
        first = observed.read()
        first['streams'][0]['loaded'] = True
        first['active_streams'].append('fake')
        self.assertEqual(observed.read()['active_streams'], [])
        memory.active(loaded=0, state=3)
        current = observed.read()
        self.assertEqual(current['streams'][0]['streaming_state'], 3)
        self.assertFalse(current['streams'][0]['loaded'])
        # Neither the pool bytes nor flags change: this must still read/reject.
        memory.active(loaded=0, state=1)
        with self.assertRaisesRegex(i.Rejected, 'ipl_load_transition'):
            observed.read()
        memory.active(loaded=1, state=1)
        self.assertEqual(observed.read()['active_streams'], ['barriers1'])

    def test_warm_cache_rechecks_all_changed_definition_bytes_and_code_guards(self):
        for change, reason in (('name', 'unterminated'), ('generation', None),
                               ('flags', 'invalid_ipl_flags'), ('guard', 'layout_guard')):
            memory = FakeMemory(); observed = reader(memory)
            observed.read()
            if change == 'name':
                # A formerly unused, unrelated slot cannot bypass full parsing.
                memory.blocks[0x300000] = bytes([0]*6+[0x80]*250)
                memory.blocks[0x200000][5*52+16:5*52+34] = b'X'*18
            elif change == 'generation':
                memory.blocks[0x300000] = bytes([7]+[0]*4+[0x80]*251)
            elif change == 'flags':
                memory.blocks[0x200000][0x2f] = 2
            else:
                memory.blocks[0x404A60] = bytes(38)
            with self.subTest(change=change):
                if reason:
                    with self.assertRaisesRegex(i.Rejected, reason): observed.read()
                else:
                    self.assertEqual(observed.read()['streams'][0]['generation'], 7)

    def test_identical_pool_bytes_at_a_new_address_are_observed_as_new_identity(self):
        memory = FakeMemory(); observed = reader(memory)
        first = observed.read()
        memory.blocks[0x400000] = memory.blocks.pop(0x200000)
        memory.blocks[0x100000] = struct.pack('<IIi', 0x400000, 0x300000, 256)
        moved = observed.read()
        self.assertEqual(moved['streams'], first['streams'])
        self.assertNotEqual(moved['objects_address'], first['objects_address'])

    def test_active_ignored_ipl_is_included_and_order_is_stable(self):
        memory = FakeMemory()
        memory.active()
        state = reader(memory).read()
        self.assertEqual(state["active_streams"], ["barriers1"])
        self.assertTrue(state["streams"][0]["disable_dynamic_streaming"])
        self.assertEqual([s["name"] for s in state["streams"]], sorted(i.SUPPORTED_STREAMS))
        self.assertLess(memory.total, 30000)

    def test_loading_is_inactive_but_disagreement_is_rejected(self):
        for state in (0, 2, 3, 4):
            memory = FakeMemory()
            memory.active(loaded=0, state=state)
            self.assertEqual(reader(memory).read()["active_streams"], [])
        for loaded, state in ((1, 0), (1, 3), (0, 1)):
            memory = FakeMemory()
            memory.active(loaded=loaded, state=state)
            with self.assertRaisesRegex(i.Rejected, "ipl_load_transition"):
                reader(memory).read()

    def test_missing_duplicate_and_unterminated_names_fail_closed(self):
        memory = FakeMemory()
        memory.blocks[0x300000] = bytes([0x80] + [0]*4 + [0x80]*251)
        with self.assertRaisesRegex(i.Rejected, "missing_conditional"):
            reader(memory).read()
        memory = FakeMemory()
        memory.blocks[0x200000][52+16:52+34] = memory.blocks[0x200000][16:34]
        with self.assertRaisesRegex(i.Rejected, "duplicate_conditional"):
            reader(memory).read()
        memory.blocks[0x200000][16:34] = b"X"*18
        with self.assertRaisesRegex(i.Rejected, "unterminated"):
            reader(memory).read()

    def test_bad_pointer_budget_layout_and_flags_fail_closed(self):
        for address, value, reason in (
            (i.POOL_POINTER, struct.pack("<I", 0), "read_range"),
            (0x100000, struct.pack("<IIi", 0x200000, 0x300000, 257), "pool_size"),
            (i.STREAMING_POINTER, struct.pack("<I", 0), "streaming_array"),
            (0x404A60, bytes(38), "layout_guard")):
            memory = FakeMemory()
            memory.blocks[address] = value
            with self.subTest(reason=reason), self.assertRaisesRegex(i.Rejected, reason):
                reader(memory).read()
        memory = FakeMemory()
        memory.blocks[0x200000][0x2f] = 2
        with self.assertRaisesRegex(i.Rejected, "invalid_ipl_flags"):
            reader(memory).read()

    def test_reused_pool_slot_or_activation_during_read_rejected(self):
        for change in ("generation", "activation"):
            memory = FakeMemory()
            original = memory.read
            calls = 0
            def changing(address, size):
                nonlocal calls
                if address == i.POOL_POINTER:
                    calls += 1
                    if calls == 2:
                        if change == "generation":
                            memory.blocks[0x300000] = bytes([1]+[0]*4+[0x80]*251)
                        else:
                            memory.active()
                return original(address, size)
            memory.read = changing
            with self.subTest(change=change), self.assertRaisesRegex(i.Rejected, "changed_during_read"):
                reader(memory).read()

    def test_scene_change_and_staleness_discard_inspection(self):
        for after, reason in ((snapshot(epoch=4), "scene_changed"), (snapshot(age_ms=1001), "stale")):
            with self.assertRaisesRegex(i.Rejected, reason):
                i.inspect_once(FakeObserver(snapshot(), after), reader(FakeMemory()))
        good = i.inspect_once(FakeObserver(snapshot(), snapshot(sequence=101)), reader(FakeMemory()))
        self.assertFalse(good["physics_ready"])
        self.assertEqual(good["sequence_end"], 101)

    def test_database_mapping_requires_exact_sources_and_supported_names(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"world.db"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE instances(stream TEXT,payload TEXT,conditional INTEGER)")
                for name in i.SUPPORTED_STREAMS:
                    db.execute("INSERT INTO instances VALUES(?,?,1)", (name, json.dumps(dict(
                        source=f"models/gta3.img::{name}.ipl", stream=name, conditional=True))))
            with patch.object(i, "database_info"):
                self.assertEqual(set(i.conditional_streams(path)), i.SUPPORTED_STREAMS)
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute("UPDATE instances SET payload='{}' WHERE stream='crack'")
                with self.assertRaisesRegex(i.Rejected, "ambiguous_conditional"):
                    i.conditional_streams(path)


if __name__ == "__main__":
    unittest.main()
