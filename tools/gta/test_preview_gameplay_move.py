import copy
import ctypes as C
import math
import struct
import unittest
from types import SimpleNamespace
import preview_gameplay_move as m
from preview_pose import Publisher
from test_atomic_store import fake_store32


def capture():
    bits = list(struct.unpack("<16I", struct.pack("<16f", 1, 0, 0, 0, 0, 1, 0, 0,
                                               0, 0, 1, 0, 0, 0, 0, 1)))
    frames = []
    for i in range(3):
        frame = 16+i
        frames.append(dict(frame=frame, position=[i*5., i*3., -i*2.], velocity=[100., 60., -40.] if i else [0, 0, 0],
                           state=1 if i else 0, mode=0, walk_state=0, walking_gate=0, bailing=False, actor_matrix_bits=bits,
                           clock=dict(native_frame_count=frame, native_vblank_count=frame, frame_open=False,
                                      native_time_scale=1, native_next_time_scale=1,
                                      native_seconds_per_frame=1/60, native_gameplay_seconds_per_frame=1/60)))
    return dict(format="stock-gameplay-capture-v1", complete=True, sample_hz=60, frames=frames)


def state():
    return dict(protocol="GTATHAW2", pid=17, creation_100ns=123, ped_address=0x20000,
                epoch=3, area=0, ped_area=0, control_eligible=True, grounded=True,
                flags=25, age_ms=1, position=[2240., -1261., 23.], heading=.4,
                raw_gta_velocity=[0., 0., 0.])


class TrajectoryTests(unittest.TestCase):
    def test_bounded_source_adjustment_preserves_wire_pose_and_buffer_identity(self):
        s=state()
        base=dict(m.prepare(capture(),16)['samples'][1],source_adjustment=True,
                  position=[2236.02661,-1289.96399,26.1628857],
                  velocity=[-.0235399567,.0731444508,-.0147885839])
        for contact,context in ((m.GROUND,dict(source_state=0,source_bailing=1)),
                                (m.AIR,dict(source_state=1,source_bailing=1)),
                                (m.RAIL,dict(source_state=4)),
                                (m.CONSTRAINT,dict(source_mode=1,source_walk_state=3))):
            for buffered in (False,True):
                sample=dict(base,contact=contact,**context)
                raw=m.packet(s,m.scene(s),99,5,1000,0,sample,7,interactive=True,buffered=buffered)
                self.assertEqual(len(raw),144)
                self.assertEqual(raw[72:84],struct.pack('<3f',*sample['position']))
                self.assertEqual(raw[88:100],struct.pack('<3f',*sample['velocity']))
                self.assertEqual(struct.unpack_from('<I',raw,100)[0],
                    contact|m.SOURCE_ADJUSTMENT_FLAG|(m.BUFFERING_CONTACT_FLAG if buffered else 0))
        invalid=({'source_adjustment':1}, {'source_tick':0,'source_time_us':0},
                 {'contact':m.RAIL,'source_state':4,'rail_correction':True},
                 {'velocity':[0.,0.,0.],'ceiling_correction':True},
                 {'contact':m.GROUND,'source_state':0,'source_bailing':1,'body_correction':True},
                 {'velocity':[0.,0.,1.21]}, {'velocity':[float('nan'),0.,0.]},
                 {'position':[float('inf'),0.,0.]}, {'source_state':0})
        for change in invalid:
            with self.subTest(change=change),self.assertRaises(ValueError):
                m.packet(s,m.scene(s),99,5,1000,0,dict(base,**change),7,interactive=True)
        for duration,interactive in ((5000,True),(0,False)):
            with self.assertRaises(ValueError):
                m.packet(s,m.scene(s),99,5,1000,duration,base,7,interactive=interactive)

    def test_ceiling_witness_flag_keeps_atomic_air_context_and_buffering(self):
        s=state()
        sample=dict(m.prepare(capture(),16)['samples'][1],velocity=[.2,.1,0.],ceiling_correction=True)
        for buffered in (False,True):
            raw=m.packet(s,m.scene(s),99,5,1000,0,sample,7,interactive=True,buffered=buffered)
            self.assertEqual(len(raw),144)
            self.assertEqual(struct.unpack_from('<I',raw,100)[0],m.AIR|m.CEILING_CORRECTION_FLAG|
                             (m.BUFFERING_CONTACT_FLAG if buffered else 0))
        for change in ({'source_mode':1},{'source_state':0},{'source_bailing':1},{'contact':m.RAIL},
                       {'ceiling_correction':1},{'source_tick':0,'source_time_us':0},
                       {'velocity':[.2,.1,.001]},{'rail_correction':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                m.packet(s,m.scene(s),99,5,1000,0,dict(sample,**change),7,interactive=True)
        with self.assertRaises(ValueError):
            m.packet(s,m.scene(s),99,5,1000,5000,sample,7,interactive=True)

    def test_ceiling_projection_residue_preserves_source_wire_velocity(self):
        s=state()
        sample=dict(m.prepare(capture(),16)['samples'][1],ceiling_correction=True)
        # Actual raw GTA velocities from wall-release sessions 50, 55 and 59.
        for velocity in ([0.,.3656631997070312,3.1005859375e-8],
                         [0.,-.36540141723632813,3.1005859375e-8],
                         [0.,.2843877575683594,1.55029296875e-8]):
            source=dict(sample,velocity=velocity)
            for sign in (1,-1):
                source['velocity']=[velocity[0],velocity[1],sign*velocity[2]]
                raw=m.packet(s,m.scene(s),99,5,1000,0,source,7,interactive=True)
                self.assertEqual(raw[88:100],struct.pack('<3f',*source['velocity']))
                self.assertNotEqual(struct.unpack_from('<f',raw,96)[0],0.)
        for vertical in (m.CEILING_MAX_RAW_VERTICAL_SPEED*1.01,-m.CEILING_MAX_RAW_VERTICAL_SPEED*1.01,
                         .00001,-.00001,.01,-.01):
            with self.subTest(vertical=vertical),self.assertRaises(ValueError):
                m.packet(s,m.scene(s),99,5,1000,0,dict(sample,velocity=[0.,.2,vertical]),7,interactive=True)

    def test_explicit_rail_correction_retains_wire_size_and_buffer_identity(self):
        s=state()
        sample=dict(m.prepare(capture(),16)['samples'][1],contact=m.RAIL,source_state=4,rail_correction=True)
        raw=m.packet(s,m.scene(s),99,5,1000,0,sample,7,interactive=True)
        self.assertEqual(len(raw),144)
        self.assertEqual(struct.unpack_from('<I',raw,100)[0],m.RAIL|m.RAIL_CORRECTION_FLAG)
        buffered=m.packet(s,m.scene(s),99,5,1000,0,sample,7,interactive=True,buffered=True)
        self.assertEqual(struct.unpack_from('<I',buffered,100)[0],m.RAIL|m.RAIL_CORRECTION_FLAG|m.BUFFERING_CONTACT_FLAG)
        for change,duration,interactive in (({},5000,True),({},5000,False),
                ({'contact':m.GROUND},0,True),({'contact':m.AIR},0,True),
                ({'contact':m.CONSTRAINT},0,True),({'source_tick':0,'source_time_us':0},0,True),
                ({'rail_correction':1},0,True)):
            with self.assertRaises(ValueError):
                m.packet(s,m.scene(s),99,5,1000,duration,dict(sample,**change),7,interactive=interactive)

    def test_sloped_ceiling_witness_preserves_actual_projected_velocity(self):
        s=state()
        sample=dict(m.prepare(capture(),16)['samples'][1],source_tick=7191,source_time_us=119850000,
                    position=[2751.85901875,-1425.8696796875,28.61011265541992],
                    velocity=[1.10250261575e-5,.03641297894287109,.008909984245300293],
                    ceiling_correction=True,ceiling_slope_correction=True)
        packets=[]
        for buffered in (False,True):
            raw=m.packet(s,m.scene(s),99,5,1000,0,sample,7,interactive=True,buffered=buffered)
            packets.append(raw)
            self.assertEqual(len(raw),144)
            self.assertEqual(raw[72:84],struct.pack('<3f',*sample['position']))
            self.assertEqual(raw[88:100],struct.pack('<3f',*sample['velocity']))
            self.assertEqual(struct.unpack_from('<I',raw,100)[0],m.AIR|m.CEILING_CORRECTION_FLAG|
                             m.CEILING_SLOPE_CORRECTION_FLAG|(m.BUFFERING_CONTACT_FLAG if buffered else 0))
        self.assertEqual(packets[0][:100]+packets[0][104:],packets[1][:100]+packets[1][104:])
        for change in ({'ceiling_correction':False},{'ceiling_correction':1},
                       {'ceiling_slope_correction':False},{'ceiling_slope_correction':1},
                       {'source_mode':1},{'source_state':0},{'source_bailing':2},
                       {'contact':m.GROUND},{'rail_correction':True},
                       {'source_tick':0,'source_time_us':0},
                       {'velocity':[0.,0.,1.21]},{'velocity':[0.,0.,float('nan')]}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                m.packet(s,m.scene(s),99,5,1000,0,dict(sample,**change),7,interactive=True)
        for duration,interactive in ((5000,True),(0,False)):
            with self.assertRaises(ValueError):
                m.packet(s,m.scene(s),99,5,1000,duration,sample,7,interactive=interactive)

    def test_bailing_inclined_ceiling_keeps_captured_motion_and_state(self):
        s=state()
        sample=dict(m.prepare(capture(),16)['samples'][1], source_tick=1220, source_time_us=20333333,
                    position=[2241.305921875,-1589.607803125,14.634724262780763],
                    velocity=[.008648925567626952,.1803730987548828,.006910725463867187],
                    source_bailing=1, ceiling_correction=True, ceiling_slope_correction=True)
        raw=m.packet(s,m.scene(s),99,5,1000,0,sample,7,interactive=True)
        self.assertEqual(raw[72:84],struct.pack('<3f',*sample['position']))
        self.assertEqual(raw[88:100],struct.pack('<3f',*sample['velocity']))
        self.assertEqual(struct.unpack_from('<4I',raw,128),(0,1,0,1))
        with self.assertRaises(ValueError):
            m.packet(s,m.scene(s),99,5,1000,0,
                     dict(sample,ceiling_slope_correction=False,velocity=[0,0,0]),7,interactive=True)

    def test_body_witness_preserves_ground_bail_and_cannot_mix_corrections(self):
        s=state()
        sample=dict(m.prepare(capture(),16)['samples'][1],source_state=0,source_bailing=1,
                    contact=m.GROUND,body_correction=True,position=[2305.55273,-1060.8761,50.44241])
        packets=[m.packet(s,m.scene(s),99,5,1000,0,sample,7,interactive=True,buffered=b)
                 for b in (False,True)]
        self.assertEqual(packets[0][:100]+packets[0][104:],packets[1][:100]+packets[1][104:])
        self.assertEqual(struct.unpack_from('<I',packets[0],100)[0],m.GROUND|m.BODY_CORRECTION_FLAG)
        self.assertEqual(struct.unpack_from('<I',packets[1],100)[0],
                         m.GROUND|m.BODY_CORRECTION_FLAG|m.BUFFERING_CONTACT_FLAG)
        self.assertEqual(packets[0][72:84],struct.pack('<3f',*sample['position']))
        self.assertEqual(packets[0][88:100],struct.pack('<3f',*sample['velocity']))
        self.assertEqual(struct.unpack_from('<4I',packets[0],128),(0,0,0,1))
        for change in ({'body_correction':1},{'source_bailing':0},{'source_mode':1},
                       {'source_state':1,'contact':m.AIR},{'contact':m.RAIL,'source_state':4},
                       {'ceiling_correction':True},{'ceiling_slope_correction':True},
                       {'rail_correction':True},{'source_tick':0,'source_time_us':0}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                m.packet(s,m.scene(s),99,5,1000,0,dict(sample,**change),7,interactive=True)
        for duration,interactive in ((5000,True),(0,False)):
            with self.assertRaises(ValueError):
                m.packet(s,m.scene(s),99,5,1000,duration,sample,7,interactive=interactive)

    def test_original_ground_entry_clock_physical_axes(self):
        source = capture()
        prepared = m.prepare(source, 16)
        self.assertEqual(prepared["duration_ms"], 84)
        zero, one = prepared["samples"][:2]
        self.assertEqual((zero["source_frame"], zero["source_tick"], zero["source_time_us"]), (16, 0, 0))
        self.assertEqual((one["source_tick"], one["source_time_us"], one["contact"]), (1, 16667, m.AIR))
        self.assertEqual(zero["contact"], m.GROUND)
        for actual, expected in zip(one["position"], [.127, .0508, .0762]):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(one["velocity"], [2.54/50, 1.016/50, 1.524/50]):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(source, capture()) # Conversion never mutates original data.

    def test_translation_and_yaw_do_not_rotate_world_velocity(self):
        sample = m.prepare(capture(), 16)["samples"][1]
        initial = state()
        initial["heading"] = math.pi/2
        anchored = m.anchor(sample, initial)
        self.assertEqual(anchored["velocity"], sample["velocity"])
        self.assertAlmostEqual(anchored["heading"], math.pi/2)
        for actual, expected in zip(anchored["position"], [2240.127, -1260.9492, 23.0762]):
            self.assertAlmostEqual(actual, expected)

    def test_wire_offsets_and_air_scene(self):
        s = state(); identity = m.scene(s)
        sample = m.anchor(m.prepare(capture(), 16)["samples"][1], s)
        raw = m.packet(dict(s, grounded=False), identity, 99, 5, 1000, 5000, sample, 7)
        self.assertEqual(len(raw), 144)
        self.assertEqual(struct.unpack_from("<8sII", raw), (b"GTAMOVE3", 3, 144))
        self.assertEqual(struct.unpack_from("<I3Q", raw, 100), (m.AIR, 1, 16667, 7))
        self.assertAlmostEqual(struct.unpack_from("<f", raw, 96)[0], 1.524/50)
        self.assertEqual(struct.unpack_from('<4I', raw, 128), (0, 1, 0, 0))

    def test_reject_ineligible_stale_or_new_scene(self):
        s = state(); identity = m.scene(s)
        sample = m.anchor(m.prepare(capture(), 16)["samples"][0], s)
        for key, value in (("epoch", 4), ("area", 1), ("control_eligible", False),
                           ("age_ms", 101), ("age_ms", -1), ("flags", 17)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                m.packet(dict(s, **{key: value}), identity, 99, 1, 1000, 5000, sample, 7)

    def test_distinct_rail_contact_preserves_clock_velocity_and_wire_layout(self):
        s = state()
        sample = m.anchor(m.prepare(capture(), 16)['samples'][1], s)
        rail = dict(sample, contact=m.RAIL, source_state=4)
        raw = m.packet(s, m.scene(s), 99, 5, 1000, 5000, rail, 7)
        air = m.packet(s, m.scene(s), 99, 5, 1000, 5000, sample, 7)
        self.assertEqual(struct.unpack_from('<I3Q', raw, 100), (4, 1, 16667, 7))
        self.assertEqual(raw[:100]+raw[104:128], air[:100]+air[104:128])
        self.assertEqual(struct.unpack_from('<4I', raw, 128), (0, 4, 0, 0))
        for contact in (True, 3, 6, 4.0):
            with self.assertRaises(ValueError):
                m.packet(s, m.scene(s), 99, 5, 1000, 5000, dict(rail, contact=contact), 7)

    def test_packet_bounds_do_not_clamp(self):
        s = state(); identity = m.scene(s)
        sample = m.anchor(m.prepare(capture(), 16)["samples"][0], s)
        for key, value in (("velocity", [0, 0, .4001]), ("velocity", [float("nan"), 0, 0]),
                           ("position", [0, 0, float("inf")]), ("heading", float("nan")),
                           ("source_tick", 1), ("source_time_us", 1), ("contact", 0)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                m.packet(s, identity, 99, 1, 1000, 5000, dict(sample, **{key: value}), 7)
        for duration, generation in ((5001, 7), (0, 7), (5000, 0)):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                m.packet(s, identity, 99, 1, 1000, duration, sample, generation)

    def test_only_real_stationary_reference_and_contiguous_source(self):
        for key, value in (("state", 1), ("velocity", [0, 1, 0]), ("mode", 1), ("walking_gate", 1)):
            source = capture(); source["frames"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): m.prepare(source, 16)
        for mutate in (lambda x: x["frames"][1].update(frame=18),
                       lambda x: x["frames"][1].update(state=2),
                       lambda x: x["frames"][1]["clock"].update(native_time_scale=.5),
                       lambda x: x["frames"][1]["clock"].update(frame_open=True),
                       lambda x: x["frames"][1]["clock"].update(native_gameplay_seconds_per_frame=.02),
                       lambda x: x["frames"][1].update(position=[1000, 0, 0]),
                       lambda x: x["frames"][1].update(velocity=[0, 1000, 0])):
            source = capture(); mutate(source)
            with self.assertRaises(ValueError): m.prepare(source, 16)
        with self.assertRaises(ValueError): m.prepare(capture(), 17)

    def test_source_heading_and_shared_memory_boundary(self):
        source = capture(); matrix = [0., 0., -1., 0., 0., 1., 0., 0., 1., 0., 0., 0., 0., 0., 0., 1.]
        source["frames"][1]["actor_matrix_bits"] = list(struct.unpack("<16I", struct.pack("<16f", *matrix)))
        sample = m.prepare(source, 16)["samples"][1]
        self.assertAlmostEqual(sample["heading"], math.pi/2)
        s = state(); raw = m.packet(s, m.scene(s), 99, 5, 1000, 5000, m.anchor(sample, s), 7)
        memory = C.create_string_buffer(160)
        C.memset(C.addressof(memory)+152, 0xAC, 8)
        writer = Publisher.__new__(Publisher)
        writer.packet_size = 144; writer.view = C.addressof(memory)
        writer.exchange32 = fake_store32
        writer.publish(raw, 5)
        self.assertEqual(bytes(memory)[4:148], raw)
        self.assertEqual(struct.unpack_from("<I", memory, 148)[0], 10)
        self.assertEqual(bytes(memory)[152:], b"\xAC"*8)

    def test_interactive_duration_requires_explicit_opt_in(self):
        s = state(); sample = m.anchor(m.prepare(capture(), 16)["samples"][0], s)
        with self.assertRaises(ValueError): m.packet(s, m.scene(s), 99, 1, 1000, 30000, sample, 7)
        raw = m.packet(s, m.scene(s), 99, 1, 1000, 30000, sample, 7, interactive=True)
        self.assertEqual(struct.unpack_from("<I", raw, 68)[0], 30000)
        with self.assertRaises(ValueError):
            m.packet(s, m.scene(s), 99, 1, 1000, 30001, sample, 7, interactive=True)
        flagged = dict(s, flags=s["flags"] | 64 | 128)
        self.assertEqual(m.scene(flagged), m.scene(s))

    def test_original_max_stats_jump_exceeds_old_proof_speed(self):
        s = state(); sample = m.anchor(m.prepare(capture(), 16)["samples"][1], s)
        sample['velocity'] = m.thaw_velocity_to_gta([692.795593, 451.298340, -31.233828])
        with self.assertRaises(ValueError): m.packet(s, m.scene(s), 99, 2, 1000, 5000, sample, 7)
        m.packet(s, m.scene(s), 99, 2, 1000, 5000, sample, 7, interactive=True)
        sample['velocity'] = [1.21, 0, 0]
        with self.assertRaises(ValueError):
            m.packet(s, m.scene(s), 99, 2, 1000, 5000, sample, 7, interactive=True)

    def test_continuous_session_keeps_uint64_clock_and_explicit_opt_in(self):
        s = state(); sample = m.anchor(m.prepare(capture(), 16)["samples"][1], s)
        sample.update(source_tick=432000, source_time_us=7200000000, position=[-2500, 2200, 15])
        with self.assertRaises(ValueError):
            m.packet(s, m.scene(s), 99, 432001, 7201000, m.CONTINUOUS_DURATION_MS, sample, 7)
        raw = m.packet(s, m.scene(s), 99, 432001, 7201000, m.CONTINUOUS_DURATION_MS, sample, 7, interactive=True)
        self.assertEqual(struct.unpack_from('<I', raw, 68)[0], 0)
        self.assertEqual(struct.unpack_from('<3Q', raw, 104), (432000, 7200000000, 7))
        for key, value in (("source_time_us", 2**64), ("source_tick", 2**64),
                           ("source_time_us", -1), ("velocity", [1.21, 0, 0]),
                           ("position", [100001, 0, 0])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                m.packet(s, m.scene(s), 99, 432001, 7201000, 0, dict(sample, **{key: value}), 7, interactive=True)
        with self.assertRaises(ValueError):
            m.packet(s, m.scene(s), 99, 432001, 7201000, 30000, sample, 7, interactive=True)

    def test_buffering_flag_is_explicit_continuous_and_preserves_source_sample(self):
        s = state(); sample = m.anchor(m.prepare(capture(), 16)["samples"][1], s)
        for contact in (m.GROUND, m.AIR, m.RAIL, m.CONSTRAINT):
            source = dict(sample, contact=contact, source_state={m.GROUND:0,m.AIR:1,m.RAIL:4,m.CONSTRAINT:2}[contact])
            normal = m.packet(s, m.scene(s), 99, 5, 1000, 0, source, 7, interactive=True)
            buffered = m.packet(s, m.scene(s), 99, 5, 1000, 0, source, 7, interactive=True, buffered=True)
            self.assertEqual(struct.unpack_from('<I', buffered, 100)[0], contact | m.BUFFERING_CONTACT_FLAG)
            self.assertEqual(normal[:100]+normal[104:], buffered[:100]+buffered[104:])
            self.assertEqual(source['contact'], contact)
        for duration, interactive, buffered in ((5000, True, True), (0, False, True),
                                                 (30000, True, True), (0, True, 1)):
            with self.subTest(duration=duration, interactive=interactive, buffered=buffered), self.assertRaises(ValueError):
                m.packet(s, m.scene(s), 99, 5, 1000, duration, sample, 7, interactive=interactive, buffered=buffered)

    def test_original_constraints_preserve_source_velocity_and_named_contact(self):
        s = state(); sample = m.anchor(m.prepare(capture(), 16)['samples'][1], s)
        contacts = ((0, 2, 0, 'wall'), (0, 3, 0, 'lip'), (0, 5, 0, 'wallplant'),
                    (0, 6, 0, 'stall'), (0, 7, 0, 'tiretap'), (1, 1, 3, 'hang'),
                    (1, 0, 4, 'ladder'), (1, 0, 6, 'anim_wait'),
                    (1, 0, 5, 'walk_state_5'), (1, 0, 7, 'walk_state_7'))
        for mode, skating, walking, name in contacts:
            with self.subTest(contact=name):
                self.assertEqual(m.source_contact_code(name), m.CONSTRAINT)
                constrained = dict(sample, contact=m.source_contact_code(name),
                                   **m.source_context(dict(mode=mode, state=skating, walk_state=walking,
                                                           walking_gate=0, bailing=False)))
                raw = m.packet(s, m.scene(s), 99, 5, 1000, 0, constrained, 7, interactive=True)
                self.assertEqual(struct.unpack_from('<I3Q', raw, 100), (5, 1, 16667, 7))
                self.assertEqual(constrained['position'], sample['position'])
                self.assertEqual(constrained['velocity'], sample['velocity'])
                self.assertEqual(struct.unpack_from('<4I', raw, 128), (mode, skating, walking, 0))
        self.assertEqual([m.source_contact_code(name) for name in ('ground', 'air', 'rail')], [1, 2, 4])
        for invalid in (None, True, [], 'unknown', 'bail'):
            with self.subTest(contact=invalid), self.assertRaises(ValueError):
                m.source_contact_code(invalid)

    def test_context_requires_completed_original_state_and_rejects_contact_mismatch(self):
        s = state(); sample = m.prepare(capture(),16)['samples'][1]
        context = dict(mode=1, state=4, walk_state=3, bailing=True, walking_gate=0)
        hanging = dict(sample, contact=m.CONSTRAINT, **m.source_context(context))
        raw = m.packet(s,m.scene(s),99,5,1000,0,hanging,7,interactive=True)
        self.assertEqual(struct.unpack_from('<4I',raw,128),(1,4,3,1))
        # Walking uses walk_state even if the inactive skating state still says rail.
        for change in (dict(contact=m.RAIL), dict(source_mode=2), dict(source_mode=True),
                       dict(source_state=8), dict(source_walk_state=8), dict(source_bailing=2),
                       dict(source_bailing=False), dict(source_bailing=None),
                       dict(source_tick=0,source_time_us=0)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                m.packet(s,m.scene(s),99,5,1000,0,dict(hanging,**change),7,interactive=True)
        for change in (dict(walking_gate=1), dict(walking_gate=False), dict(mode=2),
                       dict(bailing=1), dict(bailing=None), dict(walk_state=8), dict(state=True)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                m.source_context(dict(context,**change))
        del hanging['source_bailing']
        with self.assertRaises(ValueError):
            m.packet(s,m.scene(s),99,5,1000,0,hanging,7,interactive=True)


if __name__ == "__main__": unittest.main()
