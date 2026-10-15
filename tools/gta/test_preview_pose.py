"""Synthetic preview boundary tests; no assets, game or writable process handle."""
import hashlib
import ctypes as C
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import preview_pose as p


def fixture():
    identity = [float(i % 5 == 0) for i in range(16)]
    nodes = [dict(ordinal=i, bone_id=i, matrix_index=i, parent_flags=int(i == 31), frame_address=0)
             for i in range(32)]
    hierarchy = dict(address=0x80000, count=32, flags=0x3000, matrix_space="world",
                     node_address=0x90000, matrix_address=0xA0000, nodes=nodes)
    skin = dict(address=0xB0000, count=32, inverse_bind_address=0xC0000,
                inverse_bind_invariants=[dict(affine_row_major=identity) for _ in range(32)])
    atomic = dict(address=0x60000, geometry_address=0x70000, hierarchy_address=0x80000, skin_address=0xB0000)
    scene = dict(pid=17, creation_100ns=123, ped_address=0x20000, epoch=3, area=0, ped_area=0)
    target = dict(format="gta-thaw-cj-rig-inspection-v1", accepted=True,
                  executable=str(Path.cwd()/"gta_sa.exe"), scene=scene,
                  rig=dict(clump_address=0x50000, atomics=[atomic], hierarchies=[hierarchy], skins=[skin],
                           plugin_offsets=dict(atomic_hierarchy=0x78, geometry_skin=0x64)))
    rig_bytes = json.dumps(target).encode()
    models = [list(identity) for _ in range(32)]
    models[1][14] = .25
    pose_hash = hashlib.sha256(struct.pack("<512d", *(v for m in models for v in m))).hexdigest()
    report = dict(format="gta-thaw-offline-retarget-v1", offline_checks_passed=True, target_bones=32,
                  inputs=[{}, dict(sha256=hashlib.sha256(rig_bytes).hexdigest()), {}], nodes=nodes,
                  frames=[dict(model_row_major=models, target_model_sha256=pose_hash, time=.3)])
    return rig_bytes, report


class RigMemory:
    def __init__(self, prepared):
        self.words = {0x20000: 0x86D168, 0x20018: 0x50000,
                      0x50008: 0x60040, 0x5000C: 0x60040,
                      0x6003C: 0x50000, 0x60040: 0x50008, 0x60044: 0x50008, 0x60018: 0x70000,
                      0xC978A4: 0x78, 0xC978A8: 0x64, 0x60078: 0x80000, 0x70064: 0xB0000,
                      0x80000: 0x3000, 0x80004: 32, 0x80008: 0xA0000, 0x80010: 0x90000,
                      0xB0000: 32, 0xB000C: 0xC0000}
        self.raw_binds = bytearray(prepared['bind_bytes'])
        # RW flags/padding are not affine floats, including their NaN bit patterns.
        for i in range(32):
            for offset in (12, 28, 44, 60):
                struct.pack_into('<I', self.raw_binds, i*64+offset, 0x7FC01234)
        self.nodes, self.reads = prepared['nodes'], []
        self.fail_final_clump = False

    def read(self, address, size):
        self.reads.append((address, size))
        if address == 0x90000:
            return self.nodes
        if address == 0xC0000:
            return bytes(self.raw_binds)
        if (address == 0x20018 and self.fail_final_clump and
                self.reads.count((address, size)) == 2):
            return struct.pack('<I', self.words[address]+4)
        return struct.pack('<I', self.words[address])


class PreviewTests(unittest.TestCase):
    def prepare(self, rig=None, report=None):
        if rig is None:
            rig, default = fixture()
            report = report or default
        return p.prepare(rig, json.dumps(report).encode(), 0)

    def test_exact_packet_offsets_and_canonical_matrices(self):
        prepared = self.prepare()
        raw = p.packet(prepared, prepared["target"]["scene"], 5, 1234)
        self.assertEqual(len(raw), 5168)
        header = p.HEADER.unpack_from(raw)
        self.assertEqual(header[:8], (b"GTAPOSE5", 5, 5168, 17, 3, 123, 5, 1234))
        self.assertEqual(p.NODE.unpack_from(raw, 112), (0, 0, 0, 0))
        self.assertEqual(struct.unpack_from("<f", raw, 2672+64+14*4)[0], .25)
        self.assertEqual(raw[4720:5032], bytes(312))
        self.assertEqual(p.VISUAL.unpack_from(raw,5032),(*p.IDENTITY,0))
        self.assertEqual(p.DETACHED.unpack_from(raw,5100),(*p.IDENTITY,0))
        prepared["board_hash"] = b"b"*32
        prepared["board_palette"] = struct.pack("<64f",*([float(i%5==0) for i in range(16)]*4))
        raw = p.packet(prepared, prepared["target"]["scene"], 6, 1235)
        self.assertEqual(p.HEADER.unpack_from(raw)[-1], 1)
        self.assertEqual(raw[4720:4752], b"b"*32)
        self.assertEqual(raw[4752:5008],prepared["board_palette"])
        self.assertEqual(p.VISUAL.unpack_from(raw,5032)[-1],1)
        prepared['board_visible']=False
        raw=p.packet(prepared,prepared['target']['scene'],7,1236)
        self.assertEqual(p.HEADER.unpack_from(raw)[-1],1)
        self.assertEqual(p.VISUAL.unpack_from(raw,5032)[-1],0)
        prepared['visual_delta']=list(p.IDENTITY);prepared['visual_delta'][12]=11
        with self.assertRaisesRegex(ValueError,'visual pivot'):
            p.packet(prepared,prepared['target']['scene'],7,1236)

    def test_detached_board_world_matrix_requires_visible_original_identity(self):
        prepared=self.prepare();prepared['board_hash']=b'b'*32
        prepared['board_world']=list(p.IDENTITY);prepared['board_world'][12]=1500.
        prepared['board_object_id']=99
        raw=p.packet(prepared,prepared['target']['scene'],7,1236)
        self.assertEqual(p.DETACHED.unpack_from(raw,5100),(*prepared['board_world'],99))
        for key,value in (('board_object_id',0),('board_object_id',-1),('board_visible',False),('board_hash',bytes(32))):
            with self.subTest(key=key):
                changed=dict(prepared);changed[key]=value
                with self.assertRaisesRegex(ValueError,'detached board'):
                    p.packet(changed,changed['target']['scene'],7,1236)

    def test_source_clock_distinguishes_gameplay_samples_from_heartbeats(self):
        prepared = self.prepare()
        args = (prepared, prepared["target"]["scene"])
        packet = p.packet(*args, 5, 1234, request=89, source_tick=60, source_time_us=1000000)
        heartbeat = p.packet(*args, 6, 1240, request=89, source_tick=60, source_time_us=1000000)
        self.assertEqual(packet[48:], heartbeat[48:])
        self.assertEqual(p.SOURCE_CLOCK.unpack_from(packet, 5008), (89, 60, 1000000))
        for clock in ((0, 1, 1), (1, 0, 1), (1, 1, 0), (-1, 0, 0), (2**64, 0, 0)):
            with self.subTest(clock=clock), self.assertRaisesRegex(ValueError, "source clock"):
                p.packet(*args, 5, 1234, **dict(zip(("request", "source_tick", "source_time_us"), clock)))

    def test_motion_selects_real_timestamp_without_interpolation_and_holds_endpoint(self):
        times = [0,.1,.2]
        self.assertEqual(p.sample_index(times,.099,.2,10),0)
        self.assertEqual(p.sample_index(times,.1,.2,10),1)
        self.assertEqual(p.sample_index(times,.29,.2,10),2)
        self.assertEqual(p.sample_index(times,.301,.2,10),0)
        with self.assertRaises(ValueError):
            p.sample_index(times,float("nan"),.2,10)

    def test_native_body_and_board_sample_linkage(self):
        prepared = self.prepare()
        bits = list(struct.unpack("<832I", struct.pack("<832f",*([float(i%5==0) for i in range(16)]*52))))
        digest = hashlib.sha256(struct.pack("<832I",*bits)).hexdigest()
        frames = []
        records = []
        for time in (0,.3,.6333333253860474,.3):
            frames.append(dict(prepared["report"]["frames"][0],time=time,source_palette_sha256=digest))
            records.append(dict(phase="animation_frame",time=time,palette_bits=bits,palette_sha256=digest,active_bones=52))
        records.append(dict(phase="animation_pass",host_palette_writes=False,deterministic_repeat=True))
        prepared["report"]["frames"] = frames
        prepared["frame_index"] = 1
        samples,_ = p.prepare_samples(prepared,records,False,True)
        self.assertEqual(samples[0]["board_palette"],struct.pack("<832I",*bits)[48*64:52*64])
        self.assertEqual(samples[0]["model_bytes"],prepared["model_bytes"])
        with self.assertRaisesRegex(ValueError,"120 Hz"):
            p.prepare_samples(prepared,records,True,True)
        frames[1]["source_palette_sha256"] = "changed"
        with self.assertRaisesRegex(ValueError,"same original sample"):
            p.prepare_samples(prepared,records,False,True)

    def test_publisher_orders_interlocked_odd_body_then_even(self):
        memory = C.create_string_buffer(p.MAPPING_SIZE)
        publisher = p.Publisher.__new__(p.Publisher)
        publisher.packet_size = p.PACKET_SIZE
        publisher.view = C.addressof(memory)
        barriers = []
        def exchange(address,value):
            C.c_uint32.from_address(address).value=value
            barriers.append(bytes(memory))
            return 1
        publisher.exchange32 = exchange
        prepared = self.prepare()
        raw = p.packet(prepared, prepared["target"]["scene"], 5, 1234)
        publisher.publish(raw, 5)
        self.assertEqual(len(barriers), 3)
        self.assertEqual(struct.unpack_from("<I", barriers[0])[0], 9)
        self.assertEqual(barriers[0][4:4+p.PACKET_SIZE], bytes(p.PACKET_SIZE))
        self.assertEqual(struct.unpack_from("<I", barriers[1])[0], 9)
        self.assertEqual(barriers[1][4:4+p.PACKET_SIZE], raw)
        self.assertEqual(struct.unpack_from("<I",barriers[1],p.PACKET_SIZE+4)[0],10)
        self.assertEqual(struct.unpack_from("<I",barriers[2])[0],10)
        self.assertEqual(struct.unpack_from("<I", memory)[0], 10)
        self.assertEqual(struct.unpack_from("<I", memory, p.PACKET_SIZE+4)[0], 10)
        address = publisher.view
        publisher.clear()
        self.assertEqual(publisher.view, address)
        self.assertEqual(struct.unpack_from("<I", memory)[0], 0)
        publisher.publish(raw, 6)
        self.assertEqual(struct.unpack_from("<I", memory)[0], 12)

    def test_atomic_helper_failure_precedes_publisher_ownership(self):
        from unittest.mock import Mock,patch
        observer=SimpleNamespace(api=Mock())
        with patch.object(p,'load_atomic_store',side_effect=ValueError('missing helper')):
            with self.assertRaisesRegex(ValueError,'missing helper'):p.Publisher(observer)
        self.assertEqual(observer.api.mock_calls,[])

    def test_publisher_rejects_invalid_atomic_targets_before_any_store(self):
        from unittest.mock import Mock
        publisher=p.Publisher.__new__(p.Publisher)
        publisher.packet_size=p.PACKET_SIZE;publisher.view=0x10000;publisher.exchange32=Mock(return_value=1)
        for offset,value in ((4,1),(-4,1),(0,-1),(0,2**31),(0,True)):
            with self.subTest(offset=offset,value=value),self.assertRaises(ValueError):publisher.store(offset,value)
        publisher.view+=1
        with self.assertRaises(ValueError):publisher.store(0,1)
        publisher.exchange32.assert_not_called()
        publisher.view=0x10000;publisher.exchange32.return_value=0
        with self.assertRaisesRegex(ValueError,'store failed'):publisher.store(0,1)

    def test_stale_rig_link_and_edited_pose_fail(self):
        rig, report = fixture()
        with self.assertRaisesRegex(ValueError, "different target rig"):
            self.prepare(rig+b" ", report)
        report["frames"][0]["model_row_major"][1][12] = .5
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.prepare(rig, report)

    def test_wrong_rig_space_duplicate_nodes_and_nonrigid_pose_fail(self):
        rig, report = fixture()
        target = json.loads(rig)
        target["rig"]["hierarchies"][0]["flags"] = 0x7000
        changed = json.dumps(target).encode()
        report["inputs"][1]["sha256"] = hashlib.sha256(changed).hexdigest()
        with self.assertRaisesRegex(ValueError, "binding"):
            self.prepare(changed, report)
        rig, report = fixture()
        report["frames"][0]["model_row_major"][1][0] = 2
        with self.assertRaises(ValueError):
            self.prepare(rig, report)
        with self.assertRaises(ValueError):
            p.matrix_bytes([[float("nan")]*16]*32)

    def test_scene_identity_foreground_and_cutscene_gates(self):
        prepared = self.prepare()
        observer = SimpleNamespace(pid=17, created=123, exe=Path.cwd()/"gta_sa.exe")
        state = dict(prepared["target"]["scene"], protocol="GTATHAW2", capabilities=3,
                     profile_path=str(Path.cwd()/"thaw_gta"/"profile"), age_ms=0, flags=25,
                     sequence=1, position=[0, 0, 0], raw_gta_velocity=[0, 0, 0],
                     heading=0, health=100, time_step=1, clock_hour=12, clock_minute=0, clock_second=0,
                     controls_disabled_mask=0, cutscene_flags=0, ped_motion_flags=1, control_eligible=True)
        p.validate_scene(observer, state, prepared)
        for key, value in (("epoch", 4), ("age_ms", 101), ("flags", 17), ("cutscene_flags", 1)):
            changed = dict(state, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                p.validate_scene(observer, changed, prepared)

    def test_live_pointer_and_inverse_bind_changes_fail(self):
        prepared = self.prepare()
        memory = RigMemory(prepared)
        p.live_identity(memory, prepared)
        memory.words[0x20018] += 4
        with self.assertRaisesRegex(ValueError, "identity changed"):
            p.live_identity(memory, prepared)
        memory.words[0x20018] -= 4
        struct.pack_into("<f", memory.raw_binds, 64+48, .5)
        with self.assertRaisesRegex(ValueError, "inverse binds changed"):
            p.live_identity(memory, prepared)

    def test_identical_bind_cache_retains_every_read_and_exact_packet(self):
        prepared = self.prepare()
        memory = RigMemory(prepared)
        before = p.packet(prepared, prepared['target']['scene'], 5, 1234)
        with patch.object(p, 'matrix_summary', wraps=p.matrix_summary) as summary:
            p.live_identity(memory, prepared)
            self.assertEqual(summary.call_count, 32)
            reads = list(memory.reads)
            summary.reset_mock(); memory.reads.clear()
            p.live_identity(memory, prepared)
            summary.assert_not_called()
        self.assertEqual(memory.reads, reads)
        self.assertEqual(reads.count((0x20018, 4)), 2)
        self.assertIn((0x90000, 512), reads)
        self.assertIn((0xC0000, 2048), reads)
        self.assertEqual(p.packet(prepared, prepared['target']['scene'], 5, 1234), before)

    def test_changed_raw_flags_revalidate_and_replace_only_one_cache_entry(self):
        prepared = self.prepare()
        memory = RigMemory(prepared)
        p.live_identity(memory, prepared)
        first = prepared['_live_bind_validation']
        before = p.packet(prepared, prepared['target']['scene'], 5, 1234)
        struct.pack_into('<I', memory.raw_binds, 12, 3)
        with patch.object(p, 'matrix_summary', wraps=p.matrix_summary) as summary:
            p.live_identity(memory, prepared)
            self.assertEqual(summary.call_count, 32)
            p.live_identity(memory, prepared)
            self.assertEqual(summary.call_count, 32)
            memory.raw_binds[:] = first[0]
            p.live_identity(memory, prepared)
            self.assertEqual(summary.call_count, 64)
        self.assertEqual(prepared['_live_bind_validation'], first)
        self.assertEqual(p.packet(prepared, prepared['target']['scene'], 5, 1234), before)

    def test_changed_nonfinite_singular_or_different_binds_never_reuse_cache(self):
        prepared = self.prepare()
        memory = RigMemory(prepared)
        p.live_identity(memory, prepared)
        cached = prepared['_live_bind_validation']
        for offset, value, reason in ((0, float('nan'), 'nonfinite'),
                                      (0, 0., 'singular'),
                                      (48, .5, 'inverse binds changed')):
            with self.subTest(reason=reason):
                memory.raw_binds[:] = cached[0]
                struct.pack_into('<f', memory.raw_binds, offset, value)
                with self.assertRaisesRegex(ValueError, reason):
                    p.live_identity(memory, prepared)
                self.assertIs(prepared['_live_bind_validation'], cached)
        memory.raw_binds[:] = cached[0]
        changed = bytearray(prepared['bind_bytes'])
        struct.pack_into('<f', changed, 48, .25)
        prepared['bind_bytes'] = bytes(changed)
        with self.assertRaisesRegex(ValueError, 'inverse binds changed'):
            p.live_identity(memory, prepared)
        self.assertIs(prepared['_live_bind_validation'], cached)

    def test_node_and_final_clump_guards_apply_to_cache_hits_and_misses(self):
        prepared = self.prepare()
        memory = RigMemory(prepared)
        memory.fail_final_clump = True
        with self.assertRaisesRegex(ValueError, 'clump changed during'):
            p.live_identity(memory, prepared)
        self.assertNotIn('_live_bind_validation', prepared)
        memory.fail_final_clump = False
        p.live_identity(memory, prepared)
        cached = prepared['_live_bind_validation']
        for change_raw in (False, True):
            memory.reads.clear(); memory.fail_final_clump = True
            if change_raw:
                struct.pack_into('<I', memory.raw_binds, 12, 3)
            with self.assertRaisesRegex(ValueError, 'clump changed during'):
                p.live_identity(memory, prepared)
            self.assertIs(prepared['_live_bind_validation'], cached)
        memory.fail_final_clump = False
        memory.nodes = bytes(512)
        with self.assertRaisesRegex(ValueError, 'node identity changed'):
            p.live_identity(memory, prepared)
        self.assertIs(prepared['_live_bind_validation'], cached)


if __name__ == "__main__":
    unittest.main()
