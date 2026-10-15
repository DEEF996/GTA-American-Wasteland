import copy
import hashlib
import math
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from probe_rail_correction import RailCorrection, proves_correction, proves_corner_correction, proves_native_projection
import probe_rail_correction as correction
from probe_world_rails import RailStream


def evidence():
    # Original rail-bail replay: the next linked node reprojects the scripted
    # sideways displacement; the hidden board keeps its proven stock bail ID.
    old=dict(frame=119,position=[90003.5625,909.8189086914062,48586.2421875],
             state=4,mode=0,walk_state=0,walking_gate=0,board_visible=False,detached_board_id=0x884ef81b)
    new=dict(old,frame=120,position=[89931.0625,909.8189086914062,48570.671875])
    before=dict(rail_node=0x180000,rail_manager=0x200000,rail_name=4040001662,
                source_chain='chain_a',position=[89925.5625,909.8179321289062,48583.90625],
                links=[0x180060,0x1800c0])
    after=dict(before,rail_node=0x180060,rail_name=1774508484,
               position=[89925.5625,909.8179321289062,47953.984375],links=[0x180000,0x180120])
    return old,new,before,after


def corner_evidence():
    # Final04 completed stock frames2517/2518, real curved GTA ledge nodes120/316/256.
    old=dict(frame=2517,position=[76890.296875,747.7725219726562,46587.69140625],
             state=4,mode=0,walk_state=0,walking_gate=0,bailing=True,
             board_visible=False,detached_board_id=0x884ef81b)
    new=dict(old,frame=2518,position=[76887.6640625,748.9552001953125,46529.68359375])
    before=dict(rail_node=0x180000,rail_manager=0x200000,rail_name=1278594654,
                source_chain='gta_cached_8f4e23fde048351866f49507',
                position=[76572.9609375,748.9542236328125,46625.5546875],links=[0x180060,0])
    after=dict(before,rail_node=0x180060,rail_name=3528547325,
               position=[76868.8515625,748.9542236328125,46530.51171875],links=[0x1800c0,0x180000])
    following=dict(before,rail_node=0x1800c0,rail_name=2773904235,
                   position=[77209.03125,748.9542236328125,46469.609375],links=[0,0x180060])
    return old,new,before,after,following


def projection_evidence():
    old=dict(frame=3046,position=[90009.5625,909.8189086914062,50474.43359375],
             previous_position=[90007.0625,909.8189086914062,50474.43359375],
             velocity=[0.,0.,521.7738647460938],state=4,mode=0,walk_state=0,
             walking_gate=0,bailing=True,board_visible=False,detached_board_id=0x884ef81b)
    new=dict(old,frame=3047,position=[89928.0625,909.8189086914062,50488.69921875],
             previous_position=[89925.5625,909.8189086914062,50488.69921875])
    before=dict(rail_node=0x180000,rail_manager=0x200000,rail_name=506601916,
                source_chain='gta_cached_cc1ed031c60813989d651977',
                position=[89925.5625,909.8179321289062,49843.75],links=[0x180060,0])
    after=dict(before,rail_node=0x180060,rail_name=1765085482,
               position=[89925.5625,909.8179321289062,50473.671875],links=[0x1800c0,0x180000])
    following=dict(before,rail_node=0x1800c0,rail_name=902973027,
                   position=[89925.5625,909.8179321289062,51103.59375],links=[0,0x180060])
    return old,new,before,after,following


def reverse_projection_evidence():
    # Trace981/982: native projection precedes unchanged BodyPush; scripts then
    # add2.5in. Exact retained graph nodes256 ->215 with old next764.
    old=dict(frame=981,position=[90236.1015625,909.8189086914062,49218.55078125],
             previous_position=[90233.6015625,909.8189086914062,49218.55078125],
             velocity=[-0.,-0.,-185.79473876953125],state=4,mode=0,walk_state=0,
             walking_gate=0,bailing=True,board_visible=False,detached_board_id=0x884ef81b)
    new=dict(old,frame=982,position=[90152.6015625,909.8189086914062,49209.56640625],
             previous_position=[90150.1015625,909.8189086914062,49209.56640625])
    before=dict(rail_node=0x180060,rail_manager=0x200000,rail_name=1237911006,
                source_chain='gta_cached_9bfb4478a22092c9f5a09d28',
                position=[90150.1015625,909.8179321289062,49213.828125],links=[0x1800c0,0x180000])
    after=dict(before,rail_node=0x180000,rail_name=1053701448,
               position=[90150.1015625,909.8179321289062,48583.90625],links=[0x180060,0])
    following=dict(before,rail_node=0x1800c0,rail_name=3648395343,
                   position=[90150.1015625,909.8179321289062,49843.75],links=[0,0x180060])
    return old,new,before,after,following


class RailCorrectionTests(unittest.TestCase):
    def test_recorded_reverse_projection_uses_original_old_endpoint(self):
        rows=reverse_projection_evidence()
        self.assertFalse(proves_correction(*rows[:4],0x884ef81b))
        self.assertFalse(proves_corner_correction(*rows,0x884ef81b))
        self.assertFalse(proves_native_projection(*rows,0x884ef81b))
        self.assertTrue(proves_native_projection(*rows,0x884ef81b,reverse=True))
        old,new,before,after,following=rows
        overshoot=math.dist(old['position'],following['position'])-math.dist(following['position'],before['position'])
        self.assertGreater(overshoot,0)
        expected=before['position'][2]+new['velocity'][2]/60-overshoot
        self.assertLess(abs(expected-new['previous_position'][2]),.001)
        self.assertGreater(abs(old['position'][0]-before['position'][0])*.0254,2.02)
        self.assertEqual(correction.MAX_ADJUSTMENT,2.)

    def test_reverse_projection_keeps_owned_topology_and_same_frame_history_guards(self):
        for index,key,value in ((0,'bailing',False),(1,'board_visible',True),(1,'frame',983),
                (1,'state',0),(0,'mode',1),(1,'detached_board_id',0),
                (2,'links',[0x1800c0,0]),(3,'links',[0,0]),(4,'links',[0,0]),
                (4,'dynamic_vehicle',True),(4,'rail_manager',0x300000),(4,'source_chain','foreign'),
                (0,'velocity',[0.,0.,185.79473876953125]),
                (1,'previous_position',[90150.1015625,909.8189,49210.]),
                (1,'position',[90153.,909.8189,49209.5664]),
                (0,'position',[90300.,909.8189,49218.5508]),
                (4,'position',[90150.1015625,910.,49843.75])):
            rows=list(reverse_projection_evidence());rows[index]=dict(rows[index],**{key:value})
            with self.subTest(index=index,key=key):
                self.assertFalse(proves_native_projection(*rows,0x884ef81b,reverse=True))

    def test_reverse_reader_validates_old_next_once_and_preserves_pending_frame(self):
        old,new,before,after,following=reverse_projection_evidence();current=[before]
        def read(pointer,size):
            node=next(row for row in (before,after,following) if row['rail_node']==pointer)
            data=bytearray(size)
            struct.pack_into('<3f',data,0x20,*node['position'])
            struct.pack_into('<I',data,0x44,node['rail_name'])
            struct.pack_into('<2I',data,0x50,*node['links'])
            return bytes(data)
        actor=0x300000
        system=SimpleNamespace(word=lambda p:0x6989e8 if p==actor else 0,read=read)
        rails=SimpleNamespace(observe=lambda sample:current[0],log=Mock(),
                              adjacent_node=Mock(return_value=following))
        reader=RailCorrection(rails,system,dict(actor=actor))
        with patch.object(reader,'_verify_projection') as verify:
            self.assertFalse(reader.update(old));observation=reader.observation
            current[0]=after
            self.assertTrue(reader.update(new));self.assertTrue(reader.update(copy.deepcopy(new)))
            verify.assert_called_once_with(new,reader.observation)
            rails.adjacent_node.assert_called_once_with(observation,following['rail_node'])
            self.assertTrue(rails.log.call_args.kwargs['reverse_projection'])
            self.assertEqual(rails.log.call_count,1)

    def test_recorded_projection_uses_exact_native_overshoot_not_larger_generic_tolerance(self):
        rows=projection_evidence()
        self.assertFalse(proves_correction(*rows[:4],0x884ef81b))
        self.assertFalse(proves_corner_correction(*rows,0x884ef81b))
        self.assertTrue(proves_native_projection(*rows,0x884ef81b))
        old,new,before,after,following=rows
        overshoot=math.dist(old['position'],before['position'])-math.dist(before['position'],after['position'])
        expected=after['position'][2]+overshoot+old['velocity'][2]/60
        self.assertLess(abs(expected-new['previous_position'][2]),.0001)
        self.assertGreater(math.dist(old['position'],new['position'])*.0254,2.)
        self.assertEqual(correction.MAX_ADJUSTMENT,2.)

    def test_native_projection_rejects_wrong_topology_state_direction_and_script_shift(self):
        for index,key,value in ((0,'bailing',False),(1,'bailing',False),
                (0,'board_visible',True),(1,'detached_board_id',1),(1,'frame',3048),
                (0,'state',0),(1,'mode',1),
                (4,'dynamic_vehicle',True),(4,'rail_manager',0x300000),
                (4,'source_chain','foreign'),(4,'links',[0,0]),
                (4,'rail_node',0x180000),(2,'links',[0,0x180060]),(3,'links',[0,0x180000]),
                (0,'velocity',[0.,0.,-521.7738647460938]),(1,'velocity',[0.,0.,500.]),
                (0,'position',[90300.,909.8189,50474.43359]),
                (1,'previous_position',[89925.5625,909.8189,50489.]),
                (1,'position',[89928.2,909.8189,50488.69921875]),
                (4,'position',[89926.,909.8179,51103.59375]),
                (0,'velocity',[0.,0.,math.nan])):
            rows=list(projection_evidence());rows[index]=dict(rows[index],**{key:value})
            with self.subTest(index=index,key=key,value=value):
                self.assertFalse(proves_native_projection(*rows,0x884ef81b))
        # An exact projection still cannot borrow this marker for an over-cap jump.
        old,new,before,after,following=projection_evidence()
        old=dict(old,position=[90100.,909.8189,50474.43359],previous_position=[90097.5,909.8189,50474.43359])
        z=after['position'][2]+math.dist(old['position'],before['position'])-math.dist(before['position'],after['position'])+old['velocity'][2]/60
        new=dict(new,position=[89928.0625,909.8189,z],previous_position=[89925.5625,909.8189,z])
        self.assertFalse(proves_native_projection(old,new,before,after,following,0x884ef81b))

    def test_native_projection_verifies_pins_clock_owner_and_retained_native_history(self):
        _,sample,_,observation,_=projection_evidence()
        reader=RailCorrection.__new__(RailCorrection)
        actor,core=0x300000,0x310000
        reader.context=dict(actor=actor,components={0x5bd63b29:core})
        words={actor:0x6989e8,core:0x6a0d58,core+4:0x5bd63b29,core+12:actor,
               core+0x3f0:observation['rail_node'],core+0x3f4:observation['rail_manager']}
        raw={(0x500000,6):b'pinned',(0x8459d0,8):struct.pack('<Q',sample['frame']),
             (core+0x9c,4):struct.pack('<f',1/60),(actor+0x68,12):struct.pack('<3f',*sample['position']),
             (actor+0x78,12):struct.pack('<3f',*sample['previous_position']),
             (core+0xa8,12):struct.pack('<3f',*sample['previous_position'])}
        reader.system=SimpleNamespace(word=lambda p:words[p],read=lambda p,n:raw[p,n])
        pins=(('fixture',0x500000,6,hashlib.sha256(b'pinned').hexdigest()),)
        with patch.object(correction,'PROJECTION_PINS',pins):
            reader._verify_projection(sample,observation)
            for key,value in (((core+0x9c,4),struct.pack('<f',1/30)),
                              ((0x8459d0,8),struct.pack('<Q',sample['frame']+1)),
                              ((actor+0x78,12),bytes(12)),((core+0xa8,12),bytes(12)),
                              ((0x500000,6),b'forged')):
                original=raw[key];raw[key]=value
                with self.assertRaises(RuntimeError):reader._verify_projection(sample,observation)
                raw[key]=original
            words[core+12]=0x400000
            with self.assertRaisesRegex(RuntimeError,'ownership'):reader._verify_projection(sample,observation)

    def test_projection_fallback_verifies_original_history_once_after_owned_neighbor(self):
        old,new,before,after,following=projection_evidence();current=[before]
        def read(pointer,size):
            node=next(row for row in (before,after,following) if row['rail_node']==pointer)
            data=bytearray(size)
            struct.pack_into('<3f',data,0x20,*node['position'])
            struct.pack_into('<I',data,0x44,node['rail_name'])
            struct.pack_into('<2I',data,0x50,*node['links'])
            return bytes(data)
        actor=0x300000
        system=SimpleNamespace(word=lambda p:0x6989e8 if p==actor else 0,read=read)
        rails=SimpleNamespace(observe=lambda sample:current[0],log=Mock(),
                              adjacent_node=Mock(return_value=following))
        reader=RailCorrection(rails,system,dict(actor=actor))
        with patch.object(reader,'_verify_projection') as verify:
            self.assertFalse(reader.update(old));verify.assert_not_called()
            current[0]=after
            self.assertTrue(reader.update(new));self.assertTrue(reader.update(copy.deepcopy(new)))
            verify.assert_called_once_with(new,reader.observation)
            rails.adjacent_node.assert_called_once_with(reader.observation,following['rail_node'])
            self.assertTrue(rails.log.call_args.kwargs['native_projection'])
            self.assertFalse(rails.log.call_args.kwargs['next_segment'])
            self.assertEqual(rails.log.call_count,1)
        old=dict(old,walking_gate=1)
        with self.assertRaises(ValueError):proves_native_projection(old,new,before,after,following,0x884ef81b)

    def test_recorded_corner_uses_next_segment_without_widening_straight_guard(self):
        rows=corner_evidence()
        self.assertFalse(proves_correction(*rows[:4],0x884ef81b))
        self.assertTrue(proves_corner_correction(*rows,0x884ef81b))

    def test_reverse_transition_keeps_the_existing_new_segment_projection(self):
        old,new,before,after,following=corner_evidence()
        # In reverse, the new Core node denotes the segment ending at the old
        # node. The existing projection already checks that actual new segment.
        new=dict(new,position=[76855.,748.9552,46535.])
        self.assertTrue(proves_correction(old,new,after,before,0x884ef81b))
        self.assertFalse(proves_corner_correction(old,new,after,before,following,0x884ef81b))

    def test_corner_requires_owned_reciprocal_continuation_and_bail(self):
        for index,key,value in ((0,'bailing',False),(1,'bailing',False),
                (0,'board_visible',True),(1,'detached_board_id',1),(1,'frame',2519),
                (4,'dynamic_vehicle',True),(4,'rail_manager',0x300000),
                (4,'source_chain','foreign'),(4,'links',[0,0]),
                (4,'rail_node',0x180000),(2,'links',[0,0x180060]),(3,'links',[0,0x180000])):
            rows=list(corner_evidence());rows[index]=dict(rows[index],**{key:value})
            with self.subTest(index=index,key=key):
                self.assertFalse(proves_corner_correction(*rows,0x884ef81b))

    def test_corner_preserves_distance_height_projection_and_progress_bounds(self):
        for index,point in ((0,[76900.,747.77,46630.]), # Too far off old rail.
                (1,[76887.66,748.95,46540.]), # Not on new rail.
                (1,[76950.,748.95,46515.99]), # Advances too far along new rail.
                (1,[76887.66,760.,46529.68]),(1,[76891.,747.77,46587.69])):
            rows=list(corner_evidence());rows[index]=dict(rows[index],position=point)
            with self.subTest(index=index,point=point):
                self.assertFalse(proves_corner_correction(*rows,0x884ef81b))

    def test_corner_reader_uses_one_verified_neighbor_only_on_large_transition(self):
        old,new,before,after,following=corner_evidence(); current=[before];logs=[]
        actor=0x300000; nodes=(before,after,following)
        def read(pointer,size):
            node=next(row for row in nodes if row['rail_node']==pointer)
            data=bytearray(size)
            struct.pack_into('<3f',data,0x20,*node['position'])
            struct.pack_into('<I',data,0x44,node['rail_name'])
            struct.pack_into('<2I',data,0x50,*node['links'])
            return bytes(data)
        system=SimpleNamespace(word=lambda address:0x6989e8 if address==actor else 0,read=Mock(side_effect=read))
        rails=RailStream.__new__(RailStream);rails.system=system
        rails.groups=[dict(group=before['rail_node'],nodes=3,rail_manager=before['rail_manager'],
            nodes_by_name={n['rail_name']:dict(position_thaw=n['position'],source_chain=n['source_chain']) for n in nodes})]
        rails.observe=Mock(side_effect=lambda sample:current[0]);rails.log=lambda *a,**k:logs.append((a,k))
        rails.adjacent_node=Mock(wraps=rails.adjacent_node)
        reader=RailCorrection(rails,system,dict(actor=actor))
        self.assertFalse(reader.update(old));rails.adjacent_node.assert_not_called()
        current[0]=after
        self.assertTrue(reader.update(new));self.assertTrue(reader.update(copy.deepcopy(new)))
        rails.adjacent_node.assert_called_once_with(reader.observation,following['rail_node'])
        self.assertTrue(logs[0][1]['next_segment'])
        self.assertEqual(len(logs),1)
        # Outside/misaligned pointers fail before reading foreign memory.
        for pointer in (0x170000,0x180001,0x180120):
            system.read.reset_mock()
            with self.assertRaises(RuntimeError):RailStream.adjacent_node(rails,after,pointer)
            system.read.assert_not_called()
        for key,value in (('source_chain','foreign'),('position_thaw',[0,0,0])):
            ref=rails.groups[0]['nodes_by_name'][following['rail_name']]
            saved=ref[key];ref[key]=value
            with self.assertRaises(RuntimeError):RailStream.adjacent_node(rails,after,following['rail_node'])
            ref[key]=saved

    def test_observed_bail_reprojection_passes(self):
        self.assertTrue(proves_correction(*evidence(),0x884ef81b))

    def test_vehicle_local_nodes_never_prove_static_world_reprojection(self):
        for index in (2, 3):
            rows=list(evidence());rows[index]=dict(rows[index],dynamic_vehicle=True)
            self.assertFalse(proves_correction(*rows,0x884ef81b))

    def test_verified_vehicle_bail_clears_static_evidence_without_reading_local_points(self):
        old,new,before,after=evidence()
        actor=0x300000
        system=SimpleNamespace(word=lambda address:0x6989e8 if address==actor else 0,
                               read=Mock(side_effect=AssertionError('vehicle local projection')))
        dynamic=dict(after,dynamic_vehicle=True,vehicle_id=73,vehicle_owner=0x40000)
        rails=SimpleNamespace(observe=Mock(return_value=dynamic),log=Mock())
        reader=RailCorrection(rails,system,dict(actor=actor))
        reader.previous,reader.observation=old,before
        self.assertFalse(reader.update(new))
        self.assertIsNone(reader.observation)
        self.assertFalse(reader.update(copy.deepcopy(new)))
        rails.observe.assert_called_once_with(new)
        system.read.assert_not_called()
        rails.log.assert_not_called()
        rails.observe.side_effect=RuntimeError('unowned dynamic manager')
        with self.assertRaisesRegex(RuntimeError,'unowned dynamic manager'):
            reader.update(dict(new,frame=121))

    def test_no_generic_rail_teleport_or_foreign_detached_board(self):
        for index,key,value in ((0,'state',1),(1,'state',1),(1,'frame',121),
                (0,'board_visible',True),(1,'board_visible',True),
                (0,'detached_board_id',0),(1,'detached_board_id',123),
                (3,'rail_manager',0x300000),(3,'source_chain','foreign'),
                (3,'rail_node',0x180000),(2,'links',[]),(3,'links',[])):
            rows=list(evidence());rows[index]=dict(rows[index],**{key:value})
            with self.subTest(index=index,key=key):
                self.assertFalse(proves_correction(*rows,0x884ef81b))

    def test_source_geometry_and_adjustment_limits(self):
        for index,point in ((0,[90300.,909.82,48586.24]),
                (1,[89940.,909.82,48570.67]),(1,[89931.,930.,48570.67]),
                (1,[89931.,909.82,48400.]),(1,[90003.,909.82,48580.])):
            rows=list(evidence());rows[index]=dict(rows[index],position=point)
            with self.subTest(index=index,point=point):
                self.assertFalse(proves_correction(*rows,0x884ef81b))

    def test_production_reader_uses_native_ownership_and_stable_completed_frames(self):
        old,new,before,after=evidence(); logs=[]; current=[before]
        def read(pointer,size):
            node=next(row for row in (before,after) if row['rail_node']==pointer)
            data=bytearray(size)
            struct.pack_into('<3f',data,0x20,*node['position'])
            struct.pack_into('<I',data,0x44,node['rail_name'])
            struct.pack_into('<2I',data,0x50,*node['links'])
            return bytes(data)
        actor=0x300000
        system=SimpleNamespace(word=lambda address:0x6989e8 if address==actor else 0,read=read)
        rails=SimpleNamespace(observe=lambda sample:current[0],log=lambda *a,**k:logs.append((a,k)))
        reader=RailCorrection(rails,system,dict(actor=actor))
        self.assertFalse(reader.update(old));current[0]=after
        self.assertTrue(reader.update(new));self.assertTrue(reader.update(copy.deepcopy(new)))
        self.assertEqual(len(logs),1)
        with self.assertRaises(RuntimeError):reader.update(dict(new,position=[0,0,0]))
        self.assertFalse(reader.update(dict(new,frame=121,state=1)))


if __name__=='__main__':unittest.main()
